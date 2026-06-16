from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from hashlib import sha1
from threading import Lock
from typing import Any, Callable, Dict, List, Optional, Tuple

from langchain_core.prompts import PromptTemplate
from langchain_core.tools import Tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from kg_tools import (
    analyze_key_bottlenecks,
    carrier_delay_performance,
    category_profitability,
    component_exposure_risk,
    component_quality_cost_tradeoff,
    compare_supplier_risk,
    customer_delay_exposure,
    delay_root_mix,
    department_exposure_summary,
    department_supply_fragility,
    delivery_performance_by_ship_mode,
    estimate_delay_loss,
    estimate_supplier_disruption_loss,
    get_last_trace,
    graph,
    late_risk_carrier_modes,
    late_risk_products,
    order_root_cause,
    order_status_summary,
    on_time_delivery_by_supplier,
    payment_type_risk,
    profit_at_risk_by_order_stage,
    region_revenue,
    route_delay_risk,
    segment_financial_exposure,
    set_last_trace,
    single_source_components,
    substitute_supplier_candidates,
    supplier_affected_orders,
    supplier_affected_products,
    supplier_concentration_by_product,
    supplier_ripple_effect,
    supplier_risk_profile,
    suppliers_with_high_defect_rate,
    top_customers_by_revenue,
    top_products_by_profit,
)
from rca import RCAEngine


def _load_dotenv() -> None:
    if not os.path.exists(".env"):
        return
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "88888888")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
MODEL_NAME = os.getenv("LLM_MODEL", "deepseek-chat")

if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
if OPENAI_API_BASE:
    os.environ["OPENAI_API_BASE"] = OPENAI_API_BASE

if not OPENAI_API_KEY:
    raise ValueError(
        "OPENAI_API_KEY is missing. Configure it in env or .env with OPENAI_API_BASE and LLM_MODEL."
    )

graph.refresh_schema()
llm = ChatOpenAI(temperature=0, model=MODEL_NAME)
logger = logging.getLogger("supplychain.graphrag")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


ProgressCallback = Optional[Callable[[str, Dict[str, Any]], None]]
_CACHE_MAX = int(os.getenv("AGENT_CACHE_SIZE", "128"))
_ANSWER_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_PLAN_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_HOP_CACHE: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_MAX_HISTORY_CHARS = int(os.getenv("AGENT_MAX_HISTORY_CHARS", "12000"))
_MAX_TOOL_CHARS = int(os.getenv("AGENT_MAX_TOOL_CHARS", "12000"))
_MAX_DYNAMIC_ROWS = int(os.getenv("AGENT_MAX_DYNAMIC_ROWS", "80"))
_MAX_SYNTH_EVIDENCE_CHARS = int(os.getenv("AGENT_MAX_SYNTH_EVIDENCE_CHARS", "18000"))
_MAX_COMPLEX_HOPS = int(os.getenv("AGENT_MAX_COMPLEX_HOPS", "3"))
_MAX_HOP_WORKERS = int(os.getenv("AGENT_MAX_HOP_WORKERS", "3"))
_CACHE_LOCK = Lock()


@dataclass
class _FastRoute:
    name: str
    tool: Any
    tool_input: Any
    summary: str


def _emit_progress(callback: ProgressCallback, event: str, payload: Dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(event, payload)
    except Exception:
        logger.exception("Progress callback failed")


def _cache_get(cache: "OrderedDict[str, Dict[str, Any]]", key: str) -> Optional[Dict[str, Any]]:
    with _CACHE_LOCK:
        value = cache.get(key)
        if value is None:
            return None
        cache.move_to_end(key)
        return value


def _cache_set(cache: "OrderedDict[str, Dict[str, Any]]", key: str, value: Dict[str, Any]) -> None:
    with _CACHE_LOCK:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > _CACHE_MAX:
            cache.popitem(last=False)


def _cache_key(*parts: str) -> str:
    base = "\n".join(part.strip() for part in parts if part)
    return sha1(base.encode("utf-8")).hexdigest()


def _truncate_text(text: str, limit: int, label: str = "text") -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n\n[Truncated {label}: omitted {omitted} characters]"


def _get_schema_text() -> str:
    schema = getattr(graph, "schema", None)
    if callable(schema):
        schema = schema()
    if not schema and hasattr(graph, "get_schema"):
        schema = graph.get_schema()  # type: ignore[attr-defined]
    return schema or ""


def _sanitize_cypher(cypher: str) -> str:
    lines: List[str] = []
    for line in cypher.splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        line = re.sub(r"\s--.*$", "", line)
        line = re.sub(r"\s//.*$", "", line)
        line = re.sub(r"\s#.*$", "", line)
        lines.append(line)
    return "\n".join(lines).strip()


def _run_dynamic_query(cypher_prompt: PromptTemplate, query: str) -> str:
    schema_text = _get_schema_text()
    prompt_text = cypher_prompt.format(schema=schema_text, question=query)
    message = llm.invoke(prompt_text)
    raw_cypher = getattr(message, "content", "") or ""
    cypher = _sanitize_cypher(raw_cypher)
    if not cypher:
        return "Dynamic query failed: LLM did not produce valid Cypher."
    set_last_trace({"tool": "Dynamic_Graph_Query", "type": "cypher", "cypher": cypher})
    try:
        result = graph.query(cypher)
    except Exception as e:
        return f"Dynamic query failed: {str(e)}"
    if not result:
        return "No data found."
    payload = {
        "row_count": len(result),
        "rows": result[:_MAX_DYNAMIC_ROWS],
    }
    if len(result) > _MAX_DYNAMIC_ROWS:
        payload["truncated"] = True
        payload["message"] = (
            f"Result capped to {_MAX_DYNAMIC_ROWS} rows for context safety."
        )
    return _truncate_text(
        json.dumps(payload, ensure_ascii=False),
        _MAX_TOOL_CHARS,
        label="dynamic query result",
    )


def _build_dynamic_cypher_tool() -> Tool:
    cypher_template = """You are a Neo4j Cypher expert.
Write ONE Cypher query that answers the question using the schema.
Rules:
- Output ONLY Cypher. No markdown, no code fences, no comments.
- Use date('YYYY-MM-DD') for date literals if needed.
- Define relationship variables when relationship properties are needed.
Schema:
{schema}
Question:
{question}"""
    cypher_prompt = PromptTemplate(
        input_variables=["schema", "question"],
        template=cypher_template,
    )
    return Tool(
        name="Dynamic_Graph_Query",
        func=lambda q: _run_dynamic_query(cypher_prompt, q),
        description=(
            "Use this when fixed tools cannot satisfy filters, aggregations, or custom graph paths."
        ),
    )


def _safe_tool_invoke(tool_obj: Any, tool_input: Any) -> str:
    raw = _invoke_tool_direct(tool_obj, tool_input)
    return _truncate_text(raw, _MAX_TOOL_CHARS, label="tool output")


def _wrap_tool(tool_obj: Any) -> Tool:
    return Tool(
        name=getattr(tool_obj, "name", getattr(tool_obj, "__name__", "tool")),
        func=lambda tool_input, t=tool_obj: _safe_tool_invoke(t, tool_input),
        description=getattr(tool_obj, "description", "") or "",
    )


def _build_tools() -> List[Any]:
    return [
        _wrap_tool(supplier_ripple_effect),
        _wrap_tool(supplier_affected_orders),
        _wrap_tool(supplier_affected_products),
        _wrap_tool(component_exposure_risk),
        _wrap_tool(single_source_components),
        _wrap_tool(order_root_cause),
        _wrap_tool(analyze_key_bottlenecks),
        _wrap_tool(estimate_delay_loss),
        _wrap_tool(estimate_supplier_disruption_loss),
        _wrap_tool(top_products_by_profit),
        _wrap_tool(top_customers_by_revenue),
        _wrap_tool(suppliers_with_high_defect_rate),
        _wrap_tool(carrier_delay_performance),
        _wrap_tool(segment_financial_exposure),
        _wrap_tool(category_profitability),
        _wrap_tool(department_exposure_summary),
        _wrap_tool(department_supply_fragility),
        _wrap_tool(compare_supplier_risk),
        _wrap_tool(supplier_risk_profile),
        _wrap_tool(on_time_delivery_by_supplier),
        _wrap_tool(delay_root_mix),
        _wrap_tool(customer_delay_exposure),
        _wrap_tool(substitute_supplier_candidates),
        _wrap_tool(supplier_concentration_by_product),
        _wrap_tool(component_quality_cost_tradeoff),
        _wrap_tool(route_delay_risk),
        _wrap_tool(profit_at_risk_by_order_stage),
        _wrap_tool(order_status_summary),
        _wrap_tool(late_risk_products),
        _wrap_tool(late_risk_carrier_modes),
        _wrap_tool(region_revenue),
        _wrap_tool(payment_type_risk),
        _wrap_tool(delivery_performance_by_ship_mode),
        _build_dynamic_cypher_tool(),
    ]


def _build_graphrag_route(engine: str, summary: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    route = {
        "engine": "graphrag",
        "route_type": "general",
        "path": engine,
        "summary": summary,
    }
    if extra:
        route.update(extra)
    return route


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _history_to_text(messages: List[Any], max_turns: int = 6) -> str:
    turns: List[str] = []
    for role, content in messages[-max_turns * 2 :]:
        turns.append(f"{role}: {content}")
    return _truncate_text(
        "\n".join(turns),
        _MAX_HISTORY_CHARS,
        label="conversation history",
    )


def _extract_order_id(text: str) -> str:
    match = re.search(r"\bORD-\d{4}-\d+\b", text, re.I)
    return match.group(0) if match else ""


def _extract_supplier_name(text: str) -> str:
    stop_fragments = ["影响", "订单", "产品", "断供", "风险", "损失", "哪些", "多少"]
    quoted = re.search(r"[\"“”'']([^\"“”'']{2,40})[\"“”'']", text)
    if quoted:
        candidate = quoted.group(1).strip()
        if candidate and not any(fragment in candidate for fragment in stop_fragments):
            return candidate

    patterns = [
        r"(?:供应商|supplier)\s*(?:是|为|叫|name|named)\s*[:：]?\s*([A-Za-z0-9\u4e00-\u9fff()（）._\-\s]{2,40})",
        r"(?:供应商|supplier)\s*[:：]\s*([A-Za-z0-9\u4e00-\u9fff()（）._\-\s]{2,40})",
        r"([A-Za-z0-9\u4e00-\u9fff()（）._\-\s]{2,40})\s*(?:供应商|supplier)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        candidate = re.split(r"[，。,.?？!！]", match.group(1))[0].strip(" ：:;；")
        candidate = re.sub(r"\s+", " ", candidate)
        if candidate and len(candidate) >= 2 and not any(fragment in candidate for fragment in stop_fragments):
            return candidate
    return ""


def _extract_supplier_names(text: str) -> List[str]:
    names: List[str] = []
    for match in re.finditer(r"([A-Za-z0-9\u4e00-\u9fff()（）._\-\s]{2,40}\([A-Za-z0-9._\-\s]{2,20}\))", text):
        candidate = re.sub(r"\s+", " ", match.group(1)).strip()
        if candidate and candidate not in names:
            names.append(candidate)
    if len(names) >= 2:
        return names[:5]

    parts = [
        part.strip(" ：:;；")
        for part in re.split(r"(?:和|与|跟|以及|vs|VS|对比|比较|,|，)", text)
        if part and part.strip()
    ]
    stop_fragments = ["供应风险", "订单", "金额", "影响", "比较", "对比", "谁"]
    for part in parts:
        normalized = re.sub(r"\s+", " ", part)
        if 2 <= len(normalized) <= 40 and not any(token in normalized for token in stop_fragments):
            if normalized not in names:
                names.append(normalized)
    return names[:5]


def _extract_int_value(text: str, default: int = 5) -> int:
    match = re.search(r"(\d+)", text)
    if not match:
        return default
    try:
        return max(1, min(int(match.group(1)), 50))
    except Exception:
        return default


def _invoke_tool_direct(tool_obj: Any, tool_input: Any) -> str:
    if hasattr(tool_obj, "invoke"):
        return str(tool_obj.invoke(tool_input))
    if isinstance(tool_input, dict):
        return str(tool_obj(**tool_input))
    return str(tool_obj(tool_input))


def _question_needs_multi_factor_reasoning(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    strong_markers = [
        "同时",
        "结合",
        "举例",
        "并且",
        "综合",
        "联动",
        "多维",
        "多个维度",
        "从多个角度",
        "从不同角度",
        "延误风险",
        "利润暴露",
        "高瓶颈性",
        "高延误风险",
        "高利润暴露",
    ]
    if any(marker in q for marker in strong_markers):
        return True
    dimension_hits = sum(
        1
        for marker in ["瓶颈", "延误", "利润", "暴露", "订单", "产品", "节点", "供应链", "风险"]
        if marker in q
    )
    return dimension_hits >= 4


def _question_requests_examples(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    return any(marker in q for marker in ["举例", "例如", "比如", "样例", "案例"])


def _tool_output_is_insufficient(tool_output: str) -> bool:
    text = (tool_output or "").strip()
    if not text:
        return True

    lowered = text.lower()
    no_data_markers = [
        "no data",
        "not found",
        "no root-cause path",
        "no bottleneck results",
        "no supplier comparison data",
        "missing order id",
        "missing supplier",
    ]
    if any(marker in lowered for marker in no_data_markers):
        return True
    if re.match(r"^no\b", lowered):
        return True

    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None

    if isinstance(parsed, list) and not parsed:
        return True
    if isinstance(parsed, dict):
        if "rows" in parsed and not parsed.get("rows"):
            return True
        if "data" in parsed and not parsed.get("data"):
            return True

    return False


def _fast_route_question(question: str) -> Optional[_FastRoute]:
    q = (question or "").strip()
    ql = q.lower()
    order_id = _extract_order_id(q)
    supplier_name = _extract_supplier_name(q)
    supplier_names = _extract_supplier_names(q)
    top_k = _extract_int_value(q, default=5)
    delay_days = _extract_int_value(q, default=3)

    # Composite questions usually need multi-hop evidence synthesis instead of
    # a single fixed tool response.
    if _question_needs_multi_factor_reasoning(q):
        return None

    if len(supplier_names) >= 2 and any(key in q for key in ["对比", "比较", "谁影响", "谁的金额", "谁更大"]):
        return _FastRoute(
            name="compare_supplier_risk",
            tool=compare_supplier_risk,
            tool_input={"supplier_names": ", ".join(supplier_names[:2])},
            summary=f"direct route: compare supplier risk for {supplier_names[0]} and {supplier_names[1]}",
        )

    if order_id and any(key in q for key in ["根因", "追溯", "上游", "延误原因"]):
        return _FastRoute(
            name="order_root_cause",
            tool=order_root_cause,
            tool_input={"order_id": order_id},
            summary=f"direct route: order root cause for {order_id}",
        )

    if supplier_name and ("影响" in q or "affected" in ql) and "订单" in q:
        return _FastRoute(
            name="supplier_affected_orders",
            tool=supplier_affected_orders,
            tool_input={"supplier_name": supplier_name, "top_k": top_k},
            summary=f"direct route: affected orders for supplier {supplier_name}",
        )

    if supplier_name and ("影响" in q or "affected" in ql) and "产品" in q:
        return _FastRoute(
            name="supplier_affected_products",
            tool=supplier_affected_products,
            tool_input={"supplier_name": supplier_name, "top_k": top_k},
            summary=f"direct route: affected products for supplier {supplier_name}",
        )

    if supplier_name and any(key in q for key in ["断供", "涟漪", "风险", "损失"]):
        tool_obj = (
            estimate_supplier_disruption_loss
            if any(key in q for key in ["损失", "估算", "金额"])
            else supplier_ripple_effect
        )
        return _FastRoute(
            name=getattr(tool_obj, "name", "supplier_risk"),
            tool=tool_obj,
            tool_input={"supplier_name": supplier_name},
            summary=f"direct route: supplier risk for {supplier_name}",
        )

    if any(key in q for key in ["利润贡献最高的产品", "利润最高的产品", "top product", "top products"]):
        return _FastRoute(
            name="top_products_by_profit",
            tool=top_products_by_profit,
            tool_input={"top_k": top_k},
            summary="direct route: top products by profit",
        )

    if any(key in q for key in ["收入最高的客户", "营收最高的客户", "top customer", "top customers"]):
        return _FastRoute(
            name="top_customers_by_revenue",
            tool=top_customers_by_revenue,
            tool_input={"top_k": top_k},
            summary="direct route: top customers by revenue",
        )

    if any(key in q for key in ["瓶颈", "关键供应商"]) or "bottleneck" in ql:
        if _question_needs_multi_factor_reasoning(q):
            return None
        scenario = "finance" if "finance" in ql or "财务" in q else "production" if "production" in ql or "生产" in q else "general"
        return _FastRoute(
            name="analyze_key_bottlenecks",
            tool=analyze_key_bottlenecks,
            tool_input={"scenario": scenario},
            summary=f"direct route: bottleneck analysis ({scenario})",
        )

    if any(key in q for key in ["延误超过", "延误损失", "delay loss"]):
        return _FastRoute(
            name="estimate_delay_loss",
            tool=estimate_delay_loss,
            tool_input={"min_delay_days": delay_days},
            summary=f"direct route: delay loss for {delay_days} days",
        )

    return None


def _plan_question(question: str, history_text: str) -> Dict[str, Any]:
    cache_key = _cache_key("plan", history_text, question)
    cached = _cache_get(_PLAN_CACHE, cache_key)
    if cached is not None:
        return dict(cached)

    composite_guidance = ""
    if _question_needs_multi_factor_reasoning(question):
        composite_guidance = """
Composite-question rules:
- If the user asks for multiple dimensions at once, allocate separate sub_questions for each dimension.
- Cover every requested constraint explicitly instead of collapsing them into one vague hop.
- When the user combines graph structure, delay risk, and financial exposure, plan at least one hop for each evidence type.
"""
    example_guidance = ""
    if _question_requests_examples(question):
        example_guidance = """
Example rules:
- If the user asks for examples, include one sub_question that retrieves representative orders and products as concrete evidence.
- The final hop may focus on finding the best supporting examples rather than only summary statistics.
"""

    planner_prompt = f"""You are a GraphRAG planner for supply-chain analysis.
Break the user question into 1-{_MAX_COMPLEX_HOPS} retrieval hops that can be answered from a knowledge graph.
Latency rules:
- Prefer fewer, broader hops over many narrow hops.
- Avoid redundant or overlapping sub_questions.
- Only create an extra hop when it adds unique evidence needed for the final answer.
{composite_guidance}{example_guidance}Important language rule:
- sub_questions must always be written in Simplified Chinese.
- reasoning_path must always be written in Simplified Chinese.
- focus_entities should prefer Chinese labels when possible.
- Even if the user asks in English, still output Chinese sub_questions.
Return JSON only:
{{
  "intent": "简短中文描述",
  "sub_questions": ["中文子问题1", "中文子问题2"],
  "reasoning_path": ["中文原因1", "中文原因2"],
  "focus_entities": ["供应商/订单/产品等中文实体类型"]
}}
Conversation history:
{history_text}
User question:
{question}
"""
    resp = llm.invoke(planner_prompt)
    plan = _extract_json_object(getattr(resp, "content", "") or "")
    sub_questions = plan.get("sub_questions")
    if not isinstance(sub_questions, list) or not sub_questions:
        logger.warning("Planner JSON invalid, fallback to single-hop.")
        plan = {
            "intent": "直接检索",
            "sub_questions": [f"请用知识图谱回答这个问题：{question}"],
            "reasoning_path": ["规划结果无效，回退到单跳中文检索"],
            "focus_entities": [],
        }
    else:
        normalized_sub_questions: List[str] = []
        for item in sub_questions:
            text = str(item).strip()
            if not text:
                continue
            if not re.search(r"[\u4e00-\u9fff]", text):
                text = f"请用中文回答并检索：{text}"
            if text not in normalized_sub_questions:
                normalized_sub_questions.append(text)
        plan["sub_questions"] = normalized_sub_questions[:_MAX_COMPLEX_HOPS]
        if not plan["sub_questions"]:
            plan["sub_questions"] = [f"请用知识图谱回答这个问题：{question}"]
    reasoning_path = plan.get("reasoning_path")
    if isinstance(reasoning_path, list):
        normalized_reasoning: List[str] = []
        for item in reasoning_path:
            text = str(item).strip()
            if not text:
                continue
            if not re.search(r"[\u4e00-\u9fff]", text):
                text = f"中文推理说明：{text}"
            normalized_reasoning.append(text)
        plan["reasoning_path"] = normalized_reasoning[:_MAX_COMPLEX_HOPS]
    elif reasoning_path:
        text = str(reasoning_path).strip()
        plan["reasoning_path"] = [text if re.search(r"[\u4e00-\u9fff]", text) else f"中文推理说明：{text}"]
    else:
        plan["reasoning_path"] = ["按中文子问题逐跳检索图谱证据"]
    _cache_set(_PLAN_CACHE, cache_key, dict(plan))
    return plan


def _retrieve_one_hop(
    retriever: Any,
    question: str,
    sub_question: str,
    hop_index: int,
    hop_total: int,
) -> Dict[str, Any]:
    cache_key = _cache_key("hop", question, sub_question)
    cached = _cache_get(_HOP_CACHE, cache_key)
    if cached is not None:
        return dict(cached)

    fast_route = _fast_route_question(sub_question)
    if fast_route is not None:
        tool_output = _invoke_tool_direct(fast_route.tool, fast_route.tool_input)
        if not _tool_output_is_insufficient(tool_output):
            result = {
                "sub_question": sub_question,
                "evidence": _truncate_text(tool_output, _MAX_TOOL_CHARS, label="hop evidence"),
                "tool_trace": get_last_trace()
                or {
                    "tool": getattr(fast_route.tool, "name", fast_route.name),
                    "type": "direct",
                },
            }
            _cache_set(_HOP_CACHE, cache_key, dict(result))
            return result
        logger.info(
            "Direct hop tool %s returned insufficient output; fallback to retriever",
            getattr(fast_route.tool, "name", fast_route.name),
        )

    retrieval_system = """You are a graph retrieval specialist.
Goal: answer the sub-question with concrete graph evidence.
Requirements:
- Prefer fixed tools first.
- If fixed tools are insufficient, use Dynamic_Graph_Query.
- Include specific IDs/names and numbers.
- If data is missing, explicitly say what is missing.
"""
    retrieval_user = f"""Original question:
{question}

Sub-question ({hop_index}/{hop_total}):
{sub_question}

Return concise factual findings for this hop only.
"""
    result = retriever.invoke(
        {"messages": [("system", retrieval_system), ("user", retrieval_user)]}
    )
    hop_text = _truncate_text(
        result["messages"][-1].content,
        _MAX_TOOL_CHARS,
        label="hop evidence",
    )
    trace_snapshot = get_last_trace()
    payload = {
        "sub_question": sub_question,
        "evidence": hop_text,
        "tool_trace": trace_snapshot,
    }
    _cache_set(_HOP_CACHE, cache_key, dict(payload))
    return payload


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
                continue
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
        return "".join(parts)
    return str(content or "")


def _message_to_text(message: Any) -> str:
    return _content_to_text(getattr(message, "content", message))


def _stream_llm_text(prompt: str, progress_callback: ProgressCallback = None) -> str:
    if progress_callback is None:
        return _message_to_text(llm.invoke(prompt)).strip()

    try:
        chunks: List[str] = []
        for chunk in llm.stream(prompt):
            text = _message_to_text(chunk)
            if not text:
                continue
            chunks.append(text)
            _emit_progress(progress_callback, "delta", {"text": text})
        if chunks:
            return "".join(chunks).strip()
    except Exception:
        logger.exception("LLM streaming failed; falling back to invoke")

    return _message_to_text(llm.invoke(prompt)).strip()


def _synthesize_answer(
    question: str,
    history_text: str,
    plan: Dict[str, Any],
    hop_results: List[Dict[str, Any]],
    progress_callback: ProgressCallback = None,
) -> str:
    evidence_text = "\n\n".join(
        [
            f"Hop {idx + 1} sub-question: {item['sub_question']}\nFindings:\n{item['evidence']}"
            for idx, item in enumerate(hop_results)
        ]
    )
    evidence_text = _truncate_text(
        evidence_text,
        _MAX_SYNTH_EVIDENCE_CHARS,
        label="synthesis evidence",
    )
    synthesis_prompt = f"""You are a supply-chain GraphRAG assistant.
Use the planned multi-hop evidence to answer the user question.
Rules:
- Respond in the user's language.
- First paragraph: direct answer.
- Then provide a short section for key evidence and a short section for actionable suggestions.
- Do not claim data that is not in evidence.
- Output plain text only. Do not use markdown symbols such as *, **, #, -, or backticks.
Conversation history:
{history_text}

User question:
{question}

Plan JSON:
{json.dumps(plan, ensure_ascii=False)}

Hop evidence:
{evidence_text}
"""
    content = _stream_llm_text(synthesis_prompt, progress_callback)
    return _sanitize_text_output(content or evidence_text)


def _synthesize_direct_answer(
    question: str,
    history_text: str,
    route: _FastRoute,
    tool_output: str,
    progress_callback: ProgressCallback = None,
) -> str:
    if not tool_output or tool_output.startswith("Missing ") or "failed:" in tool_output.lower():
        return _sanitize_text_output(tool_output or "No data found.")

    prompt = f"""You are a supply-chain analysis assistant.
Use the tool result below to answer the user's question accurately and concisely.
Rules:
- Respond in the user's language.
- Start with a direct answer.
- Then give short evidence and a short suggestion section.
- If the tool result is JSON, summarize the important entities, counts, and values.
- Do not invent facts outside the tool result.
Conversation history:
{history_text}

User question:
{question}

Route:
{route.summary}

Tool result:
{tool_output}
"""
    content = _stream_llm_text(prompt, progress_callback)
    return _sanitize_text_output(content or tool_output)


def _sanitize_text_output(text: str) -> str:
    cleaned = text or ""
    cleaned = cleaned.replace("**", "")
    cleaned = cleaned.replace("__", "")
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^\s*[-*]\s+", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _run_parallel_hops(
    retriever: Any,
    question: str,
    sub_questions: List[str],
    progress_callback: ProgressCallback = None,
    request_id: str = "",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not sub_questions:
        return [], []

    total = len(sub_questions)
    hop_results: List[Optional[Dict[str, Any]]] = [None] * total
    progress_log: List[str] = []

    max_workers = max(1, min(_MAX_HOP_WORKERS, total))
    if total == 1:
        result = _retrieve_one_hop(
            retriever,
            question=question,
            sub_question=sub_questions[0],
            hop_index=1,
            hop_total=1,
        )
        hop_results[0] = result
        tool_name = (result.get("tool_trace") or {}).get("tool", "unknown")
        progress_log.append(f"hop 1/1: tool={tool_name}")
        return [result], progress_log

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {}
        for idx, sub_q in enumerate(sub_questions, start=1):
            _emit_progress(
                progress_callback,
                "status",
                {
                    "request_id": request_id,
                    "stage": "retrieval",
                    "message": f"Retrieving hop {idx}/{total}",
                    "sub_question": sub_q,
                },
            )
            future = executor.submit(
                _retrieve_one_hop,
                retriever,
                question,
                sub_q,
                idx,
                total,
            )
            future_to_index[future] = idx - 1

        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            sub_q = sub_questions[idx]
            try:
                result = future.result()
            except Exception as exc:
                logger.exception("Parallel hop failed; falling back to error payload")
                result = {
                    "sub_question": sub_q,
                    "evidence": f"Hop retrieval failed: {exc}",
                    "tool_trace": {"tool": "parallel_hop", "type": "error"},
                }
            hop_results[idx] = result
            tool_name = (result.get("tool_trace") or {}).get("tool", "unknown")
            progress_log.append(f"hop {idx + 1}/{total}: tool={tool_name}")
            _emit_progress(
                progress_callback,
                "status",
                {
                    "request_id": request_id,
                    "stage": "retrieval",
                    "message": f"Completed hop {idx + 1}/{total}: {tool_name}",
                    "sub_question": sub_q,
                },
            )

    return [item for item in hop_results if item is not None], progress_log


@dataclass
class _AssistantMessage:
    content: str


class GraphRAGAgentExecutor:
    def __init__(self, base_llm: ChatOpenAI):
        self.base_llm = base_llm
        self.retriever = create_react_agent(base_llm, _build_tools())
        self.rca_engine = RCAEngine(base_llm)

    def invoke(
        self, payload: Dict[str, Any], progress_callback: ProgressCallback = None
    ) -> Dict[str, Any]:
        messages: List[Tuple[str, str]] = payload.get("messages", [])
        if not messages:
            return {"messages": [_AssistantMessage(content="Question is empty.")]}

        question = ""
        for role, content in reversed(messages):
            if role == "user":
                question = content
                break
        question = question.strip()
        if not question:
            return {"messages": [_AssistantMessage(content="Question is empty.")]}

        request_id = uuid.uuid4().hex[:8]
        req_start = time.perf_counter()
        progress_log: List[str] = []
        question_short = question.replace("\n", " ").strip()[:120]
        logger.info("[req:%s] start question=%s", request_id, question_short)
        progress_log.append("start: received question")
        _emit_progress(
            progress_callback,
            "status",
            {"request_id": request_id, "stage": "start", "message": "已接收问题，开始分析"},
        )

        history_text = _history_to_text(messages[:-1], max_turns=6)
        answer_cache_key = _cache_key("answer", history_text, question)
        cached_answer = _cache_get(_ANSWER_CACHE, answer_cache_key)
        if cached_answer is not None:
            logger.info("[req:%s] cache hit", request_id)
            progress_log.append("cache: answer hit")
            trace = dict(cached_answer["trace"])
            trace["request_id"] = request_id
            set_last_trace(trace)
            _emit_progress(
                progress_callback,
                "status",
                {"request_id": request_id, "stage": "cache", "message": "命中缓存，直接返回结果"},
            )
            return {
                "messages": [_AssistantMessage(content=cached_answer["reply"])],
                "trace": trace,
                "rca": cached_answer.get("rca"),
            }

        rca_intent = self.rca_engine.detect_intent(question, history_text=history_text)
        if rca_intent.route_type == "rca":
            logger.info(
                "[req:%s] rca route subtype=%s target=%s",
                request_id,
                rca_intent.subtype,
                rca_intent.target_id,
            )
            progress_log.append(
                f"routing: rca -> {rca_intent.subtype} ({rca_intent.target_type}:{rca_intent.target_id})"
            )
            _emit_progress(
                progress_callback,
                "status",
                {
                    "request_id": request_id,
                    "stage": "routing",
                    "message": f"识别为 RCA 问题，切换到 {rca_intent.subtype} 专用分析链路",
                },
            )
            rca_start = time.perf_counter()
            rca_result = self.rca_engine.run(
                question,
                history_text=history_text,
                progress_callback=progress_callback,
                intent=rca_intent,
            )
            if rca_result.get("handled"):
                rca_ms = int((time.perf_counter() - rca_start) * 1000)
                total_ms = int((time.perf_counter() - req_start) * 1000)
                progress_log.append(f"rca: {rca_ms} ms")
                progress_log.append(f"done: total {total_ms} ms")
                trace = dict(rca_result.get("trace") or {})
                trace["request_id"] = request_id
                trace["progress_log"] = progress_log
                set_last_trace(trace)
                final_answer = rca_result.get("reply", "")
                _cache_set(
                    _ANSWER_CACHE,
                    answer_cache_key,
                    {"reply": final_answer, "trace": trace, "rca": rca_result.get("rca")},
                )
                return {
                    "messages": [_AssistantMessage(content=final_answer)],
                    "trace": trace,
                    "rca": rca_result.get("rca"),
                }

        fast_route = _fast_route_question(question)
        if fast_route is not None:
            logger.info("[req:%s] fast route=%s", request_id, fast_route.name)
            progress_log.append(f"routing: {fast_route.summary}")
            _emit_progress(
                progress_callback,
                "status",
                {
                    "request_id": request_id,
                    "stage": "routing",
                    "message": "已命中快速查询路径，跳过多跳规划",
                },
            )
            tool_start = time.perf_counter()
            tool_output = _invoke_tool_direct(fast_route.tool, fast_route.tool_input)
            tool_ms = int((time.perf_counter() - tool_start) * 1000)
            tool_name = getattr(fast_route.tool, "name", fast_route.name)
            progress_log.append(f"tool: {tool_name}, {tool_ms} ms")
            if not _tool_output_is_insufficient(tool_output):
                _emit_progress(
                    progress_callback,
                    "status",
                    {
                        "request_id": request_id,
                        "stage": "tool",
                        "message": f"已完成核心查询：{tool_name}",
                    },
                )

                synth_start = time.perf_counter()
                final_answer = _synthesize_direct_answer(
                    question, history_text, fast_route, tool_output, progress_callback
                )
                synth_ms = int((time.perf_counter() - synth_start) * 1000)
                total_ms = int((time.perf_counter() - req_start) * 1000)
                progress_log.append(f"synthesis: {synth_ms} ms")
                progress_log.append(f"done: total {total_ms} ms")
                trace = {
                    "tool": "GraphRAG_Orchestrator",
                    "engine": "graphrag",
                    "type": "pipeline",
                    "mode": "graphrag",
                    "route": _build_graphrag_route(
                        "fast-route",
                        f"GraphRAG -> fast-route ({tool_name})",
                        {"tool_name": tool_name},
                    ),
                    "route_summary": f"GraphRAG -> fast-route ({tool_name})",
                    "request_id": request_id,
                    "progress_log": progress_log,
                    "plan": {"intent": "fast-route", "sub_questions": [question]},
                    "hops": [
                        {
                            "hop": 1,
                            "sub_question": question,
                            "tool_trace": get_last_trace()
                            or {"tool": tool_name, "type": "direct"},
                        }
                    ],
                }
                set_last_trace(trace)
                _cache_set(
                    _ANSWER_CACHE,
                    answer_cache_key,
                    {"reply": final_answer, "trace": trace},
                )
                _emit_progress(
                    progress_callback,
                    "status",
                    {
                        "request_id": request_id,
                        "stage": "synthesis",
                        "message": "已生成最终回答",
                    },
                )
                return {
                    "messages": [_AssistantMessage(content=final_answer)],
                    "trace": trace,
                    "rca": None,
                }

            logger.info(
                "[req:%s] fast route=%s returned insufficient output; fallback to full pipeline",
                request_id,
                fast_route.name,
            )
            progress_log.append(f"fallback: {tool_name} insufficient, switch to planner")
            _emit_progress(
                progress_callback,
                "status",
                {
                    "request_id": request_id,
                    "stage": "routing",
                    "message": "固定查询结果不足，回退到动态推理与LLM检索链路",
                },
            )

        plan_start = time.perf_counter()
        _emit_progress(
            progress_callback,
            "status",
            {"request_id": request_id, "stage": "planning", "message": "正在拆解问题并规划检索路径"},
        )
        plan = _plan_question(question, history_text)
        sub_questions = plan.get("sub_questions", [question])[:_MAX_COMPLEX_HOPS]
        plan_ms = int((time.perf_counter() - plan_start) * 1000)
        logger.info(
            "[req:%s] planning done hops=%s cost_ms=%s",
            request_id,
            len(sub_questions),
            plan_ms,
        )
        progress_log.append(f"planning: {len(sub_questions)} hops in {plan_ms} ms")
        _emit_progress(
            progress_callback,
            "status",
            {
                "request_id": request_id,
                "stage": "planning",
                "message": f"规划完成，准备执行 {len(sub_questions)} 跳检索",
            },
        )

        retrieval_start = time.perf_counter()
        hop_results, hop_progress = _run_parallel_hops(
            self.retriever,
            question=question,
            sub_questions=sub_questions,
            progress_callback=progress_callback,
            request_id=request_id,
        )
        retrieval_ms = int((time.perf_counter() - retrieval_start) * 1000)
        progress_log.append(f"retrieval: {len(hop_results)} hops in {retrieval_ms} ms")
        progress_log.extend(hop_progress)
        for i, item in enumerate(hop_results, start=1):
            hop_trace = item.get("tool_trace") or {}
            tool_name = hop_trace.get("tool", "unknown")
            logger.info(
                "[req:%s] hop %s/%s done tool=%s",
                request_id,
                i,
                len(hop_results),
                tool_name,
            )

        synth_start = time.perf_counter()
        logger.info("[req:%s] synthesis start", request_id)
        _emit_progress(
            progress_callback,
            "status",
            {"request_id": request_id, "stage": "synthesis", "message": "正在综合证据并组织回答"},
        )
        final_answer = _synthesize_answer(
            question,
            history_text,
            plan,
            hop_results,
            progress_callback,
        )
        synth_ms = int((time.perf_counter() - synth_start) * 1000)
        total_ms = int((time.perf_counter() - req_start) * 1000)
        logger.info(
            "[req:%s] synthesis done cost_ms=%s total_ms=%s",
            request_id,
            synth_ms,
            total_ms,
        )
        progress_log.append(f"synthesis: {synth_ms} ms")
        progress_log.append(f"done: total {total_ms} ms")

        trace = {
            "tool": "GraphRAG_Orchestrator",
            "engine": "graphrag",
            "type": "pipeline",
            "mode": "graphrag",
            "route": _build_graphrag_route(
                "multi-hop",
                f"GraphRAG -> multi-hop ({len(hop_results)} hops)",
                {"hop_count": len(hop_results)},
            ),
            "route_summary": f"GraphRAG -> multi-hop ({len(hop_results)} hops)",
            "request_id": request_id,
            "progress_log": progress_log,
            "plan": plan,
            "hops": [
                {
                    "hop": idx + 1,
                    "sub_question": item["sub_question"],
                    "tool_trace": item.get("tool_trace"),
                }
                for idx, item in enumerate(hop_results)
            ],
        }
        set_last_trace(trace)
        _cache_set(
            _ANSWER_CACHE,
            answer_cache_key,
            {"reply": final_answer, "trace": trace},
        )
        return {
            "messages": [_AssistantMessage(content=final_answer)],
            "trace": trace,
            "rca": None,
        }


SYSTEM_PROMPT = """You are a supply-chain GraphRAG assistant.
Always follow this logic:
1) understand and decompose the question
2) perform multi-hop graph retrieval
3) synthesize evidence into a final answer
Use concrete IDs, names, and numbers whenever possible.
"""


def build_agent() -> GraphRAGAgentExecutor:
    return GraphRAGAgentExecutor(llm)


def ask_agent(question: str) -> str:
    agent = build_agent()
    response = agent.invoke({"messages": [("system", SYSTEM_PROMPT), ("user", question)]})
    return response["messages"][-1].content


def main() -> None:
    print("SupplyChain GraphRAG Agent. Type 'exit' to quit.")
    while True:
        question = input("Q> ").strip()
        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            break
        answer = ask_agent(question)
        print("\n" + answer + "\n")


if __name__ == "__main__":
    main()
