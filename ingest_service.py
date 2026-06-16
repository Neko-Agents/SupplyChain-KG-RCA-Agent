"""
Ingestion service for SupplyChain KG.

Supports:
- CSV incremental upsert (batch UNWIND + MERGE)
- PDF text extraction (template + LLM hybrid)
- Natural language text ingestion (LLM or template)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover - optional dependency for preview-only flows
    GraphDatabase = None  # type: ignore

try:
    from pypdf import PdfReader  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    PdfReader = None  # type: ignore

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional dependency
    ChatOpenAI = None  # type: ignore


def _load_dotenv():
    """
    Minimal .env loader (no external dependency).
    """
    if not os.path.exists(".env"):
        return
    with open(".env", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()

# ================= Configuration =================
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "88888888")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
MODEL_NAME = os.getenv("LLM_MODEL", "deepseek-chat")
LONG_TEXT_CHUNK_TRIGGER = int(os.getenv("INGEST_LLM_CHUNK_TRIGGER", "5000"))
LONG_TEXT_CHUNK_CHARS = int(os.getenv("INGEST_LLM_CHUNK_CHARS", "3200"))
LONG_TEXT_CHUNK_OVERLAP = int(os.getenv("INGEST_LLM_CHUNK_OVERLAP", "300"))
# ================================================


def _strip_markdown_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


ID_PATTERN_MAP = {
    "customer_id": r"\bCUST-\d+\b",
    "order_id": r"\bORD-\d{4}-\d+\b",
    "product_id": r"\bSKU-[A-Z0-9\-]+\b",
}

RELATION_SCHEMA = {
    "PLACED_ORDER": ("Customer", "Order"),
    "CONTAINS_PRODUCT": ("Order", "Product"),
    "BELONGS_TO_CATEGORY": ("Product", "Category"),
    "BELONGS_TO_DEPARTMENT": ("Category", "Department"),
    "SUPPLIES_COMPONENT": ("Supplier", "Component"),
    "USED_IN": ("Component", "Product"),
    "SHIPPED_BY": ("Order", "Carrier"),
}

MUNICIPALITY_NAMES = {"北京", "上海", "天津", "重庆"}

NAME_LIKE_FIELDS = {
    "customer_name",
    "customer_city",
    "customer_province",
    "customer_country",
    "category_name",
    "department_name",
    "supplier_name",
    "supplier_city",
    "component_name",
    "carrier_name",
    "product_name",
    "product_desc",
    "payment_type",
    "order_status",
    "delivery_status",
    "trans_mode",
    "ship_mode",
}

NAME_BASED_ENTITY_TYPES = {"Category", "Department", "Supplier", "Component", "Carrier"}
ID_BASED_ENTITY_TYPES = {"Customer", "Order", "Product"}

TEXT_FIELD_MAX_LENGTH = {
    "product_desc": 500,
    "customer_street": 200,
    "_default": 120,
}

RELATION_ERROR_EXAMPLE_LIMIT = 10


def _prepare_text_for_extraction(text: str) -> str:
    cleaned = text or ""
    cleaned = re.sub(r"```[\s\S]*?```", "\n", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace("\u3000", " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _unwrap_pdf_value_lines(text: str) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s*\n\s*(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"(?<=[A-Za-z0-9])\s*\n\s*(?=[A-Za-z])", " ", value)
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s*\n\s*(?=[A-Za-z0-9\(])", " ", value)
    value = re.sub(r"(?<=[A-Za-z0-9\)])\s*\n\s*(?=[\u4e00-\u9fff])", "", value)
    value = re.sub(r"(?<=\()\s*\n\s*(?=[A-Za-z])", "", value)
    value = re.sub(r"(?<=[A-Za-z])\s*\n\s*(?=\))", "", value)
    return value


def _is_noisy_field_value(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    if "```" in text:
        return True
    if len(text) > 80 and (text.count("{") + text.count("}") + text.count("[") + text.count("]")) >= 4:
        return True
    lowered = text.lower()
    if lowered.startswith(("json:", "python:", "sql:", "cypher:")):
        return True
    return False


def _clip_text_field(key: str, value: str) -> str:
    limit = TEXT_FIELD_MAX_LENGTH.get(key, TEXT_FIELD_MAX_LENGTH["_default"])
    return value[:limit].strip()


def _clean_scalar_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = _strip_markdown_fences(value)
    cleaned = cleaned.replace("`", " ")
    cleaned = _unwrap_pdf_value_lines(cleaned)
    cleaned = cleaned.replace("\u3000", " ")
    cleaned = re.sub(r"^(?:[\-\*\u2022]+|\d{1,3}[.)])\s*", "", cleaned)
    cleaned = re.sub(
        r"^(?:[A-Za-z_][A-Za-z0-9_ \-]{0,30}|[\u4e00-\u9fff]{1,12})\s*[:：]\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\n+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" \t\n\r\"'`，,；;")
    return cleaned or None


def _clean_identifier_field(key: str, value: Any) -> Optional[str]:
    cleaned = _clean_scalar_text(value)
    if not cleaned:
        return None
    pattern = ID_PATTERN_MAP.get(key)
    if not pattern:
        return str(cleaned)
    compact = re.sub(r"\s+", "", str(cleaned))
    match = re.search(pattern, compact, re.I)
    if not match:
        return None
    return match.group(0).upper()


def _clean_text_field(key: str, value: Any) -> Optional[str]:
    cleaned = _clean_scalar_text(value)
    if not cleaned:
        return None
    cleaned = str(cleaned)
    if _is_noisy_field_value(cleaned):
        return None
    return _clip_text_field(key, cleaned)


def _normalize_location_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    text = re.sub(r"(省|市|自治区|特别行政区)$", "", text)
    text = re.sub(r"(壮族自治区|回族自治区|维吾尔自治区)$", "", text)
    text = re.sub(r"\s+", "", text)
    return text or None


def _normalize_display_text(key: str, value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*\(\s*", " (", text)
    text = re.sub(r"\s*\)\s*", ")", text)
    text = re.sub(r"([A-Za-z0-9])\(", r"\1 (", text)

    if key == "product_name":
        text = re.sub(r"(\d+寸)(?=[^\s])", r"\1 ", text)
        text = re.sub(r"(\d+K超清)(?=[^\s])", r"\1 ", text)
        text = re.sub(r"(AI手机\s*\d+)(?=Pro\b)", r"\1 ", text)
        text = re.sub(r"(无线VR一体机)(?=[^\s])", r"\1 ", text)
        text = re.sub(r"\s+", " ", text).strip()

    if key in {"customer_city", "customer_province", "supplier_city"}:
        return _normalize_location_name(text)

    return text.strip()


def _postprocess_record_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key in NAME_LIKE_FIELDS:
        if key in out and out[key] is not None:
            out[key] = _normalize_display_text(key, out[key])

    city = out.get("customer_city")
    province = out.get("customer_province")
    if city and province and city == province and city not in MUNICIPALITY_NAMES:
        out["customer_province"] = None

    supplier_city = out.get("supplier_city")
    if supplier_city and supplier_city not in MUNICIPALITY_NAMES and supplier_city == out.get("customer_city"):
        out["supplier_city"] = supplier_city

    return out


def _coerce_float_value(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("，", ",").replace("％", "%")
    text = re.sub(r"[。；;、]+$", "", text)
    text = re.sub(r"(元|天|件|笔|%)$", "", text)
    text = text.replace(",", "")
    text = re.sub(r"(?<=\d)%$", "", text)
    try:
        return float(text)
    except Exception:
        return None


def _coerce_int_value(value: Any) -> Optional[int]:
    number = _coerce_float_value(value)
    if number is None:
        return None
    try:
        return int(number)
    except Exception:
        return None


def _merge_sparse_rows(rows: List[Dict[str, Any]], key_fn) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    fallback: List[Dict[str, Any]] = []
    for row in rows:
        key = key_fn(row)
        if key is None:
            fallback.append(row)
            continue
        if key not in merged:
            merged[key] = dict(row)
            continue
        target = merged[key]
        for field, value in row.items():
            if target.get(field) in (None, "") and value not in (None, ""):
                target[field] = value
    return list(merged.values()) + fallback


def _entity_record_key(record: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    for key in (
        "customer_id",
        "order_id",
        "product_id",
        "category_name",
        "department_name",
        "supplier_name",
        "component_name",
        "carrier_name",
    ):
        value = record.get(key)
        if value:
            return (key, value)
    return None


def _relation_record_key(record: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    src_ref = record.get("src_id") or record.get("src_name")
    dst_ref = record.get("dst_id") or record.get("dst_name")
    if not all([record.get("src_type"), record.get("rel_type"), record.get("dst_type"), src_ref, dst_ref]):
        return None
    return (
        record.get("src_type"),
        src_ref,
        record.get("rel_type"),
        record.get("dst_type"),
        dst_ref,
    )


def _node_ref_complete(row: Dict[str, Any], prefix: str) -> bool:
    node_type = row.get(f"{prefix}_type")
    if not node_type:
        return False
    if node_type in ID_BASED_ENTITY_TYPES:
        return bool(row.get(f"{prefix}_id"))
    return bool(row.get(f"{prefix}_name"))


def _split_oversized_text_unit(text: str, max_chars: int) -> List[str]:
    normalized = (text or "").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    sentences = [
        seg.strip()
        for seg in re.split(r"(?<=[。！？!?；;])\s+|(?<=\.)\s+(?=[A-Z])", normalized)
        if seg.strip()
    ]
    if len(sentences) <= 1:
        return [normalized[i : i + max_chars] for i in range(0, len(normalized), max_chars)]

    parts: List[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current}\n{sentence}".strip() if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
        if len(sentence) <= max_chars:
            current = sentence
        else:
            parts.extend(
                [sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)]
            )
            current = ""
    if current:
        parts.append(current)
    return parts


def _build_overlap_prefix(text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0 or not text:
        return ""
    tail = text[-overlap_chars:].strip()
    if not tail:
        return ""
    return tail


def _split_long_text_for_llm(
    text: str,
    max_chars: int = LONG_TEXT_CHUNK_CHARS,
    overlap_chars: int = LONG_TEXT_CHUNK_OVERLAP,
) -> List[str]:
    prepared = _prepare_text_for_extraction(text)
    if not prepared:
        return []
    if len(prepared) <= max_chars:
        return [prepared]

    blocks = _split_text_blocks(prepared) or [prepared]
    units: List[str] = []
    for block in blocks:
        units.extend(_split_oversized_text_unit(block, max_chars))

    chunks: List[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}".strip() if current else unit
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        overlap_prefix = _build_overlap_prefix(chunks[-1], overlap_chars) if chunks else ""
        current = f"{overlap_prefix}\n\n{unit}".strip() if overlap_prefix else unit
        if len(current) > max_chars:
            pieces = _split_oversized_text_unit(current, max_chars)
            chunks.extend(pieces[:-1])
            current = pieces[-1] if pieces else ""
    if current:
        chunks.append(current)
    return chunks


def _extract_balanced_json_object(text: str) -> Optional[str]:
    """Return the first balanced JSON object found in text."""
    if not text:
        return None

    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for idx in range(start, len(text)):
        ch = text[idx]

        if escape:
            escape = False
            continue

        if ch == "\\":
            escape = True
            continue

        if ch == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]

    return None


def _repair_json_with_llm(raw_content: str, llm: Any) -> Dict[str, Any]:
    repair_prompt = (
        "Convert the following content into ONE valid JSON object only. "
        'Return strictly JSON with top-level keys "entities" and "relations". '
        "Do not add explanations, markdown, or code fences. "
        "If a field is uncertain, omit it. "
        "Content to repair:\n"
        f"{raw_content}"
    )

    repaired_msg = llm.invoke(repair_prompt)
    repaired_content = _strip_markdown_fences(
        getattr(repaired_msg, "content", "") or ""
    )
    candidate = _extract_balanced_json_object(repaired_content) or repaired_content
    return json.loads(candidate)


# Internal normalized keys for ingestion.
INTERNAL_KEYS = [
    "customer_id",
    "customer_name",
    "customer_email",
    "customer_segment",
    "customer_country",
    "customer_province",
    "customer_city",
    "customer_street",
    "customer_lat",
    "customer_lon",
    "order_id",
    "order_date",
    "shipping_date",
    "scheduled_date",
    "actual_date",
    "payment_type",
    "order_status",
    "product_id",
    "product_sku",
    "product_name",
    "product_desc",
    "product_base_price",
    "category_name",
    "department_name",
    "supplier_name",
    "supplier_city",
    "component_name",
    "carrier_name",
    "quantity",
    "gross_total",
    "discount_rate",
    "discount_amount",
    "net_total",
    "profit",
    "profit_ratio",
    "mfg_cost",
    "defect_rate",
    "trans_mode",
    "ship_mode",
    "days_scheduled",
    "days_real",
    "late_risk",
    "delivery_status",
    "last_updated_time",
]


# Map CSV column names (Chinese/English) to internal keys.
COLUMN_ALIASES = {
    # Customer
    "客户ID": "customer_id",
    "客户姓名": "customer_name",
    "客户邮箱": "customer_email",
    "客户群体": "customer_segment",
    "客户国家": "customer_country",
    "客户省份": "customer_province",
    "客户城市": "customer_city",
    "客户街道": "customer_street",
    "客户纬度": "customer_lat",
    "客户经度": "customer_lon",
    # Order
    "订单ID": "order_id",
    "订单日期": "order_date",
    "发货日期": "shipping_date",
    "预计送达日期": "scheduled_date",
    "实际送达日期": "actual_date",
    "支付类型": "payment_type",
    "订单状态": "order_status",
    # Product
    "产品ID": "product_id",
    "产品SKU": "product_sku",
    "产品名称": "product_name",
    "产品描述": "product_desc",
    "产品基础价格": "product_base_price",
    # Category/Department
    "类别名称": "category_name",
    "部门名称": "department_name",
    # Supplier/Component/Carrier
    "供应商名称": "supplier_name",
    "供应商城市": "supplier_city",
    "核心组件名称": "component_name",
    "承运商名称": "carrier_name",
    # Relationship properties
    "购买数量": "quantity",
    "销售总额_应付": "gross_total",
    "折扣率": "discount_rate",
    "折扣金额": "discount_amount",
    "实付总金额": "net_total",
    "订单单笔利润": "profit",
    "利润率": "profit_ratio",
    "单件制造成本": "mfg_cost",
    "次品率": "defect_rate",
    "运输方式": "trans_mode",
    "发货模式": "ship_mode",
    "计划物流天数": "days_scheduled",
    "实际物流天数": "days_real",
    "发货延误风险_标签": "late_risk",
    "物流运输状态": "delivery_status",
    # English aliases (if any)
    "customer_id": "customer_id",
    "customer_name": "customer_name",
    "customer_email": "customer_email",
    "customer_segment": "customer_segment",
    "customer_country": "customer_country",
    "customer_province": "customer_province",
    "customer_city": "customer_city",
    "customer_street": "customer_street",
    "customer_lat": "customer_lat",
    "customer_lon": "customer_lon",
    "order_id": "order_id",
    "order_date": "order_date",
    "shipping_date": "shipping_date",
    "scheduled_date": "scheduled_date",
    "actual_date": "actual_date",
    "payment_type": "payment_type",
    "order_status": "order_status",
    "product_id": "product_id",
    "product_sku": "product_sku",
    "product_name": "product_name",
    "product_desc": "product_desc",
    "product_base_price": "product_base_price",
    "category_name": "category_name",
    "department_name": "department_name",
    "supplier_name": "supplier_name",
    "supplier_city": "supplier_city",
    "component_name": "component_name",
    "carrier_name": "carrier_name",
    "quantity": "quantity",
    "gross_total": "gross_total",
    "discount_rate": "discount_rate",
    "discount_amount": "discount_amount",
    "net_total": "net_total",
    "profit": "profit",
    "profit_ratio": "profit_ratio",
    "mfg_cost": "mfg_cost",
    "defect_rate": "defect_rate",
    "trans_mode": "trans_mode",
    "ship_mode": "ship_mode",
    "days_scheduled": "days_scheduled",
    "days_real": "days_real",
    "late_risk": "late_risk",
    "delivery_status": "delivery_status",
}


NUMERIC_FLOAT_FIELDS = {
    "customer_lat",
    "customer_lon",
    "product_base_price",
    "gross_total",
    "discount_rate",
    "discount_amount",
    "net_total",
    "profit",
    "profit_ratio",
    "mfg_cost",
    "defect_rate",
}

NUMERIC_INT_FIELDS = {
    "quantity",
    "days_scheduled",
    "days_real",
    "late_risk",
}

# Relation-only CSV support
RELATION_CSV_ALIASES = {
    # Source
    "起点类型": "src_type",
    "起点标签": "src_type",
    "源类型": "src_type",
    "起点ID": "src_id",
    "源ID": "src_id",
    "起点名称": "src_name",
    "源名称": "src_name",
    # Relation
    "关系类型": "rel_type",
    "关系": "rel_type",
    "关系名": "rel_type",
    # Target
    "终点类型": "dst_type",
    "终点标签": "dst_type",
    "目标类型": "dst_type",
    "终点ID": "dst_id",
    "目标ID": "dst_id",
    "终点名称": "dst_name",
    "目标名称": "dst_name",
    # English
    "src_type": "src_type",
    "src_id": "src_id",
    "src_name": "src_name",
    "rel_type": "rel_type",
    "dst_type": "dst_type",
    "dst_id": "dst_id",
    "dst_name": "dst_name",
}

REL_TYPE_ALIASES = {
    "PLACED_ORDER": "PLACED_ORDER",
    "下单": "PLACED_ORDER",
    "客户下单": "PLACED_ORDER",
    "CONTAINS_PRODUCT": "CONTAINS_PRODUCT",
    "包含产品": "CONTAINS_PRODUCT",
    "订单包含产品": "CONTAINS_PRODUCT",
    "BELONGS_TO_CATEGORY": "BELONGS_TO_CATEGORY",
    "属于品类": "BELONGS_TO_CATEGORY",
    "BELONGS_TO_DEPARTMENT": "BELONGS_TO_DEPARTMENT",
    "属于部门": "BELONGS_TO_DEPARTMENT",
    "SUPPLIES_COMPONENT": "SUPPLIES_COMPONENT",
    "供应组件": "SUPPLIES_COMPONENT",
    "USED_IN": "USED_IN",
    "用于产品": "USED_IN",
    "SHIPPED_BY": "SHIPPED_BY",
    "承运": "SHIPPED_BY",
}

NODE_LABEL_ALIASES = {
    "Customer": "Customer",
    "客户": "Customer",
    "Order": "Order",
    "订单": "Order",
    "Product": "Product",
    "产品": "Product",
    "Category": "Category",
    "品类": "Category",
    "Department": "Department",
    "部门": "Department",
    "Supplier": "Supplier",
    "供应商": "Supplier",
    "Component": "Component",
    "组件": "Component",
    "Carrier": "Carrier",
    "承运商": "Carrier",
}


# Allowed update fields for natural-language update intent (conservative set)
UPDATE_FIELD_ALIASES = {
    # Customer
    "客户名称": "customer_name",
    "客户名字": "customer_name",
    "客户姓名": "customer_name",
    "客户城市": "customer_city",
    "客户省份": "customer_province",
    "客户国家": "customer_country",
    "客户群体": "customer_segment",
    "客户邮箱": "customer_email",
    # Order
    "订单状态": "order_status",
    "支付类型": "payment_type",
    "订单日期": "order_date",
    # Product
    "产品名称": "product_name",
    "产品描述": "product_desc",
    "产品价格": "product_base_price",
    "基础价格": "product_base_price",
    # Supplier
    "供应商名称": "supplier_name",
    "供应商城市": "supplier_city",
    # Carrier
    "承运商名称": "carrier_name",
    # Category / Department
    "类别名称": "category_name",
    "部门名称": "department_name",
    # Relationship: Order-Product
    "购买数量": "quantity",
    "销售总额": "gross_total",
    "销售总额_应付": "gross_total",
    "折扣率": "discount_rate",
    "折扣金额": "discount_amount",
    "实付总金额": "net_total",
    "订单利润": "profit",
    "订单单笔利润": "profit",
    "利润率": "profit_ratio",
    # Relationship: Supplier-Component
    "制造成本": "mfg_cost",
    "单件制造成本": "mfg_cost",
    "次品率": "defect_rate",
    "缺陷率": "defect_rate",
    # Relationship: Order-Carrier
    "运输方式": "trans_mode",
    "发货模式": "ship_mode",
    "计划物流天数": "days_scheduled",
    "实际物流天数": "days_real",
    "延误风险": "late_risk",
    "物流状态": "delivery_status",
}


def _extract_ids_from_text(text: str) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    # Standard IDs
    m = re.search(r"\bCUST-\d+\b", text, re.I)
    if m:
        ids["customer_id"] = m.group(0).upper()
    m = re.search(r"\bORD-\d{4}-\d+\b", text, re.I)
    if m:
        ids["order_id"] = m.group(0).upper()
    m = re.search(r"\bSKU-[A-Z0-9\-]+\b", text, re.I)
    if m:
        ids["product_id"] = m.group(0).upper()
    return ids


def _try_parse_update_intent(text: str) -> List[Dict[str, Any]]:
    """
    Parse natural-language update intent like:
    修改用户CUST-90002的名字为王老七
    将订单ORD-2024-100002的订单状态改为已发货
    把SKU-ST-M01的产品名称更新为16GB DDR5 6000MHz
    """
    if not re.search(r"(修改|更新|改为|调整|把|将)", text):
        return []

    ids = _extract_ids_from_text(text)

    # Extract target field and new value
    # Patterns: "X的Y为Z" / "把X的Y改为Z" / "将X的Y更新为Z"
    m = re.search(r"(?:把|将)?(.+?)的(.+?)(?:改为|更新为|修改为|调整为|设为|为)\s*([^，,\n\r]+)", text)
    if not m:
        # Pattern: "修改用户CUST-90002名字为王老七"
        m = re.search(r"(?:修改|更新)\s*(.+?)(?:的)?(.+?)(?:改为|更新为|修改为|调整为|设为|为)\s*([^，,\n\r]+)", text)
    if not m:
        return []

    field_label = m.group(2).strip()
    new_value = m.group(3).strip()

    # Normalize common field aliases
    field_key = UPDATE_FIELD_ALIASES.get(field_label)
    if not field_key:
        # Try to trim common words
        field_key = UPDATE_FIELD_ALIASES.get(field_label.replace("的", ""))
    if not field_key:
        return []

    record: Dict[str, Any] = {}
    record.update(ids)
    record[field_key] = new_value
    record["last_updated_time"] = _now_iso()
    record = _sanitize_row(record)

    # For relationship updates, ensure required IDs exist
    rel_fields = {
        "quantity",
        "gross_total",
        "discount_rate",
        "discount_amount",
        "net_total",
        "profit",
        "profit_ratio",
    }
    ship_fields = {"trans_mode", "ship_mode", "days_scheduled", "days_real", "late_risk", "delivery_status"}
    sup_fields = {"mfg_cost", "defect_rate"}

    if field_key in rel_fields:
        if not (record.get("order_id") and record.get("product_id")):
            return []
    if field_key in ship_fields:
        if not (record.get("order_id") and record.get("carrier_name")):
            return []
    if field_key in sup_fields:
        if not (record.get("supplier_name") and record.get("component_name")):
            # allow supplier_name only if component not specified; skip to avoid ambiguous update
            return []

    return [record]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="gbk")


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map: Dict[str, str] = {}
    for col in df.columns:
        col_clean = str(col).strip()
        if col_clean in COLUMN_ALIASES:
            rename_map[col] = COLUMN_ALIASES[col_clean]
        elif col_clean in RELATION_CSV_ALIASES:
            rename_map[col] = RELATION_CSV_ALIASES[col_clean]
    return df.rename(columns=rename_map)


def _sanitize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in row.items():
        if key not in INTERNAL_KEYS:
            continue
        if isinstance(val, str):
            if key.endswith("_id"):
                val = _clean_identifier_field(key, val)
            else:
                val = _clean_text_field(key, val)
        out[key] = val

    # Coerce numeric fields
    for key in NUMERIC_FLOAT_FIELDS:
        if key in out and out[key] is not None:
            out[key] = _coerce_float_value(out[key])
    for key in NUMERIC_INT_FIELDS:
        if key in out and out[key] is not None:
            out[key] = _coerce_int_value(out[key])

    return _postprocess_record_fields(out)


def _df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
    df = _rename_columns(df)
    df = df.replace({pd.NA: None})
    df = df.where(pd.notnull(df), None)

    # Add batch timestamp
    df["last_updated_time"] = _now_iso()

    records = []
    for raw in df.to_dict(orient="records"):
        records.append(_sanitize_row(raw))
    return records


def _is_relation_csv(df: pd.DataFrame) -> bool:
    cols = set(df.columns)
    required = {"src_type", "rel_type", "dst_type"}
    has_min = required.issubset(cols)
    has_ids = ("src_id" in cols or "src_name" in cols) and (
        "dst_id" in cols or "dst_name" in cols
    )
    return bool(has_min and has_ids)


def _chunked(records: List[Dict[str, Any]], size: int) -> Iterable[List[Dict[str, Any]]]:
    size = max(1, size)
    for i in range(0, len(records), size):
        yield records[i : i + size]


def _get_driver():
    if GraphDatabase is None:
        raise RuntimeError(
            "neo4j not installed. Please install neo4j before running import/write operations."
        )
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def _upsert_batch(tx, rows: List[Dict[str, Any]], update_mode: str) -> None:
    cypher = """
    UNWIND $rows AS row
    WITH row

    FOREACH (_ IN CASE WHEN row.customer_id IS NOT NULL AND row.customer_id <> '' THEN [1] ELSE [] END |
        MERGE (c:Customer {id: row.customer_id})
        ON CREATE SET
            c.name = row.customer_name,
            c.email = row.customer_email,
            c.segment = row.customer_segment,
            c.country = row.customer_country,
            c.province = row.customer_province,
            c.city = row.customer_city,
            c.street = row.customer_street,
            c.lat = row.customer_lat,
            c.lon = row.customer_lon,
            c.last_updated_time = row.last_updated_time
        ON MATCH SET
            c.name = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_name, c.name)
                WHEN (c.name IS NULL OR c.name = '') AND row.customer_name IS NOT NULL THEN row.customer_name
                ELSE c.name END,
            c.email = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_email, c.email)
                WHEN (c.email IS NULL OR c.email = '') AND row.customer_email IS NOT NULL THEN row.customer_email
                ELSE c.email END,
            c.segment = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_segment, c.segment)
                WHEN (c.segment IS NULL OR c.segment = '') AND row.customer_segment IS NOT NULL THEN row.customer_segment
                ELSE c.segment END,
            c.country = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_country, c.country)
                WHEN (c.country IS NULL OR c.country = '') AND row.customer_country IS NOT NULL THEN row.customer_country
                ELSE c.country END,
            c.province = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_province, c.province)
                WHEN (c.province IS NULL OR c.province = '') AND row.customer_province IS NOT NULL THEN row.customer_province
                ELSE c.province END,
            c.city = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_city, c.city)
                WHEN (c.city IS NULL OR c.city = '') AND row.customer_city IS NOT NULL THEN row.customer_city
                ELSE c.city END,
            c.street = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_street, c.street)
                WHEN (c.street IS NULL OR c.street = '') AND row.customer_street IS NOT NULL THEN row.customer_street
                ELSE c.street END,
            c.lat = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_lat, c.lat)
                WHEN c.lat IS NULL AND row.customer_lat IS NOT NULL THEN row.customer_lat
                ELSE c.lat END,
            c.lon = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.customer_lon, c.lon)
                WHEN c.lon IS NULL AND row.customer_lon IS NOT NULL THEN row.customer_lon
                ELSE c.lon END,
            c.last_updated_time = CASE
                WHEN $update_mode = 'overwrite' THEN row.last_updated_time
                ELSE c.last_updated_time END
    )

    FOREACH (_ IN CASE WHEN row.order_id IS NOT NULL AND row.order_id <> '' THEN [1] ELSE [] END |
        MERGE (o:Order {id: row.order_id})
        ON CREATE SET
            o.order_date = row.order_date,
            o.shipping_date = row.shipping_date,
            o.scheduled_date = row.scheduled_date,
            o.actual_date = row.actual_date,
            o.payment_type = row.payment_type,
            o.status = row.order_status,
            o.last_updated_time = row.last_updated_time
        ON MATCH SET
            o.order_date = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.order_date, o.order_date)
                WHEN (o.order_date IS NULL OR o.order_date = '') AND row.order_date IS NOT NULL THEN row.order_date
                ELSE o.order_date END,
            o.shipping_date = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.shipping_date, o.shipping_date)
                WHEN (o.shipping_date IS NULL OR o.shipping_date = '') AND row.shipping_date IS NOT NULL THEN row.shipping_date
                ELSE o.shipping_date END,
            o.scheduled_date = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.scheduled_date, o.scheduled_date)
                WHEN (o.scheduled_date IS NULL OR o.scheduled_date = '') AND row.scheduled_date IS NOT NULL THEN row.scheduled_date
                ELSE o.scheduled_date END,
            o.actual_date = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.actual_date, o.actual_date)
                WHEN (o.actual_date IS NULL OR o.actual_date = '') AND row.actual_date IS NOT NULL THEN row.actual_date
                ELSE o.actual_date END,
            o.payment_type = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.payment_type, o.payment_type)
                WHEN (o.payment_type IS NULL OR o.payment_type = '') AND row.payment_type IS NOT NULL THEN row.payment_type
                ELSE o.payment_type END,
            o.status = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.order_status, o.status)
                WHEN (o.status IS NULL OR o.status = '') AND row.order_status IS NOT NULL THEN row.order_status
                ELSE o.status END,
            o.last_updated_time = CASE
                WHEN $update_mode = 'overwrite' THEN row.last_updated_time
                ELSE o.last_updated_time END
    )

    FOREACH (_ IN CASE WHEN row.product_id IS NOT NULL AND row.product_id <> '' THEN [1] ELSE [] END |
        MERGE (p:Product {id: row.product_id})
        ON CREATE SET
            p.sku = row.product_sku,
            p.name = row.product_name,
            p.desc = row.product_desc,
            p.base_price = row.product_base_price,
            p.last_updated_time = row.last_updated_time
        ON MATCH SET
            p.sku = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.product_sku, p.sku)
                WHEN (p.sku IS NULL OR p.sku = '') AND row.product_sku IS NOT NULL THEN row.product_sku
                ELSE p.sku END,
            p.name = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.product_name, p.name)
                WHEN (p.name IS NULL OR p.name = '') AND row.product_name IS NOT NULL THEN row.product_name
                ELSE p.name END,
            p.desc = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.product_desc, p.desc)
                WHEN (p.desc IS NULL OR p.desc = '') AND row.product_desc IS NOT NULL THEN row.product_desc
                ELSE p.desc END,
            p.base_price = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.product_base_price, p.base_price)
                WHEN p.base_price IS NULL AND row.product_base_price IS NOT NULL THEN row.product_base_price
                ELSE p.base_price END,
            p.last_updated_time = CASE
                WHEN $update_mode = 'overwrite' THEN row.last_updated_time
                ELSE p.last_updated_time END
    )

    FOREACH (_ IN CASE WHEN row.category_name IS NOT NULL AND row.category_name <> '' THEN [1] ELSE [] END |
        MERGE (cat:Category {name: row.category_name})
        ON CREATE SET cat.last_updated_time = row.last_updated_time
        ON MATCH SET cat.last_updated_time = CASE
            WHEN $update_mode = 'overwrite' THEN row.last_updated_time
            ELSE cat.last_updated_time END
    )

    FOREACH (_ IN CASE WHEN row.department_name IS NOT NULL AND row.department_name <> '' THEN [1] ELSE [] END |
        MERGE (dept:Department {name: row.department_name})
        ON CREATE SET dept.last_updated_time = row.last_updated_time
        ON MATCH SET dept.last_updated_time = CASE
            WHEN $update_mode = 'overwrite' THEN row.last_updated_time
            ELSE dept.last_updated_time END
    )

    FOREACH (_ IN CASE WHEN row.supplier_name IS NOT NULL AND row.supplier_name <> '' THEN [1] ELSE [] END |
        MERGE (s:Supplier {name: row.supplier_name})
        ON CREATE SET
            s.city = row.supplier_city,
            s.last_updated_time = row.last_updated_time
        ON MATCH SET
            s.city = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.supplier_city, s.city)
                WHEN (s.city IS NULL OR s.city = '') AND row.supplier_city IS NOT NULL THEN row.supplier_city
                ELSE s.city END,
            s.last_updated_time = CASE
                WHEN $update_mode = 'overwrite' THEN row.last_updated_time
                ELSE s.last_updated_time END
    )

    FOREACH (_ IN CASE WHEN row.component_name IS NOT NULL AND row.component_name <> '' THEN [1] ELSE [] END |
        MERGE (comp:Component {name: row.component_name})
        ON CREATE SET comp.last_updated_time = row.last_updated_time
        ON MATCH SET comp.last_updated_time = CASE
            WHEN $update_mode = 'overwrite' THEN row.last_updated_time
            ELSE comp.last_updated_time END
    )

    FOREACH (_ IN CASE WHEN row.carrier_name IS NOT NULL AND row.carrier_name <> '' THEN [1] ELSE [] END |
        MERGE (car:Carrier {name: row.carrier_name})
        ON CREATE SET car.last_updated_time = row.last_updated_time
        ON MATCH SET car.last_updated_time = CASE
            WHEN $update_mode = 'overwrite' THEN row.last_updated_time
            ELSE car.last_updated_time END
    )

    // Relationships
    FOREACH (_ IN CASE WHEN row.customer_id IS NOT NULL AND row.order_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (c:Customer {id: row.customer_id})
        MERGE (o:Order {id: row.order_id})
        MERGE (c)-[:PLACED_ORDER]->(o)
    )

    FOREACH (_ IN CASE WHEN row.order_id IS NOT NULL AND row.product_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (o:Order {id: row.order_id})
        MERGE (p:Product {id: row.product_id})
        MERGE (o)-[con:CONTAINS_PRODUCT]->(p)
        SET
            con.quantity = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.quantity, con.quantity)
                WHEN con.quantity IS NULL AND row.quantity IS NOT NULL THEN row.quantity
                ELSE con.quantity END,
            con.gross_total = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.gross_total, con.gross_total)
                WHEN con.gross_total IS NULL AND row.gross_total IS NOT NULL THEN row.gross_total
                ELSE con.gross_total END,
            con.discount_rate = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.discount_rate, con.discount_rate)
                WHEN con.discount_rate IS NULL AND row.discount_rate IS NOT NULL THEN row.discount_rate
                ELSE con.discount_rate END,
            con.discount_amount = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.discount_amount, con.discount_amount)
                WHEN con.discount_amount IS NULL AND row.discount_amount IS NOT NULL THEN row.discount_amount
                ELSE con.discount_amount END,
            con.net_total = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.net_total, con.net_total)
                WHEN con.net_total IS NULL AND row.net_total IS NOT NULL THEN row.net_total
                ELSE con.net_total END,
            con.profit = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.profit, con.profit)
                WHEN con.profit IS NULL AND row.profit IS NOT NULL THEN row.profit
                ELSE con.profit END,
            con.profit_ratio = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.profit_ratio, con.profit_ratio)
                WHEN con.profit_ratio IS NULL AND row.profit_ratio IS NOT NULL THEN row.profit_ratio
                ELSE con.profit_ratio END
    )

    FOREACH (_ IN CASE WHEN row.product_id IS NOT NULL AND row.category_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (p:Product {id: row.product_id})
        MERGE (cat:Category {name: row.category_name})
        MERGE (p)-[:BELONGS_TO_CATEGORY]->(cat)
    )

    FOREACH (_ IN CASE WHEN row.category_name IS NOT NULL AND row.department_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (cat:Category {name: row.category_name})
        MERGE (dept:Department {name: row.department_name})
        MERGE (cat)-[:BELONGS_TO_DEPARTMENT]->(dept)
    )

    FOREACH (_ IN CASE WHEN row.supplier_name IS NOT NULL AND row.component_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (s:Supplier {name: row.supplier_name})
        MERGE (comp:Component {name: row.component_name})
        MERGE (s)-[sup:SUPPLIES_COMPONENT]->(comp)
        SET
            sup.mfg_cost = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.mfg_cost, sup.mfg_cost)
                WHEN sup.mfg_cost IS NULL AND row.mfg_cost IS NOT NULL THEN row.mfg_cost
                ELSE sup.mfg_cost END,
            sup.defect_rate = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.defect_rate, sup.defect_rate)
                WHEN sup.defect_rate IS NULL AND row.defect_rate IS NOT NULL THEN row.defect_rate
                ELSE sup.defect_rate END
    )

    FOREACH (_ IN CASE WHEN row.component_name IS NOT NULL AND row.product_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (comp:Component {name: row.component_name})
        MERGE (p:Product {id: row.product_id})
        MERGE (comp)-[:USED_IN]->(p)
    )

    FOREACH (_ IN CASE WHEN row.order_id IS NOT NULL AND row.carrier_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (o:Order {id: row.order_id})
        MERGE (car:Carrier {name: row.carrier_name})
        MERGE (o)-[ship:SHIPPED_BY]->(car)
        SET
            ship.trans_mode = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.trans_mode, ship.trans_mode)
                WHEN (ship.trans_mode IS NULL OR ship.trans_mode = '') AND row.trans_mode IS NOT NULL THEN row.trans_mode
                ELSE ship.trans_mode END,
            ship.ship_mode = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.ship_mode, ship.ship_mode)
                WHEN (ship.ship_mode IS NULL OR ship.ship_mode = '') AND row.ship_mode IS NOT NULL THEN row.ship_mode
                ELSE ship.ship_mode END,
            ship.days_scheduled = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.days_scheduled, ship.days_scheduled)
                WHEN ship.days_scheduled IS NULL AND row.days_scheduled IS NOT NULL THEN row.days_scheduled
                ELSE ship.days_scheduled END,
            ship.days_real = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.days_real, ship.days_real)
                WHEN ship.days_real IS NULL AND row.days_real IS NOT NULL THEN row.days_real
                ELSE ship.days_real END,
            ship.late_risk = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.late_risk, ship.late_risk)
                WHEN ship.late_risk IS NULL AND row.late_risk IS NOT NULL THEN row.late_risk
                ELSE ship.late_risk END,
            ship.delivery_status = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.delivery_status, ship.delivery_status)
                WHEN (ship.delivery_status IS NULL OR ship.delivery_status = '') AND row.delivery_status IS NOT NULL THEN row.delivery_status
                ELSE ship.delivery_status END
    )
    """
    tx.run(cypher, rows=rows, update_mode=update_mode)


def _normalize_relation_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if "src_type" in out and out["src_type"] is not None:
        out["src_type"] = NODE_LABEL_ALIASES.get(str(out["src_type"]).strip(), str(out["src_type"]).strip())
    if "dst_type" in out and out["dst_type"] is not None:
        out["dst_type"] = NODE_LABEL_ALIASES.get(str(out["dst_type"]).strip(), str(out["dst_type"]).strip())
    if "rel_type" in out and out["rel_type"] is not None:
        out["rel_type"] = REL_TYPE_ALIASES.get(str(out["rel_type"]).strip(), str(out["rel_type"]).strip())
    if "src_id" in out:
        out["src_id"] = _clean_identifier_field("customer_id", out["src_id"]) if out.get("src_type") == "Customer" else (
            _clean_identifier_field("order_id", out["src_id"]) if out.get("src_type") == "Order" else (
                _clean_identifier_field("product_id", out["src_id"]) if out.get("src_type") == "Product" else _clean_text_field("src_id", out["src_id"])
            )
        )
    if "dst_id" in out:
        out["dst_id"] = _clean_identifier_field("customer_id", out["dst_id"]) if out.get("dst_type") == "Customer" else (
            _clean_identifier_field("order_id", out["dst_id"]) if out.get("dst_type") == "Order" else (
                _clean_identifier_field("product_id", out["dst_id"]) if out.get("dst_type") == "Product" else _clean_text_field("dst_id", out["dst_id"])
            )
        )
    if "src_name" in out:
        out["src_name"] = _normalize_display_text("src_name", _clean_text_field("src_name", out["src_name"]))
    if "dst_name" in out:
        out["dst_name"] = _normalize_display_text("dst_name", _clean_text_field("dst_name", out["dst_name"]))
    return out


def _relation_row_ref_value(row: Dict[str, Any], side: str) -> Optional[str]:
    rtype = row.get(f"{side}_type")
    if rtype in ID_BASED_ENTITY_TYPES:
        return row.get(f"{side}_id")
    if rtype in NAME_BASED_ENTITY_TYPES:
        return row.get(f"{side}_name")
    return None


def _validate_relation_row(row: Dict[str, Any]) -> Optional[str]:
    src_type = row.get("src_type")
    dst_type = row.get("dst_type")
    rel_type = row.get("rel_type")

    if not rel_type:
        return "missing rel_type"
    if rel_type not in RELATION_SCHEMA:
        return f"unsupported rel_type: {rel_type}"
    if not src_type:
        return "missing src_type"
    if not dst_type:
        return "missing dst_type"
    if src_type not in ID_BASED_ENTITY_TYPES and src_type not in NAME_BASED_ENTITY_TYPES:
        return f"unsupported src_type: {src_type}"
    if dst_type not in ID_BASED_ENTITY_TYPES and dst_type not in NAME_BASED_ENTITY_TYPES:
        return f"unsupported dst_type: {dst_type}"

    expected_src, expected_dst = RELATION_SCHEMA[rel_type]
    if src_type != expected_src or dst_type != expected_dst:
        return (
            f"schema mismatch for {rel_type}: expected "
            f"{expected_src}->{expected_dst}, got {src_type}->{dst_type}"
        )

    if expected_src in ID_BASED_ENTITY_TYPES and not row.get("src_id"):
        return f"missing src_id for {expected_src}"
    if expected_src in NAME_BASED_ENTITY_TYPES and not row.get("src_name"):
        return f"missing src_name for {expected_src}"
    if expected_dst in ID_BASED_ENTITY_TYPES and not row.get("dst_id"):
        return f"missing dst_id for {expected_dst}"
    if expected_dst in NAME_BASED_ENTITY_TYPES and not row.get("dst_name"):
        return f"missing dst_name for {expected_dst}"

    return None


def _relation_error_example(
    row_number: int, row: Dict[str, Any], reason: str
) -> Dict[str, Any]:
    return {
        "row_number": row_number,
        "reason": reason,
        "src_type": row.get("src_type"),
        "src_ref": _relation_row_ref_value(row, "src"),
        "rel_type": row.get("rel_type"),
        "dst_type": row.get("dst_type"),
        "dst_ref": _relation_row_ref_value(row, "dst"),
    }


def _prepare_relation_records(df: pd.DataFrame) -> Dict[str, Any]:
    df = _rename_columns(df)
    df = df.replace({pd.NA: None})
    df = df.where(pd.notnull(df), None)

    normalized_records: List[Dict[str, Any]] = []
    relation_counts: Dict[str, int] = {}
    error_examples: List[Dict[str, Any]] = []
    skipped_rows = 0

    for row_number, raw in enumerate(df.to_dict(orient="records"), start=1):
        normalized = _normalize_relation_row(raw)
        error_reason = _validate_relation_row(normalized)
        if error_reason:
            skipped_rows += 1
            if len(error_examples) < RELATION_ERROR_EXAMPLE_LIMIT:
                error_examples.append(
                    _relation_error_example(row_number, normalized, error_reason)
                )
            continue

        normalized_records.append(normalized)
        rel_type = str(normalized.get("rel_type") or "").strip()
        relation_counts[rel_type] = relation_counts.get(rel_type, 0) + 1

    return {
        "records": normalized_records,
        "input_rows": len(df),
        "valid_rows": len(normalized_records),
        "skipped_rows": skipped_rows,
        "relation_counts": relation_counts,
        "error_examples": error_examples,
    }


def _upsert_relation_batch(tx, rows: List[Dict[str, Any]], update_mode: str) -> None:
    cypher = """
    UNWIND $rows AS row
    WITH row

    // Source node
    FOREACH (_ IN CASE WHEN row.src_type = 'Customer' AND row.src_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Customer {id: row.src_id})
    )
    FOREACH (_ IN CASE WHEN row.src_type = 'Order' AND row.src_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Order {id: row.src_id})
    )
    FOREACH (_ IN CASE WHEN row.src_type = 'Product' AND row.src_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Product {id: row.src_id})
    )
    FOREACH (_ IN CASE WHEN row.src_type = 'Category' AND row.src_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Category {name: row.src_name})
    )
    FOREACH (_ IN CASE WHEN row.src_type = 'Department' AND row.src_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Department {name: row.src_name})
    )
    FOREACH (_ IN CASE WHEN row.src_type = 'Supplier' AND row.src_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Supplier {name: row.src_name})
    )
    FOREACH (_ IN CASE WHEN row.src_type = 'Component' AND row.src_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Component {name: row.src_name})
    )
    FOREACH (_ IN CASE WHEN row.src_type = 'Carrier' AND row.src_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (a:Carrier {name: row.src_name})
    )

    // Target node
    FOREACH (_ IN CASE WHEN row.dst_type = 'Customer' AND row.dst_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Customer {id: row.dst_id})
    )
    FOREACH (_ IN CASE WHEN row.dst_type = 'Order' AND row.dst_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Order {id: row.dst_id})
    )
    FOREACH (_ IN CASE WHEN row.dst_type = 'Product' AND row.dst_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Product {id: row.dst_id})
    )
    FOREACH (_ IN CASE WHEN row.dst_type = 'Category' AND row.dst_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Category {name: row.dst_name})
    )
    FOREACH (_ IN CASE WHEN row.dst_type = 'Department' AND row.dst_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Department {name: row.dst_name})
    )
    FOREACH (_ IN CASE WHEN row.dst_type = 'Supplier' AND row.dst_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Supplier {name: row.dst_name})
    )
    FOREACH (_ IN CASE WHEN row.dst_type = 'Component' AND row.dst_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Component {name: row.dst_name})
    )
    FOREACH (_ IN CASE WHEN row.dst_type = 'Carrier' AND row.dst_name IS NOT NULL THEN [1] ELSE [] END |
        MERGE (b:Carrier {name: row.dst_name})
    )

    // Relationships (by rel_type)
    FOREACH (_ IN CASE WHEN row.rel_type = 'PLACED_ORDER' THEN [1] ELSE [] END |
        MATCH (a:Customer {id: row.src_id})
        MATCH (b:Order {id: row.dst_id})
        MERGE (a)-[:PLACED_ORDER]->(b)
    )

    FOREACH (_ IN CASE WHEN row.rel_type = 'CONTAINS_PRODUCT' THEN [1] ELSE [] END |
        MATCH (a:Order {id: row.src_id})
        MATCH (b:Product {id: row.dst_id})
        MERGE (a)-[r:CONTAINS_PRODUCT]->(b)
        SET
            r.quantity = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.quantity, r.quantity)
                WHEN r.quantity IS NULL AND row.quantity IS NOT NULL THEN row.quantity
                ELSE r.quantity END,
            r.gross_total = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.gross_total, r.gross_total)
                WHEN r.gross_total IS NULL AND row.gross_total IS NOT NULL THEN row.gross_total
                ELSE r.gross_total END,
            r.discount_rate = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.discount_rate, r.discount_rate)
                WHEN r.discount_rate IS NULL AND row.discount_rate IS NOT NULL THEN row.discount_rate
                ELSE r.discount_rate END,
            r.discount_amount = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.discount_amount, r.discount_amount)
                WHEN r.discount_amount IS NULL AND row.discount_amount IS NOT NULL THEN row.discount_amount
                ELSE r.discount_amount END,
            r.net_total = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.net_total, r.net_total)
                WHEN r.net_total IS NULL AND row.net_total IS NOT NULL THEN row.net_total
                ELSE r.net_total END,
            r.profit = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.profit, r.profit)
                WHEN r.profit IS NULL AND row.profit IS NOT NULL THEN row.profit
                ELSE r.profit END,
            r.profit_ratio = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.profit_ratio, r.profit_ratio)
                WHEN r.profit_ratio IS NULL AND row.profit_ratio IS NOT NULL THEN row.profit_ratio
                ELSE r.profit_ratio END
    )

    FOREACH (_ IN CASE WHEN row.rel_type = 'BELONGS_TO_CATEGORY' THEN [1] ELSE [] END |
        MATCH (a:Product {id: row.src_id})
        MATCH (b:Category {name: row.dst_name})
        MERGE (a)-[:BELONGS_TO_CATEGORY]->(b)
    )

    FOREACH (_ IN CASE WHEN row.rel_type = 'BELONGS_TO_DEPARTMENT' THEN [1] ELSE [] END |
        MATCH (a:Category {name: row.src_name})
        MATCH (b:Department {name: row.dst_name})
        MERGE (a)-[:BELONGS_TO_DEPARTMENT]->(b)
    )

    FOREACH (_ IN CASE WHEN row.rel_type = 'SUPPLIES_COMPONENT' THEN [1] ELSE [] END |
        MATCH (a:Supplier {name: row.src_name})
        MATCH (b:Component {name: row.dst_name})
        MERGE (a)-[r:SUPPLIES_COMPONENT]->(b)
        SET
            r.mfg_cost = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.mfg_cost, r.mfg_cost)
                WHEN r.mfg_cost IS NULL AND row.mfg_cost IS NOT NULL THEN row.mfg_cost
                ELSE r.mfg_cost END,
            r.defect_rate = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.defect_rate, r.defect_rate)
                WHEN r.defect_rate IS NULL AND row.defect_rate IS NOT NULL THEN row.defect_rate
                ELSE r.defect_rate END
    )

    FOREACH (_ IN CASE WHEN row.rel_type = 'USED_IN' THEN [1] ELSE [] END |
        MATCH (a:Component {name: row.src_name})
        MATCH (b:Product {id: row.dst_id})
        MERGE (a)-[:USED_IN]->(b)
    )

    FOREACH (_ IN CASE WHEN row.rel_type = 'SHIPPED_BY' THEN [1] ELSE [] END |
        MATCH (a:Order {id: row.src_id})
        MATCH (b:Carrier {name: row.dst_name})
        MERGE (a)-[r:SHIPPED_BY]->(b)
        SET
            r.trans_mode = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.trans_mode, r.trans_mode)
                WHEN (r.trans_mode IS NULL OR r.trans_mode = '') AND row.trans_mode IS NOT NULL THEN row.trans_mode
                ELSE r.trans_mode END,
            r.ship_mode = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.ship_mode, r.ship_mode)
                WHEN (r.ship_mode IS NULL OR r.ship_mode = '') AND row.ship_mode IS NOT NULL THEN row.ship_mode
                ELSE r.ship_mode END,
            r.days_scheduled = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.days_scheduled, r.days_scheduled)
                WHEN r.days_scheduled IS NULL AND row.days_scheduled IS NOT NULL THEN row.days_scheduled
                ELSE r.days_scheduled END,
            r.days_real = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.days_real, r.days_real)
                WHEN r.days_real IS NULL AND row.days_real IS NOT NULL THEN row.days_real
                ELSE r.days_real END,
            r.late_risk = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.late_risk, r.late_risk)
                WHEN r.late_risk IS NULL AND row.late_risk IS NOT NULL THEN row.late_risk
                ELSE r.late_risk END,
            r.delivery_status = CASE
                WHEN $update_mode = 'overwrite' THEN coalesce(row.delivery_status, r.delivery_status)
                WHEN (r.delivery_status IS NULL OR r.delivery_status = '') AND row.delivery_status IS NOT NULL THEN row.delivery_status
                ELSE r.delivery_status END
    )
    """
    tx.run(cypher, rows=rows, update_mode=update_mode)


def ingest_records(
    records: List[Dict[str, Any]], batch_size: int = 2000, update_mode: str = "safe"
) -> Dict[str, Any]:
    if not records:
        return {"rows": 0, "batches": 0}

    update_mode = (update_mode or "safe").lower()
    if update_mode not in {"safe", "overwrite"}:
        update_mode = "safe"

    with _get_driver() as driver:
        with driver.session() as session:
            batch_count = 0
            for batch in _chunked(records, batch_size):
                session.execute_write(_upsert_batch, batch, update_mode)
                batch_count += 1

    return {"rows": len(records), "batches": batch_count}


def ingest_relation_csv(
    path: str, batch_size: int = 2000, update_mode: str = "safe"
) -> Dict[str, Any]:
    df = _read_csv(path)
    prepared = _prepare_relation_records(df)
    records = prepared["records"]

    if not records:
        return {
            "rows": 0,
            "batches": 0,
            "input_rows": prepared["input_rows"],
            "valid_rows": prepared["valid_rows"],
            "skipped_rows": prepared["skipped_rows"],
            "relation_counts": prepared["relation_counts"],
            "error_examples": prepared["error_examples"],
        }

    update_mode = (update_mode or "safe").lower()
    if update_mode not in {"safe", "overwrite"}:
        update_mode = "safe"

    with _get_driver() as driver:
        with driver.session() as session:
            batch_count = 0
            for batch in _chunked(records, batch_size):
                session.execute_write(_upsert_relation_batch, batch, update_mode)
                batch_count += 1

    return {
        "rows": len(records),
        "batches": batch_count,
        "input_rows": prepared["input_rows"],
        "valid_rows": prepared["valid_rows"],
        "skipped_rows": prepared["skipped_rows"],
        "relation_counts": prepared["relation_counts"],
        "error_examples": prepared["error_examples"],
    }

def ingest_csv(
    path: str, batch_size: int = 2000, update_mode: str = "safe"
) -> Dict[str, Any]:
    df = _read_csv(path)
    records = _df_to_records(df)
    if _is_relation_csv(_rename_columns(df)):
        return ingest_relation_csv(path, batch_size=batch_size, update_mode=update_mode)
    return ingest_records(records, batch_size=batch_size, update_mode=update_mode)


def extract_text_from_pdf(path: str) -> str:
    if PdfReader is None:
        raise RuntimeError("pypdf not installed. Please install pypdf to read PDF.")
    reader = PdfReader(path)
    text_parts: List[str] = []
    for page in reader.pages:
        text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def _split_text_blocks(text: str) -> List[str]:
    normalized = text.replace("\r", "\n")
    blocks = [b.strip() for b in re.split(r"\n{2,}", normalized) if b.strip()]
    if not blocks:
        return []
    refined: List[str] = []
    for block in blocks:
        positions = [
            m.start()
            for m in re.finditer(r"(?:\u8ba2\u5355ID|order_id)\s*[:\uFF1A]", block)
        ]
        if len(positions) <= 1:
            refined.append(block)
            continue
        positions.append(len(block))
        for i in range(len(positions) - 1):
            seg = block[positions[i] : positions[i + 1]].strip()
            if seg:
                refined.append(seg)
    return refined


def _extract_template_fields_from_segment(
    segment: str,
    label_map: Dict[str, str],
) -> Dict[str, Any]:
    normalized_segment = _unwrap_pdf_value_lines(segment)
    if not normalized_segment.strip():
        return {}

    label_pattern = "|".join(
        sorted((re.escape(label) for label in label_map.keys()), key=len, reverse=True)
    )
    matches = list(
        re.finditer(rf"(?P<label>{label_pattern})\s*[:\uFF1A]\s*", normalized_segment)
    )
    if not matches:
        return {}

    record: Dict[str, Any] = {}
    for idx, match in enumerate(matches):
        label = match.group("label")
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized_segment)
        value = normalized_segment[start:end]
        value = re.split(
            r"\n\s*(?:\d+\.\d+\s*订单案例|\d+\.\d+|[一二三四五六七八九十]+、|供应商观察：|附录说明[一二三四五六七八九十]：|文档尾注：)",
            value,
            maxsplit=1,
        )[0]
        value = re.sub(r"^[；;，,\s]+|[；;，,\s]+$", "", value)
        if value.strip():
            record[label_map[label]] = value.strip()
    return record


def _template_extract(text: str) -> List[Dict[str, Any]]:
    # Simple key-value extraction (supports Chinese/English labels).
    # Example: 订单ID: ORD-2024-100001
    text = _prepare_text_for_extraction(text)
    label_map = {
        "客户ID": "customer_id",
        "客户姓名": "customer_name",
        "客户邮箱": "customer_email",
        "客户群体": "customer_segment",
        "客户国家": "customer_country",
        "客户省份": "customer_province",
        "客户城市": "customer_city",
        "客户街道": "customer_street",
        "订单ID": "order_id",
        "订单日期": "order_date",
        "发货日期": "shipping_date",
        "预计送达日期": "scheduled_date",
        "实际送达日期": "actual_date",
        "支付类型": "payment_type",
        "订单状态": "order_status",
        "产品ID": "product_id",
        "产品SKU": "product_sku",
        "产品名称": "product_name",
        "产品描述": "product_desc",
        "产品基础价格": "product_base_price",
        "类别名称": "category_name",
        "部门名称": "department_name",
        "供应商名称": "supplier_name",
        "供应商城市": "supplier_city",
        "核心组件名称": "component_name",
        "承运商名称": "carrier_name",
        "购买数量": "quantity",
        "销售总额_应付": "gross_total",
        "折扣率": "discount_rate",
        "折扣金额": "discount_amount",
        "实付总金额": "net_total",
        "订单单笔利润": "profit",
        "利润率": "profit_ratio",
        "单件制造成本": "mfg_cost",
        "次品率": "defect_rate",
        "运输方式": "trans_mode",
        "发货模式": "ship_mode",
        "计划物流天数": "days_scheduled",
        "实际物流天数": "days_real",
        "发货延误风险_标签": "late_risk",
        "物流运输状态": "delivery_status",
        "customer_id": "customer_id",
        "order_id": "order_id",
        "supplier_name": "supplier_name",
    }


    records: List[Dict[str, Any]] = []
    for block in _split_text_blocks(text):
        record: Dict[str, Any] = {}
        fact_match = re.search(r"事实摘录\s*[:：](.*)", block, re.S)
        if fact_match:
            record = _extract_template_fields_from_segment(fact_match.group(1), label_map)
        if not record:
            record = _extract_template_fields_from_segment(block, label_map)
        if record:
            record["last_updated_time"] = _now_iso()
            records.append(_sanitize_row(record))
    return _merge_sparse_rows(records, _record_key)


def _llm_extract(text: str, partial: Optional[Any] = None) -> List[Dict[str, Any]]:
    if ChatOpenAI is None:
        raise RuntimeError("langchain_openai not installed. Cannot run LLM extraction.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set. Cannot run LLM extraction.")

    os.environ.setdefault("OPENAI_API_KEY", OPENAI_API_KEY)
    os.environ.setdefault("OPENAI_API_BASE", OPENAI_API_BASE)

    llm = ChatOpenAI(temperature=0, model=MODEL_NAME)

    allowed = ", ".join(INTERNAL_KEYS)
    partial_json = json.dumps(partial or {}, ensure_ascii=False)
    text = _prepare_text_for_extraction(text)

    prompt = (
        "You are a data extraction assistant for a supply-chain knowledge graph. "
        "Extract structured records from the text. "
        "Return ONLY valid JSON array (no markdown). "
        "Each record is an object with keys from the allowed set. "
        "Do NOT invent values; only extract what is explicitly stated. "
        "Remove formatting noise such as code fences, inline code, stray newlines, labels copied into values, and markdown bullets. "
        "Do not put JSON/code fragments into field values. "
        "For location fields, only fill province/city/country when the text explicitly states them. "
        "Do not infer province from city names. "
        "Preserve company names, product names, and carrier names as written in the text. "
        f"Allowed keys: {allowed}. "
        f"If partial data is provided, only fill missing keys when explicitly present. "
        f"Partial data: {partial_json}\n\n"
        f"Text:\n{text}"
    )

    try:
        msg = llm.invoke(prompt)
    except Exception as e:
        raise RuntimeError(
            "LLM extraction request failed. Please verify OPENAI_API_KEY, OPENAI_API_BASE, "
            "model availability, and local network access."
        ) from e
    content = getattr(msg, "content", "") or ""

    try:
        data = json.loads(content)
    except Exception:
        # Try to recover JSON array from text.
        m = re.search(r"\[.*\]", content, re.S)
        if not m:
            raise RuntimeError("LLM output is not valid JSON.")
        data = json.loads(m.group(0))

    if not isinstance(data, list):
        raise RuntimeError("LLM output is not a JSON array.")

    records: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            item.setdefault("last_updated_time", _now_iso())
            records.append(_sanitize_row(item))
    return _merge_sparse_rows(records, _record_key)


def _record_key(rec: Dict[str, Any]) -> Tuple:
    if rec.get("order_id"):
        return ("order_id", rec.get("order_id"))
    if rec.get("product_id"):
        return ("product_id", rec.get("product_id"))
    if rec.get("customer_id") or rec.get("supplier_name"):
        return ("cust_supp", rec.get("customer_id"), rec.get("supplier_name"))
    return ("row", id(rec))


def _merge_records(base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    index: Dict[Tuple, Dict[str, Any]] = {}
    for rec in base:
        index[_record_key(rec)] = rec
    for rec in extra:
        key = _record_key(rec)
        if key not in index:
            index[key] = rec
            continue
        target = index[key]
        for k, v in rec.items():
            if target.get(k) is None and v is not None:
                target[k] = v
    return list(index.values())


def extract_records_from_text(text: str, mode: str = "hybrid") -> List[Dict[str, Any]]:
    mode = (mode or "hybrid").lower()
    # First try update-intent parsing for natural language modifications
    intent_records = _try_parse_update_intent(text)
    if intent_records:
        return intent_records
    if mode == "llm_rel":
        # handled by ingest_text / ingest_pdf
        return []
    if mode == "template":
        return _template_extract(text)
    if mode == "llm":
        return _llm_extract(text)

    # hybrid: template first, then LLM for missing fields
    template_records = _template_extract(text)
    if template_records:
        try:
            llm_records = _llm_extract(text, partial=template_records)
            return _merge_sparse_rows(_merge_records(template_records, llm_records), _record_key)
        except Exception:
            return _merge_sparse_rows(template_records, _record_key)
    return _merge_sparse_rows(_llm_extract(text), _record_key)


def _build_llm_graph_prompt(text: str) -> str:
    allowed_nodes = ", ".join(sorted(set(NODE_LABEL_ALIASES.values())))
    allowed_rels = ", ".join(sorted(set(REL_TYPE_ALIASES.values())))
    text = _prepare_text_for_extraction(text)

    return (
        "You are an information extraction engine for a supply-chain knowledge graph. "
        "Extract entities and relations from the text and return ONLY valid JSON (no markdown). "
        "Schema constraints:\n"
        f"- Allowed entity types: {allowed_nodes}\n"
        f"- Allowed relation types: {allowed_rels}\n"
        "Output JSON format:\n"
        "{\n"
        "  \"entities\": [\n"
        "    {\"type\": \"Customer\", \"id\": \"CUST-...\", \"name\": \"...\", \"properties\": {\"city\": \"...\"}}\n"
        "  ],\n"
        "  \"relations\": [\n"
        "    {\"type\": \"PLACED_ORDER\", \"source\": {\"type\":\"Customer\",\"id\":\"...\"}, "
        "\"target\": {\"type\":\"Order\",\"id\":\"...\"}, \"properties\": {}}\n"
        "  ]\n"
        "}\n"
        "Rules:\n"
        "- Only use allowed types. If uncertain, omit.\n"
        "- Use id only for Customer/Order/Product, and only when the id is explicitly stated in the text.\n"
        "- Use name for Category/Department/Supplier/Component/Carrier. Do not create synthetic ids for these types.\n"
        "- Relationship schema must be exactly:\n"
        "  * PLACED_ORDER: Customer -> Order\n"
        "  * CONTAINS_PRODUCT: Order -> Product\n"
        "  * BELONGS_TO_CATEGORY: Product -> Category\n"
        "  * BELONGS_TO_DEPARTMENT: Category -> Department\n"
        "  * SUPPLIES_COMPONENT: Supplier -> Component\n"
        "  * USED_IN: Component -> Product\n"
        "  * SHIPPED_BY: Order -> Carrier\n"
        "- Do NOT output Product -> Department for BELONGS_TO_DEPARTMENT.\n"
        "- Do NOT invent values.\n"
        "- Remove formatting noise such as code fences, inline code, markdown bullets, and newline pollution from values.\n"
        "- Do not copy field labels like 'supplier_name:' or 'order_id:' into values.\n"
        "- For customer location properties, only include province/city/country when they are explicitly stated. Do not infer province from city names.\n"
        "- When the text explicitly provides entity properties, include them:\n"
        "  * Customer.properties: city, country, province, street, segment, email\n"
        "  * Order.properties: status, payment_type, order_date, shipping_date, scheduled_date, actual_date\n"
        "  * Product.properties: sku, description, base_price\n"
        "  * Supplier.properties: city\n"
        "- When the text explicitly provides relation properties, include them instead of leaving an empty object:\n"
        "  * CONTAINS_PRODUCT.properties: quantity, gross_total, discount_rate, discount_amount, net_total, profit, profit_ratio\n"
        "  * SUPPLIES_COMPONENT.properties: mfg_cost, defect_rate\n"
        "  * SHIPPED_BY.properties: trans_mode, ship_mode, days_scheduled, days_real, late_risk, delivery_status\n"
        "- Prefer preserving numeric values exactly as written in the text.\n"
        "- Deduplicate repeated entities and repeated relations.\n"
        "- Keep the output concise and valid. Prefer the most important entities and relations only.\n"
        "- Return at most 80 entities and 120 relations.\n"
        f"Text:\n{text}"
    )


def _llm_extract_graph_raw_response(text: str) -> str:
    if ChatOpenAI is None:
        raise RuntimeError("langchain_openai not installed. Cannot run LLM extraction.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set. Cannot run LLM extraction.")

    os.environ.setdefault("OPENAI_API_KEY", OPENAI_API_KEY)
    os.environ.setdefault("OPENAI_API_BASE", OPENAI_API_BASE)

    llm = ChatOpenAI(temperature=0, model=MODEL_NAME)
    prompt = _build_llm_graph_prompt(text)

    try:
        msg = llm.invoke(prompt)
    except Exception as e:
        raise RuntimeError(
            "LLM graph extraction request failed. Please verify OPENAI_API_KEY, OPENAI_API_BASE, "
            "model availability, and local network access."
        ) from e
    return getattr(msg, "content", "") or ""


def _parse_llm_graph_response(raw_content: str) -> Dict[str, List[Dict[str, Any]]]:
    content = _strip_markdown_fences(raw_content)
    llm = ChatOpenAI(temperature=0, model=MODEL_NAME)

    try:
        data = json.loads(content)
    except Exception:
        candidate = _extract_balanced_json_object(content)
        if candidate is not None:
            try:
                data = json.loads(candidate)
            except Exception:
                data = _repair_json_with_llm(content, llm)
        else:
            data = _repair_json_with_llm(content, llm)

    if not isinstance(data, dict):
        raise RuntimeError("LLM output is not a JSON object.")
    entities = data.get("entities") or []
    relations = data.get("relations") or []
    if not isinstance(entities, list) or not isinstance(relations, list):
        raise RuntimeError("LLM output missing entities/relations lists.")
    return {"entities": entities, "relations": relations}


def _merge_graph_payloads(payloads: List[Dict[str, List[Dict[str, Any]]]]) -> Dict[str, List[Dict[str, Any]]]:
    merged_entities: List[Dict[str, Any]] = []
    merged_relations: List[Dict[str, Any]] = []
    for payload in payloads:
        entities = payload.get("entities") or []
        relations = payload.get("relations") or []
        for ent in entities:
            if isinstance(ent, dict):
                merged_entities.append(ent)
        for rel in relations:
            if isinstance(rel, dict):
                merged_relations.append(rel)
    return {"entities": merged_entities, "relations": merged_relations}


def _llm_extract_graph(text: str) -> Dict[str, List[Dict[str, Any]]]:
    prepared = _prepare_text_for_extraction(text)
    if len(prepared) <= LONG_TEXT_CHUNK_TRIGGER:
        raw_content = _llm_extract_graph_raw_response(prepared)
        return _parse_llm_graph_response(raw_content)

    chunks = _split_long_text_for_llm(
        prepared,
        max_chars=LONG_TEXT_CHUNK_CHARS,
        overlap_chars=LONG_TEXT_CHUNK_OVERLAP,
    )
    payloads: List[Dict[str, List[Dict[str, Any]]]] = []
    for chunk in chunks:
        raw_content = _llm_extract_graph_raw_response(chunk)
        payloads.append(_parse_llm_graph_response(raw_content))
    return _merge_graph_payloads(payloads)


def _normalize_entity_record(ent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    etype = ent.get("type")
    if not etype:
        return None
    etype = NODE_LABEL_ALIASES.get(str(etype).strip(), str(etype).strip())
    props = ent.get("properties") or {}
    if not isinstance(props, dict):
        props = {}

    record: Dict[str, Any] = {"last_updated_time": _now_iso()}

    if etype == "Customer":
        cid = _clean_identifier_field("customer_id", ent.get("id"))
        if not cid:
            return None
        record["customer_id"] = cid
        record["customer_name"] = ent.get("name") or props.get("name")
        record["customer_email"] = props.get("email")
        record["customer_segment"] = props.get("segment")
        record["customer_country"] = props.get("country")
        record["customer_province"] = props.get("province")
        record["customer_city"] = props.get("city")
        record["customer_street"] = props.get("street")
        record["customer_lat"] = props.get("lat")
        record["customer_lon"] = props.get("lon")
    elif etype == "Order":
        oid = _clean_identifier_field("order_id", ent.get("id"))
        if not oid:
            return None
        record["order_id"] = oid
        record["order_status"] = ent.get("status") or props.get("status")
        record["payment_type"] = props.get("payment_type")
        record["order_date"] = props.get("order_date")
        record["shipping_date"] = props.get("shipping_date")
        record["scheduled_date"] = props.get("scheduled_date")
        record["actual_date"] = props.get("actual_date")
    elif etype == "Product":
        pid = _clean_identifier_field("product_id", ent.get("id"))
        if not pid:
            return None
        record["product_id"] = pid
        record["product_sku"] = ent.get("sku") or props.get("sku")
        record["product_name"] = ent.get("name") or props.get("name")
        record["product_desc"] = props.get("desc") or props.get("description")
        record["product_base_price"] = props.get("base_price")
    elif etype == "Category":
        name = _clean_text_field("category_name", ent.get("name") or props.get("name"))
        if not name:
            return None
        record["category_name"] = name
    elif etype == "Department":
        name = _clean_text_field("department_name", ent.get("name") or props.get("name"))
        if not name:
            return None
        record["department_name"] = name
    elif etype == "Supplier":
        name = _clean_text_field("supplier_name", ent.get("name") or props.get("name"))
        if not name:
            return None
        record["supplier_name"] = name
        record["supplier_city"] = props.get("city")
    elif etype == "Component":
        name = _clean_text_field("component_name", ent.get("name") or props.get("name"))
        if not name:
            return None
        record["component_name"] = name
    elif etype == "Carrier":
        name = _clean_text_field("carrier_name", ent.get("name") or props.get("name"))
        if not name:
            return None
        record["carrier_name"] = name
    else:
        return None

    return _sanitize_row(record)


def _normalize_relation_record(rel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rtype = rel.get("type")
    if not rtype:
        return None
    rtype = REL_TYPE_ALIASES.get(str(rtype).strip(), str(rtype).strip())
    src = rel.get("source") or {}
    dst = rel.get("target") or {}
    if not isinstance(src, dict) or not isinstance(dst, dict):
        return None
    src_type = NODE_LABEL_ALIASES.get(str(src.get("type", "")).strip(), str(src.get("type", "")).strip())
    dst_type = NODE_LABEL_ALIASES.get(str(dst.get("type", "")).strip(), str(dst.get("type", "")).strip())
    if not src_type or not dst_type:
        return None
    expected_schema = RELATION_SCHEMA.get(rtype)
    if expected_schema and expected_schema != (src_type, dst_type):
        return None
    row: Dict[str, Any] = {
        "src_type": src_type,
        "dst_type": dst_type,
        "rel_type": rtype,
        "src_id": src.get("id"),
        "dst_id": dst.get("id"),
        "src_name": src.get("name"),
        "dst_name": dst.get("name"),
    }

    props = rel.get("properties") or {}
    if isinstance(props, dict):
        row.update(props)

    # Normalize types and numeric fields using existing helpers
    row = _normalize_relation_row(row)
    row = _sanitize_row(row)  # reuse numeric coercion for shared keys
    if not (_node_ref_complete(row, "src") and _node_ref_complete(row, "dst")):
        return None
    return row


def ingest_text_graph(text: str, batch_size: int = 2000, update_mode: str = "safe") -> Dict[str, Any]:
    prepared_text = _prepare_text_for_extraction(text)
    text_chunks = _split_long_text_for_llm(
        prepared_text,
        max_chars=LONG_TEXT_CHUNK_CHARS,
        overlap_chars=LONG_TEXT_CHUNK_OVERLAP,
    )
    graph = _llm_extract_graph(text)
    entities = graph.get("entities") or []
    relations = graph.get("relations") or []
    raw_entity_count = len([ent for ent in entities if isinstance(ent, dict)])
    raw_relation_count = len([rel for rel in relations if isinstance(rel, dict)])

    entity_records: List[Dict[str, Any]] = []
    for ent in entities:
        if isinstance(ent, dict):
            rec = _normalize_entity_record(ent)
            if rec:
                entity_records.append(rec)
    normalized_entity_count = len(entity_records)
    entity_records = _merge_sparse_rows(entity_records, _entity_record_key)

    relation_records: List[Dict[str, Any]] = []
    for rel in relations:
        if isinstance(rel, dict):
            rec = _normalize_relation_record(rel)
            if rec:
                relation_records.append(rec)
    normalized_relation_count = len(relation_records)
    relation_records = _merge_sparse_rows(relation_records, _relation_record_key)

    total_rows = 0
    total_batches = 0

    if entity_records:
        res = ingest_records(entity_records, batch_size=batch_size, update_mode=update_mode)
        total_rows += res.get("rows", 0)
        total_batches += res.get("batches", 0)

    if relation_records:
        update_mode = (update_mode or "safe").lower()
        if update_mode not in {"safe", "overwrite"}:
            update_mode = "safe"
        with _get_driver() as driver:
            with driver.session() as session:
                batch_count = 0
                for batch in _chunked(relation_records, batch_size):
                    session.execute_write(_upsert_relation_batch, batch, update_mode)
                    batch_count += 1
        total_rows += len(relation_records)
        total_batches += batch_count

    return {
        "rows": total_rows,
        "batches": total_batches,
        "text_length": len(prepared_text),
        "chunk_count": len(text_chunks),
        "raw_entities": raw_entity_count,
        "raw_relations": raw_relation_count,
        "normalized_entities": normalized_entity_count,
        "normalized_relations": normalized_relation_count,
        "deduped_entities": len(entity_records),
        "deduped_relations": len(relation_records),
    }


def preview_relation_csv(path: str, sample_size: int = 20) -> Dict[str, Any]:
    """Preview relation CSV normalization and validation without writing to Neo4j."""
    df = _read_csv(path)
    renamed = _rename_columns(df)
    prepared = _prepare_relation_records(df)
    sample_size = max(1, int(sample_size or 20))
    sample_records = prepared["records"][:sample_size]
    return {
        "path": path,
        "is_relation_csv": _is_relation_csv(renamed),
        "input_rows": prepared["input_rows"],
        "valid_rows": prepared["valid_rows"],
        "skipped_rows": prepared["skipped_rows"],
        "relation_counts": prepared["relation_counts"],
        "sample_records": sample_records,
        "error_examples": prepared["error_examples"],
    }


def preview_text_extraction(
    text: str,
    mode: str = "hybrid",
    text_preview_chars: int = 2000,
) -> Dict[str, Any]:
    """Preview extracted data without writing anything to Neo4j."""
    normalized_mode = (mode or "hybrid").lower()
    prepared_text = _prepare_text_for_extraction(text)
    text_preview = prepared_text[: max(0, text_preview_chars)]

    if normalized_mode == "llm_rel":
        chunks = _split_long_text_for_llm(
            prepared_text,
            max_chars=LONG_TEXT_CHUNK_CHARS,
            overlap_chars=LONG_TEXT_CHUNK_OVERLAP,
        )
        chunk_previews = chunks[:3]
        raw_llm_output = _llm_extract_graph_raw_response(chunks[0] if chunks else prepared_text)
        return {
            "mode": normalized_mode,
            "text_preview": text_preview,
            "text_length": len(prepared_text),
            "chunk_count": len(chunks),
            "chunk_preview_lengths": [len(chunk) for chunk in chunk_previews],
            "raw_llm_output": raw_llm_output,
        }

    records = extract_records_from_text(prepared_text, mode=normalized_mode)
    return {
        "mode": normalized_mode,
        "text_preview": text_preview,
        "text_length": len(prepared_text),
        "record_count": len(records),
        "records": records,
    }


def preview_pdf_extraction(
    path: str,
    mode: str = "hybrid",
    text_preview_chars: int = 2000,
) -> Dict[str, Any]:
    """Preview PDF extraction without writing anything to Neo4j."""
    pdf_path = os.path.abspath(path)
    text = extract_text_from_pdf(pdf_path)
    result = preview_text_extraction(
        text=text,
        mode=mode,
        text_preview_chars=text_preview_chars,
    )
    result["pdf_path"] = pdf_path
    return result

def ingest_pdf(
    path: str,
    mode: str = "hybrid",
    batch_size: int = 2000,
    update_mode: str = "safe",
) -> Dict[str, Any]:
    text = extract_text_from_pdf(path)
    if (mode or "").lower() == "llm_rel":
        return ingest_text_graph(text, batch_size=batch_size, update_mode=update_mode)
    records = extract_records_from_text(text, mode=mode)
    return ingest_records(records, batch_size=batch_size, update_mode=update_mode)


def ingest_text(
    text: str,
    mode: str = "hybrid",
    batch_size: int = 2000,
    update_mode: str = "safe",
) -> Dict[str, Any]:
    if (mode or "").lower() == "llm_rel":
        return ingest_text_graph(text, batch_size=batch_size, update_mode=update_mode)
    records = extract_records_from_text(text, mode=mode)
    return ingest_records(records, batch_size=batch_size, update_mode=update_mode)
