import json
import os
import queue
import re
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
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


app = FastAPI(title="SupplyChain GraphRAG API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class TextIngestRequest(BaseModel):
    text: str
    mode: str | None = "hybrid"
    batch_size: int | None = 2000
    update_mode: str | None = "safe"


class GraphViewRequest(BaseModel):
    question: str | None = ""
    mode: str | None = "focus"
    max_nodes: int | None = None
    max_edges: int | None = None


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


def _preferred_csv_update_mode() -> str:
    return "safe"


def _preferred_doc_mode() -> str:
    return "hybrid"


def _preferred_doc_update_mode() -> str:
    return "safe"


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
              [n IN nodes | {id: id(n), labels: labels(n), props: properties(n)}] AS nodes,
              [r IN rels | {id: id(r), source: id(startNode(r)), target: id(endNode(r)), type: type(r), props: properties(r)}] AS edges
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
          [n IN nodes | {id: id(n), labels: labels(n), props: properties(n)}] AS nodes,
          [r IN rels | {id: id(r), source: id(startNode(r)), target: id(endNode(r)), type: type(r), props: properties(r)}] AS edges
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


def _query_full_graph(
    driver: Any, max_nodes: int = 400, max_edges: int = 1000
) -> Dict[str, Any]:
    with driver.session() as session:
        count_row = session.run(
            "MATCH (n) WITH count(n) AS total_nodes MATCH ()-[r]->() RETURN total_nodes, count(r) AS total_edges"
        ).single()
        cypher = """
        MATCH (n)
        WITH collect(n)[0..$max_nodes] AS nodes
        UNWIND nodes AS a
        OPTIONAL MATCH (a)-[r]->(b)
        WHERE b IN nodes
        WITH nodes, collect(DISTINCT r)[0..$max_edges] AS rels
        RETURN
          [n IN nodes | {id: id(n), labels: labels(n), props: properties(n)}] AS nodes,
          [r IN rels | {id: id(r), source: id(startNode(r)), target: id(endNode(r)), type: type(r), props: properties(r)}] AS edges
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
        return graph_data


@app.post("/api/chat", summary="GraphRAG + LangChain agent")
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
        "trace": get_last_trace(),
    }


@app.post("/api/chat/stream", summary="GraphRAG streaming chat")
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
                    "trace": get_last_trace(),
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


@app.post("/api/graph_view", summary="Get graph visualization data")
def graph_view(request: GraphViewRequest):
    mode = (request.mode or "focus").strip().lower()
    max_nodes = request.max_nodes or (80 if mode == "focus" else 400)
    max_edges = request.max_edges or (160 if mode == "focus" else 1000)
    max_nodes = max(10, min(max_nodes, 1500))
    max_edges = max(20, min(max_edges, 5000))

    driver = neo4j_driver
    if driver is None:
        raise HTTPException(status_code=503, detail="Neo4j driver is not ready")
    if mode == "full":
        graph_data = _query_full_graph(driver, max_nodes=max_nodes, max_edges=max_edges)
    else:
        graph_data = _query_focus_graph(
            driver,
            question=request.question or "",
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    return {"ok": True, "graph": graph_data}


@app.get("/admin", summary="Admin UI for ingestion")
def admin_ui():
    page = Path(__file__).with_name("admin.html")
    if not page.exists():
        raise HTTPException(status_code=404, detail="admin.html not found")
    return FileResponse(page)


@app.post("/api/ingest/csv", summary="Ingest CSV incrementally")
async def ingest_csv_endpoint(
    file: UploadFile = File(...),
    batch_size: int = Form(2000),
    update_mode: str = Form("safe"),
):
    try:
        uploads_dir = Path(__file__).with_name("uploads")
        uploads_dir.mkdir(exist_ok=True)
        file_path = uploads_dir / file.filename
        content = await file.read()
        file_path.write_bytes(content)
        effective_update_mode = _preferred_csv_update_mode()
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


@app.post("/api/ingest/pdf", summary="Ingest PDF with template/LLM hybrid")
async def ingest_pdf_endpoint(
    file: UploadFile = File(...),
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
        effective_mode = _preferred_doc_mode()
        effective_update_mode = _preferred_doc_update_mode()
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


@app.post("/api/ingest/text", summary="Ingest natural language text")
def ingest_text_endpoint(request: TextIngestRequest):
    try:
        text = (request.text or "").strip()
        if not text:
            raise ValueError("text is empty")
        mode = _preferred_doc_mode()
        batch_size = request.batch_size or 2000
        update_mode = _preferred_doc_update_mode()
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
