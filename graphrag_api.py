import json
import os
import queue
import re
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Tuple
from neo4j import GraphDatabase
import uvicorn
from kg_tools import get_last_trace
from ingest_service import ingest_csv as ingest_csv_file, ingest_pdf, ingest_text

from pathlib import Path

# ===== Configuration (env-first, defaults for local dev) =====
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "88888888")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
# =============================================================

# Keep env available for the agent initialization.
if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
if OPENAI_API_BASE:
    os.environ["OPENAI_API_BASE"] = OPENAI_API_BASE
os.environ["NEO4J_URI"] = NEO4J_URI
os.environ["NEO4J_USER"] = NEO4J_USER
os.environ["NEO4J_PASSWORD"] = NEO4J_PASSWORD
os.environ["LLM_MODEL"] = LLM_MODEL

agent_executor = None
SYSTEM_PROMPT = None
neo4j_driver = None
conversation_store: Dict[str, List[Tuple[str, str]]] = {}
MAX_TURNS = int(os.getenv("MEMORY_MAX_TURNS", "12"))
API_VERSION = "1.0.0"
DOC_MODES = {"template", "llm", "hybrid", "llm_rel"}
UPDATE_MODES = {"safe", "overwrite"}
GRAPH_VIEW_MODES = {"focus", "full", "rca_evidence"}
API_TAGS = [
    {
        "name": "system",
        "description": "Service discovery, health check, and external integration metadata.",
    },
    {
        "name": "chat",
        "description": "GraphRAG question answering and streaming response interfaces.",
    },
    {
        "name": "graph",
        "description": "Graph visualization data for focused, RCA evidence, or full knowledge graph views.",
    },
    {
        "name": "rca",
        "description": "Structured root-cause analysis responses with evidence graph metadata.",
    },
    {
        "name": "ingestion",
        "description": "CSV, PDF, and text ingestion endpoints for knowledge graph updates.",
    },
]
API_ENDPOINT_CATALOG: List[Dict[str, Any]] = [
    {
        "method": "GET",
        "path": "/api/health",
        "summary": "Service health check",
        "content_type": "application/json",
        "description": "Check whether Neo4j and the chat agent are ready for external traffic.",
        "example": None,
    },
    {
        "method": "GET",
        "path": "/api/catalog",
        "summary": "Public API catalog",
        "content_type": "application/json",
        "description": "Return the endpoint list, parameter style, and example payloads.",
        "example": None,
    },
    {
        "method": "POST",
        "path": "/api/chat",
        "summary": "GraphRAG Q&A",
        "content_type": "application/json",
        "description": "Ask a supply-chain question and receive a final answer plus query trace.",
        "example": {
            "message": "请分析中芯国际断供对订单的影响",
            "conversation_id": "demo-001",
        },
    },
    {
        "method": "POST",
        "path": "/api/chat/stream",
        "summary": "GraphRAG streaming Q&A",
        "content_type": "text/event-stream",
        "description": "Use Server-Sent Events and consume status, delta, done, and error events.",
        "example": {
            "message": "请给我一个供应商风险摘要",
            "conversation_id": "demo-stream-001",
        },
    },
    {
        "method": "POST",
        "path": "/api/rca",
        "summary": "Structured RCA response",
        "content_type": "application/json",
        "description": "Ask an RCA-style question and receive a structured root-cause payload with graph evidence.",
        "example": {
            "message": "为什么 ORD-2024-100001 延迟？",
            "conversation_id": "demo-rca-001",
        },
    },
    {
        "method": "POST",
        "path": "/api/graph_view",
        "summary": "Graph visualization data",
        "content_type": "application/json",
        "description": "Return nodes and edges for a focused subgraph, an RCA evidence subgraph, or a full graph sample.",
        "example": {
            "question": "展示和中芯国际相关的图谱",
            "mode": "rca_evidence",
            "max_nodes": 80,
            "max_edges": 160,
        },
    },
    {
        "method": "POST",
        "path": "/api/ingest/csv",
        "summary": "CSV incremental ingestion",
        "content_type": "multipart/form-data",
        "description": "Upload a structured CSV file and upsert it into Neo4j.",
        "example": {
            "file": "Supply_Chain_Data_Fake.csv",
            "batch_size": 2000,
            "update_mode": "safe",
        },
    },
    {
        "method": "POST",
        "path": "/api/ingest/pdf",
        "summary": "PDF ingestion",
        "content_type": "multipart/form-data",
        "description": "Upload a PDF and extract entities/relations by template, LLM, hybrid, or graph mode.",
        "example": {
            "file": "report.pdf",
            "mode": "hybrid",
            "batch_size": 2000,
            "update_mode": "safe",
        },
    },
    {
        "method": "POST",
        "path": "/api/ingest/text",
        "summary": "Text ingestion",
        "content_type": "application/json",
        "description": "Submit natural language or structured text to supplement the knowledge graph.",
        "example": {
            "text": "供应商：中芯国际；组件：存储控制器；缺陷率：0.02",
            "mode": "hybrid",
            "batch_size": 2000,
            "update_mode": "safe",
        },
    },
]


def _trim_history(history: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    if len(history) > MAX_TURNS * 2:
        return history[-MAX_TURNS * 2 :]
    return history


def _store_conversation_reply(
    conversation_id: str, user_message: str, reply: str
) -> List[Tuple[str, str]]:
    history = conversation_store.get(conversation_id, [])
    history = history + [("user", user_message), ("assistant", reply)]
    history = _trim_history(history)
    conversation_store[conversation_id] = history
    return history


def _history_to_text(conversation_id: str | None, max_turns: int = 6) -> str:
    if not conversation_id:
        return ""
    history = conversation_store.get(conversation_id, [])
    if not history:
        return ""
    turns = history[-max_turns * 2 :]
    return "\n".join(f"{role}: {content}" for role, content in turns)


def _sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _check_neo4j() -> None:
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        driver.verify_connectivity()
        driver.close()
    except Exception as e:
        raise RuntimeError(
            "无法连接 Neo4j，请确认数据库已启动，并检查端口、账号、密码。"
        ) from e


def _ensure_indexes(driver: Any) -> None:
    statements = [
        "CREATE INDEX customer_id_index IF NOT EXISTS FOR (n:Customer) ON (n.id)",
        "CREATE INDEX order_id_index IF NOT EXISTS FOR (n:Order) ON (n.id)",
        "CREATE INDEX product_id_index IF NOT EXISTS FOR (n:Product) ON (n.id)",
        "CREATE INDEX supplier_name_index IF NOT EXISTS FOR (n:Supplier) ON (n.name)",
        "CREATE INDEX component_name_index IF NOT EXISTS FOR (n:Component) ON (n.name)",
        "CREATE INDEX carrier_name_index IF NOT EXISTS FOR (n:Carrier) ON (n.name)",
        "CREATE INDEX category_name_index IF NOT EXISTS FOR (n:Category) ON (n.name)",
        "CREATE INDEX department_name_index IF NOT EXISTS FOR (n:Department) ON (n.name)",
    ]
    with driver.session() as session:
        for statement in statements:
            session.run(statement).consume()


def _normalize_doc_mode(mode: str | None) -> str:
    normalized = (mode or "hybrid").strip().lower()
    return normalized if normalized in DOC_MODES else "hybrid"


def _normalize_update_mode(update_mode: str | None) -> str:
    normalized = (update_mode or "safe").strip().lower()
    return normalized if normalized in UPDATE_MODES else "safe"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent_executor
    global SYSTEM_PROMPT
    global neo4j_driver
    _check_neo4j()
    neo4j_driver = GraphDatabase.driver(
        NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
    )
    _ensure_indexes(neo4j_driver)
    from supplychain_agent import build_agent as _build_agent, SYSTEM_PROMPT as _SYSTEM_PROMPT
    agent_executor = _build_agent()
    SYSTEM_PROMPT = _SYSTEM_PROMPT
    yield
    if neo4j_driver:
        neo4j_driver.close()


app = FastAPI(
    title="SupplyChain GraphRAG API",
    description=(
        "Unified external API for supply-chain GraphRAG analysis, graph visualization, "
        "and CSV/PDF/text ingestion. Visit `/docs` for Swagger UI or `/api/catalog` for "
        "an integration-oriented endpoint index."
    ),
    version=API_VERSION,
    openapi_tags=API_TAGS,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str = Field(..., description="User question or analysis request.")
    conversation_id: str | None = Field(
        default=None,
        description="Conversation identifier for keeping chat context.",
    )


class TextIngestRequest(BaseModel):
    text: str = Field(..., description="Natural language or structured text to ingest.")
    mode: str | None = Field(
        default="hybrid",
        description="Extraction mode: template, llm, hybrid, or llm_rel.",
    )
    batch_size: int | None = Field(
        default=2000,
        description="Batch size for Neo4j writes.",
        ge=1,
    )
    update_mode: str | None = Field(
        default="safe",
        description="Write strategy: safe or overwrite.",
    )


class GraphViewRequest(BaseModel):
    question: str | None = Field(
        default="",
        description="Natural language focus prompt used to locate relevant graph nodes.",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation identifier used to resolve graph targets from prior chat context.",
    )
    mode: str | None = Field(
        default="focus",
        description="focus returns a relevant subgraph; rca_evidence returns an RCA evidence subgraph; full returns a sampled full graph.",
    )
    max_nodes: int | None = Field(default=None, ge=1, description="Maximum number of nodes.")
    max_edges: int | None = Field(default=None, ge=1, description="Maximum number of edges.")


class ApiEndpointInfo(BaseModel):
    method: str
    path: str
    summary: str
    content_type: str
    description: str
    example: Any | None = None


class ServiceIndexResponse(BaseModel):
    service: str
    version: str
    docs: str
    redoc: str
    openapi: str
    endpoints: List[ApiEndpointInfo]


class HealthComponent(BaseModel):
    status: str
    detail: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    status: str
    components: Dict[str, HealthComponent]


class ChatResponse(BaseModel):
    reply: str
    conversation_id: str
    trace: Dict[str, Any] | None = None
    rca: Dict[str, Any] | None = None


class RCAResponse(BaseModel):
    reply: str
    conversation_id: str
    trace: Dict[str, Any] | None = None
    rca: Dict[str, Any]


class GraphViewResponse(BaseModel):
    ok: bool
    graph: Dict[str, Any]


class IngestStrategy(BaseModel):
    source_type: str
    extract_mode: str | None = None
    update_mode: str


class IngestResponse(BaseModel):
    ok: bool
    strategy: IngestStrategy
    rows: int
    batches: int
    input_rows: int | None = None
    valid_rows: int | None = None
    skipped_rows: int | None = None
    relation_counts: Dict[str, int] | None = None
    error_examples: List[Dict[str, Any]] | None = None


def _extract_keywords(question: str, max_k: int = 8) -> List[str]:
    q = (question or "").strip().lower()
    if not q:
        return []
    raw_tokens = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_-]{2,}", q)
    stopwords = {
        "什么",
        "哪些",
        "多少",
        "如何",
        "分析",
        "请问",
        "一下",
        "情况",
        "问题",
        "the",
        "what",
        "which",
        "show",
        "with",
        "from",
        "that",
    }
    tokens: List[str] = []
    for t in raw_tokens:
        if t in stopwords:
            continue
        if t not in tokens:
            tokens.append(t)
        if len(tokens) >= max_k:
            break
    return tokens


def _extract_order_id(question: str) -> str:
    match = re.search(r"\bORD-\d{4}-\d+\b", question or "", re.I)
    return match.group(0) if match else ""


def _contains_any(text: str, keywords: List[str]) -> bool:
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _extract_quoted(text: str) -> str:
    match = re.search(r"[\"'“”‘’]([^\"'“”‘’]{2,60})[\"'“”‘’]", text or "")
    return match.group(1).strip() if match else ""


def _clean_entity(candidate: str) -> str:
    candidate = re.split(r"[，。,；;？?\n]", candidate or "")[0].strip(" :：")
    return re.sub(r"\s+", " ", candidate).strip()


def _extract_named_entity(text: str, labels: List[str]) -> str:
    quoted = _extract_quoted(text)
    if quoted:
        return _clean_entity(quoted)
    joined = "|".join(re.escape(label) for label in labels)
    patterns = [
        rf"(?:{joined})\s*(?:is|named|name is|名称是|叫|为)?\s*[:：]?\s*([A-Za-z0-9\u4e00-\u9fff()（）._\-\s]{{2,60}})",
        rf"([A-Za-z0-9\u4e00-\u9fff()（）._\-\s]{{2,60}})\s*(?:{joined})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "", re.I)
        if not match:
            continue
        candidate = _clean_entity(match.group(1))
        if candidate and len(candidate) >= 2:
            return candidate
    return ""


def _contains_rca_language(question: str) -> bool:
    return _contains_any(
        question,
        ["为什么", "原因", "根因", "异常", "风险高", "延迟", "瓶颈", "why", "root cause", "rca"],
    )


def _infer_graph_target(question: str) -> Dict[str, str]:
    question = (question or "").strip()
    order_id = _extract_order_id(question)
    if order_id:
        return {"target_type": "Order", "target_id": order_id}
    if _contains_any(question, ["供应商", "supplier"]):
        supplier = _extract_named_entity(question, ["供应商", "supplier"])
        if supplier:
            return {"target_type": "Supplier", "target_id": supplier}
    if _contains_any(question, ["承运商", "物流", "carrier"]):
        carrier = _extract_named_entity(question, ["承运商", "物流", "carrier"])
        if carrier:
            return {"target_type": "Carrier", "target_id": carrier}
    if _contains_any(question, ["产品", "商品", "product"]):
        product = _extract_named_entity(question, ["产品", "商品", "product"])
        if product:
            return {"target_type": "Product", "target_id": product}
    return {"target_type": "", "target_id": ""}
def _serialize_graph_record(record: Any) -> Dict[str, Any]:
    if not record:
        return {"nodes": [], "edges": [], "meta": {"node_count": 0, "edge_count": 0}}
    nodes = record.get("nodes") or []
    edges = record.get("edges") or []
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }


def _limit_graph_payload(
    graph_data: Dict[str, Any], max_nodes: int, max_edges: int
) -> Dict[str, Any]:
    limited = {
        "nodes": list(graph_data.get("nodes") or []),
        "edges": list(graph_data.get("edges") or []),
        "meta": dict(graph_data.get("meta") or {}),
    }
    truncated = False
    if len(limited["nodes"]) > max_nodes:
        limited["nodes"] = limited["nodes"][:max_nodes]
        truncated = True
    node_ids = {str(node.get("id")) for node in limited["nodes"]}
    filtered_edges = [
        edge
        for edge in limited["edges"]
        if str(edge.get("source")) in node_ids and str(edge.get("target")) in node_ids
    ]
    if len(filtered_edges) > max_edges:
        filtered_edges = filtered_edges[:max_edges]
        truncated = True
    elif len(filtered_edges) != len(limited["edges"]):
        truncated = True
    limited["edges"] = filtered_edges
    limited["meta"]["node_count"] = len(limited["nodes"])
    limited["meta"]["edge_count"] = len(limited["edges"])
    limited["meta"]["truncated"] = truncated
    return limited


def _query_focus_graph(
    driver: Any, question: str, max_nodes: int = 80, max_edges: int = 160
) -> Dict[str, Any]:
    keywords = _extract_keywords(question)
    with driver.session() as session:
        if keywords:
            cypher = """
            MATCH (n)
            WHERE any(k IN $keywords WHERE any(p IN keys(n)
                  WHERE n[p] IS NOT NULL AND toLower(toString(n[p])) CONTAINS k))
            WITH collect(DISTINCT n)[0..$seed_limit] AS seeds
            UNWIND seeds AS s
            OPTIONAL MATCH (s)-[]-(m)
            WITH collect(DISTINCT s) + collect(DISTINCT m) AS raw_nodes
            UNWIND raw_nodes AS n
            WITH collect(DISTINCT n)[0..$max_nodes] AS nodes
            UNWIND nodes AS a
            OPTIONAL MATCH (a)-[r]->(b)
            WHERE b IN nodes
            WITH nodes, collect(DISTINCT r)[0..$max_edges] AS rels
            RETURN
              [n IN nodes | {id: elementId(n), labels: labels(n), props: properties(n)}] AS nodes,
              [r IN rels | {id: elementId(r), source: elementId(startNode(r)), target: elementId(endNode(r)), type: type(r), props: properties(r)}] AS edges
            """
            record = session.run(
                cypher,
                keywords=keywords,
                seed_limit=max(10, min(max_nodes // 2, 120)),
                max_nodes=max_nodes,
                max_edges=max_edges,
            ).single()
            graph_data = _serialize_graph_record(record)
            if graph_data["meta"]["node_count"] > 0:
                graph_data["meta"]["mode"] = "focus"
                graph_data["meta"]["keywords"] = keywords
                return graph_data

        fallback = """
        MATCH (n)
        WITH collect(n)[0..$max_nodes] AS nodes
        UNWIND nodes AS a
        OPTIONAL MATCH (a)-[r]->(b)
        WHERE b IN nodes
        WITH nodes, collect(DISTINCT r)[0..$max_edges] AS rels
        RETURN
          [n IN nodes | {id: elementId(n), labels: labels(n), props: properties(n)}] AS nodes,
          [r IN rels | {id: elementId(r), source: elementId(startNode(r)), target: elementId(endNode(r)), type: type(r), props: properties(r)}] AS edges
        """
        record = session.run(
            fallback,
            max_nodes=max_nodes,
            max_edges=max_edges,
        ).single()
        graph_data = _serialize_graph_record(record)
        graph_data["meta"]["mode"] = "focus"
        graph_data["meta"]["keywords"] = keywords
        graph_data["meta"]["fallback"] = True
        return graph_data


def _run_subgraph_query(
    driver: Any,
    match_clause: str,
    relation_types: str,
    hop_limit: int,
    target_id: str,
    max_nodes: int,
    max_edges: int,
    meta: Dict[str, Any],
) -> Dict[str, Any]:
    cypher = f"""
    {match_clause}
    CALL {{
        WITH seed
        OPTIONAL MATCH p=(seed)-[:{relation_types}*1..{hop_limit}]-(n)
        RETURN [path IN collect(p) WHERE path IS NOT NULL][0..120] AS paths
    }}
    WITH
        [seed] + reduce(node_acc = [], path IN paths | node_acc + nodes(path)) AS raw_nodes,
        reduce(rel_acc = [], path IN paths | rel_acc + relationships(path)) AS raw_rels
    UNWIND raw_nodes AS n
    WITH collect(DISTINCT n) AS nodes, raw_rels
    UNWIND raw_rels AS r
    WITH nodes[0..$max_nodes] AS selected_nodes, collect(DISTINCT r) AS rels
    RETURN
      [n IN selected_nodes | {{id: elementId(n), labels: labels(n), props: properties(n)}}] AS nodes,
      [r IN rels WHERE r IS NOT NULL AND startNode(r) IN selected_nodes AND endNode(r) IN selected_nodes
        | {{id: elementId(r), source: elementId(startNode(r)), target: elementId(endNode(r)), type: type(r), props: properties(r)}}][0..$max_edges] AS edges
    """
    with driver.session() as session:
        record = session.run(
            cypher,
            target_id=target_id,
            max_nodes=max_nodes,
            max_edges=max_edges,
        ).single()
    graph_data = _serialize_graph_record(record)
    graph_data["meta"].update(meta)
    return graph_data


def _query_rca_evidence_graph(
    driver: Any, question: str, max_nodes: int = 80, max_edges: int = 160
) -> Dict[str, Any]:
    target = _infer_graph_target(question)
    target_type = target.get("target_type", "")
    target_id = target.get("target_id", "")
    if not target_type or not target_id:
        graph_data = _query_focus_graph(
            driver,
            question=question,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        graph_data["meta"]["mode"] = "rca_evidence"
        graph_data["meta"]["fallback"] = True
        graph_data["meta"]["notice"] = (
            "No explicit RCA target was found in the question. Returned a keyword-based focus subgraph."
        )
        return graph_data

    configs = {
        "Order": {
            "match_clause": "MATCH (seed:Order {id: $target_id})",
            "relation_types": "PLACED_ORDER|CONTAINS_PRODUCT|SHIPPED_BY|USED_IN|SUPPLIES_COMPONENT",
            "hop_limit": 3,
        },
        "Supplier": {
            "match_clause": "MATCH (seed:Supplier) WHERE seed.name CONTAINS $target_id",
            "relation_types": "SUPPLIES_COMPONENT|USED_IN|CONTAINS_PRODUCT|SHIPPED_BY",
            "hop_limit": 4,
        },
        "Carrier": {
            "match_clause": "MATCH (seed:Carrier) WHERE seed.name CONTAINS $target_id",
            "relation_types": "SHIPPED_BY|CONTAINS_PRODUCT|USED_IN|SUPPLIES_COMPONENT|PLACED_ORDER",
            "hop_limit": 4,
        },
        "Product": {
            "match_clause": "MATCH (seed:Product) WHERE seed.name CONTAINS $target_id",
            "relation_types": "CONTAINS_PRODUCT|USED_IN|SUPPLIES_COMPONENT|SHIPPED_BY|BELONGS_TO_CATEGORY|BELONGS_TO_DEPARTMENT",
            "hop_limit": 3,
        },
    }
    config = configs[target_type]
    return _run_subgraph_query(
        driver=driver,
        match_clause=config["match_clause"],
        relation_types=config["relation_types"],
        hop_limit=config["hop_limit"],
        target_id=target_id,
        max_nodes=max_nodes,
        max_edges=max_edges,
        meta={
            "mode": "rca_evidence",
            "target_type": target_type,
            "target_id": target_id,
            "question": question,
            "notice": "RCA evidence view prioritizes the local evidence chain instead of the whole graph.",
        },
    )


def _query_rca_engine_graph(
    question: str,
    conversation_id: str | None,
    max_nodes: int,
    max_edges: int,
) -> Dict[str, Any]:
    if agent_executor is None or not hasattr(agent_executor, "rca_engine"):
        return {}

    history_text = _history_to_text(conversation_id)
    intent = agent_executor.rca_engine.detect_intent(
        question or "",
        history_text=history_text,
    )
    if intent.route_type != "rca":
        return {}

    result = agent_executor.rca_engine.run(
        question or "",
        history_text=history_text,
        intent=intent,
        render_reply=False,
    )
    structured = result.get("rca") or {}
    graph_data = structured.get("evidence_graph") or {}
    if not graph_data:
        return {}

    limited = _limit_graph_payload(graph_data, max_nodes=max_nodes, max_edges=max_edges)
    limited["meta"]["mode"] = "rca_evidence"
    limited["meta"]["source"] = "rca_engine"
    limited["meta"]["question"] = question
    limited["meta"]["conversation_id"] = conversation_id
    limited["meta"]["structured_rca"] = True
    limited["meta"]["route"] = structured.get("route", {})
    limited["meta"]["incident_summary"] = structured.get("incident_summary", {})
    limited["meta"]["risk_signals"] = structured.get("risk_signals", [])
    limited["meta"]["validation"] = structured.get("validation", {})
    limited["meta"]["graph_metrics"] = structured.get("graph_metrics", {})
    limited["meta"]["investigation"] = structured.get("investigation", {})
    limited["meta"]["investigation_steps"] = structured.get("investigation_steps", [])
    limited["meta"]["candidate_causes"] = [
        {
            "cause": item.get("cause"),
            "label": item.get("label"),
            "score": item.get("score"),
            "evidence": item.get("evidence") or [],
            "explanation_hint": item.get("explanation_hint", ""),
            "supporting_nodes": item.get("supporting_nodes") or [],
            "supporting_edges": item.get("supporting_edges") or [],
            "evidence_chain": item.get("evidence_chain") or [],
            "source_records": item.get("source_records") or [],
            "evidence_mode": item.get("evidence_mode", "structural"),
        }
        for item in (structured.get("candidate_causes") or [])[:3]
    ]
    limited["meta"]["recommended_actions"] = list(structured.get("recommended_actions") or [])[:4]
    limited["meta"]["target_type"] = intent.target_type
    limited["meta"]["target_id"] = intent.target_id
    return limited


def _query_full_graph(
    driver: Any, max_nodes: int = 400, max_edges: int = 1000
) -> Dict[str, Any]:
    with driver.session() as session:
        count_row = session.run(
            "MATCH (n) WITH count(n) AS total_nodes MATCH ()-[r]->() RETURN total_nodes, count(r) AS total_edges"
        ).single()
        label_rows = session.run(
            "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY count DESC LIMIT 12"
        ).data()
        rel_rows = session.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC LIMIT 12"
        ).data()
        cypher = """
        MATCH (n)
        WITH collect(n)[0..$max_nodes] AS nodes
        UNWIND nodes AS a
        OPTIONAL MATCH (a)-[r]->(b)
        WHERE b IN nodes
        WITH nodes, collect(DISTINCT r)[0..$max_edges] AS rels
        RETURN
          [n IN nodes | {id: elementId(n), labels: labels(n), props: properties(n)}] AS nodes,
          [r IN rels | {id: elementId(r), source: elementId(startNode(r)), target: elementId(endNode(r)), type: type(r), props: properties(r)}] AS edges
        """
        record = session.run(
            cypher,
            max_nodes=max_nodes,
            max_edges=max_edges,
        ).single()
        graph_data = _serialize_graph_record(record)
        graph_data["meta"]["mode"] = "full"
        graph_data["meta"]["total_nodes"] = int(count_row.get("total_nodes", 0)) if count_row else 0
        graph_data["meta"]["total_edges"] = int(count_row.get("total_edges", 0)) if count_row else 0
        graph_data["meta"]["truncated"] = (
            graph_data["meta"]["node_count"] < graph_data["meta"]["total_nodes"]
            or graph_data["meta"]["edge_count"] < graph_data["meta"]["total_edges"]
        )
        graph_data["meta"]["label_distribution"] = label_rows
        graph_data["meta"]["relationship_distribution"] = rel_rows
        graph_data["meta"]["notice"] = (
            "Full graph is intended for global overview only. Use mode=rca_evidence for root-cause analysis subgraphs."
        )
        return graph_data


@app.get("/", tags=["system"], summary="Service index", response_model=ServiceIndexResponse)
def service_index():
    return {
        "service": "SupplyChain GraphRAG API",
        "version": API_VERSION,
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
        "endpoints": API_ENDPOINT_CATALOG,
    }


@app.get(
    "/api/health",
    tags=["system"],
    summary="Health check",
    response_model=HealthResponse,
)
def health_check():
    components: Dict[str, Dict[str, Any]] = {
        "neo4j": {"status": "down", "detail": "Neo4j driver is not ready."},
        "agent": {"status": "down", "detail": "GraphRAG agent is not ready."},
    }

    if neo4j_driver is not None:
        try:
            with neo4j_driver.session() as session:
                session.run("RETURN 1 AS ok").single()
            components["neo4j"] = {"status": "up", "detail": "Neo4j connection is ready."}
        except Exception as exc:
            components["neo4j"] = {"status": "down", "detail": str(exc)}

    if agent_executor is not None and SYSTEM_PROMPT:
        components["agent"] = {"status": "up", "detail": "GraphRAG chat agent is ready."}

    ok = all(component["status"] == "up" for component in components.values())
    return {
        "ok": ok,
        "status": "up" if ok else "degraded",
        "components": components,
    }


@app.get(
    "/api/catalog",
    tags=["system"],
    summary="Public API catalog",
    response_model=ServiceIndexResponse,
)
def api_catalog():
    return {
        "service": "SupplyChain GraphRAG API",
        "version": API_VERSION,
        "docs": "/docs",
        "redoc": "/redoc",
        "openapi": "/openapi.json",
        "endpoints": API_ENDPOINT_CATALOG,
    }


@app.post(
    "/api/chat",
    tags=["chat"],
    summary="GraphRAG + LangChain agent",
    response_model=ChatResponse,
)
def chat_with_kg(request: ChatRequest):
    conversation_id = request.conversation_id or "default"
    history = conversation_store.get(conversation_id, [])

    messages = [("system", SYSTEM_PROMPT)] + history + [("user", request.message)]
    response = agent_executor.invoke({"messages": messages})

    reply = response["messages"][-1].content
    _store_conversation_reply(conversation_id, request.message, reply)
    return {
        "reply": reply,
        "conversation_id": conversation_id,
        "trace": response.get("trace") or get_last_trace(),
        "rca": response.get("rca"),
    }


@app.post(
    "/api/chat/stream",
    tags=["chat"],
    summary="GraphRAG streaming chat",
    description=(
        "Server-Sent Events endpoint. Event types include `status`, `delta`, `done`, and `error`."
    ),
)
def chat_with_kg_stream(request: ChatRequest):
    conversation_id = request.conversation_id or "default"
    history = conversation_store.get(conversation_id, [])
    messages = [("system", SYSTEM_PROMPT)] + history + [("user", request.message)]
    event_queue: "queue.Queue[Tuple[str, Dict[str, Any]] | None]" = queue.Queue()
    reply_stream_started = False
    post_reply_chunking = False

    def emit(event: str, payload: Dict[str, Any]) -> None:
        nonlocal post_reply_chunking
        nonlocal reply_stream_started
        if event == "delta" and payload.get("text"):
            if post_reply_chunking and reply_stream_started:
                return
            if not post_reply_chunking:
                reply_stream_started = True
        event_queue.put((event, payload))

    def worker() -> None:
        nonlocal post_reply_chunking
        try:
            response = agent_executor.invoke(
                {"messages": messages}, progress_callback=emit
            )
            reply = response["messages"][-1].content
            trace = response.get("trace") or get_last_trace()
            rca = response.get("rca")
            _store_conversation_reply(conversation_id, request.message, reply)
            post_reply_chunking = True

            chunks = [chunk for chunk in re.split(r"(\n\n|[。！？!?])", reply) if chunk]
            buffer = ""
            for chunk in chunks:
                buffer += chunk
                if chunk in {"\n\n", "。", "！", "？", "!", "?"} or len(buffer) >= 48:
                    emit("delta", {"text": buffer})
                    buffer = ""
            if buffer:
                emit("delta", {"text": buffer})

            emit(
                "done",
                {
                    "reply": reply,
                    "conversation_id": conversation_id,
                    "trace": trace,
                    "rca": rca,
                },
            )
        except Exception as exc:
            emit("error", {"message": str(exc)})
        finally:
            event_queue.put(None)

    def stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        yield _sse("status", {"stage": "start", "message": "连接已建立，开始处理"})
        while True:
            item = event_queue.get()
            if item is None:
                break
            event, payload = item
            yield _sse(event, payload)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post(
    "/api/rca",
    tags=["rca"],
    summary="Structured root-cause analysis",
    response_model=RCAResponse,
)
def analyze_root_cause(request: ChatRequest):
    conversation_id = request.conversation_id or "default"
    history = conversation_store.get(conversation_id, [])
    messages = [("system", SYSTEM_PROMPT)] + history + [("user", request.message)]
    response = agent_executor.invoke({"messages": messages})
    reply = response["messages"][-1].content
    rca = response.get("rca")
    if not rca:
        raise HTTPException(
            status_code=422,
            detail="Question was not classified into the RCA pipeline. Use /api/chat for general GraphRAG queries.",
        )
    _store_conversation_reply(conversation_id, request.message, reply)
    return {
        "reply": reply,
        "conversation_id": conversation_id,
        "trace": response.get("trace") or get_last_trace(),
        "rca": rca,
    }


@app.post(
    "/api/graph_view",
    tags=["graph"],
    summary="Get graph visualization data",
    response_model=GraphViewResponse,
)
def graph_view(request: GraphViewRequest):
    raw_mode = (request.mode or "focus").strip().lower()
    mode = raw_mode if raw_mode in GRAPH_VIEW_MODES else "focus"
    auto_rca = mode == "focus" and _contains_rca_language(request.question or "")
    effective_mode = "rca_evidence" if auto_rca else mode
    max_nodes = request.max_nodes or (80 if effective_mode in {"focus", "rca_evidence"} else 400)
    max_edges = request.max_edges or (160 if effective_mode in {"focus", "rca_evidence"} else 1000)
    max_nodes = max(10, min(max_nodes, 1500))
    max_edges = max(20, min(max_edges, 5000))

    driver = neo4j_driver
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not ready")
    if effective_mode == "full":
        graph_data = _query_full_graph(driver, max_nodes=max_nodes, max_edges=max_edges)
    elif effective_mode == "rca_evidence":
        graph_data = _query_rca_engine_graph(
            question=request.question or "",
            conversation_id=request.conversation_id,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if not graph_data:
            graph_data = _query_rca_evidence_graph(
                driver,
                question=request.question or "",
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
            graph_data["meta"]["source"] = "graph_query"
    else:
        graph_data = _query_focus_graph(
            driver,
            question=request.question or "",
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
    graph_data["meta"]["requested_mode"] = raw_mode
    graph_data["meta"]["effective_mode"] = effective_mode
    graph_data["meta"]["conversation_id"] = request.conversation_id

    return {"ok": True, "graph": graph_data}


@app.get("/admin", tags=["ingestion"], summary="Admin UI for ingestion")
def admin_ui():
    page = Path(__file__).with_name("admin.html")
    if not page.exists():
        raise HTTPException(status_code=404, detail="admin.html not found")
    return FileResponse(page)


@app.post(
    "/api/ingest/csv",
    tags=["ingestion"],
    summary="Ingest CSV incrementally",
    response_model=IngestResponse,
)
async def ingest_csv_endpoint(
    file: UploadFile = File(..., description="CSV file to ingest into the knowledge graph."),
    batch_size: int = Form(2000),
    update_mode: str = Form("safe"),
):
    try:
        uploads_dir = Path(__file__).with_name("uploads")
        uploads_dir.mkdir(exist_ok=True)
        file_path = uploads_dir / file.filename
        content = await file.read()
        file_path.write_bytes(content)
        effective_update_mode = _normalize_update_mode(update_mode)
        result = ingest_csv_file(
            str(file_path), batch_size=batch_size, update_mode=effective_update_mode
        )
        return {
            "ok": True,
            "strategy": {
                "source_type": "structured_csv",
                "update_mode": effective_update_mode,
            },
            **result,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post(
    "/api/ingest/pdf",
    tags=["ingestion"],
    summary="Ingest PDF with template/LLM hybrid",
    response_model=IngestResponse,
)
async def ingest_pdf_endpoint(
    file: UploadFile = File(..., description="PDF file to extract and ingest."),
    mode: str = Form("hybrid"),
    batch_size: int = Form(2000),
    update_mode: str = Form("safe"),
):
    try:
        uploads_dir = Path(__file__).with_name("uploads")
        uploads_dir.mkdir(exist_ok=True)
        file_path = uploads_dir / file.filename
        content = await file.read()
        file_path.write_bytes(content)
        effective_mode = _normalize_doc_mode(mode)
        effective_update_mode = _normalize_update_mode(update_mode)
        result = ingest_pdf(
            str(file_path),
            mode=effective_mode,
            batch_size=batch_size,
            update_mode=effective_update_mode,
        )
        return {
            "ok": True,
            "strategy": {
                "source_type": "document_supplement",
                "extract_mode": effective_mode,
                "update_mode": effective_update_mode,
            },
            **result,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post(
    "/api/ingest/text",
    tags=["ingestion"],
    summary="Ingest natural language text",
    response_model=IngestResponse,
)
def ingest_text_endpoint(request: TextIngestRequest):
    try:
        text = (request.text or "").strip()
        if not text:
            raise ValueError("text is empty")
        mode = _normalize_doc_mode(request.mode)
        batch_size = request.batch_size or 2000
        update_mode = _normalize_update_mode(request.update_mode)
        result = ingest_text(
            text, mode=mode, batch_size=batch_size, update_mode=update_mode
        )
        return {
            "ok": True,
            "strategy": {
                "source_type": "text_supplement",
                "extract_mode": mode,
                "update_mode": update_mode,
            },
            **result,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


if __name__ == "__main__":
    uvicorn.run("graphrag_api:app", host="127.0.0.1", port=8000, reload=True)
