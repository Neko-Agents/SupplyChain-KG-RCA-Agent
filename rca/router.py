import re
from typing import List

from .schemas import RCAIntent


_KW_WHY = [
    "\u4e3a\u4ec0\u4e48",
    "\u4e3a\u4f55",
    "\u539f\u56e0",
    "\u6839\u56e0",
    "\u5f02\u5e38",
    "\u51fa\u95ee\u9898",
    "\u98ce\u9669\u9ad8",
    "\u74f6\u9888",
    "\u665a\u5230",
    "\u5ef6\u8fdf",
    "why",
    "root cause",
    "rca",
]
_KW_ORDER = ["\u8ba2\u5355", "order"]
_KW_SUPPLIER = ["\u4f9b\u5e94\u5546", "supplier"]
_KW_CARRIER = ["\u627f\u8fd0\u5546", "\u7269\u6d41", "carrier"]
_KW_PRODUCT = ["\u4ea7\u54c1", "\u5546\u54c1", "product"]
_STOP_TOKENS = [
    "\u4e3a\u4ec0\u4e48",
    "\u4e3a\u4f55",
    "\u539f\u56e0",
    "\u6839\u56e0",
    "\u98ce\u9669",
    "\u5f02\u5e38",
    "\u5ef6\u8fdf",
    "\u665a\u5230",
    "\u74f6\u9888",
    "\u662f\u4ec0\u4e48",
    "why",
    "root cause",
    "rca",
]
_ORDER_PRONOUNS = ["\u8fd9\u4e2a\u8ba2\u5355", "\u8be5\u8ba2\u5355", "this order"]
_SUPPLIER_PRONOUNS = ["\u8fd9\u4e2a\u4f9b\u5e94\u5546", "\u8be5\u4f9b\u5e94\u5546", "this supplier"]
_CARRIER_PRONOUNS = ["\u8fd9\u4e2a\u627f\u8fd0\u5546", "\u8be5\u627f\u8fd0\u5546", "this carrier"]
_PRODUCT_PRONOUNS = ["\u8fd9\u4e2a\u4ea7\u54c1", "\u8be5\u4ea7\u54c1", "this product"]


def _contains_any(text: str, keywords: List[str]) -> bool:
    lowered = (text or "").lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _contains_rca_language(text: str) -> bool:
    return _contains_any(text, _KW_WHY)


def _extract_order_id(text: str) -> str:
    match = re.search(r"\bORD-\d{4}-\d+\b", text or "", re.I)
    return match.group(0) if match else ""


def _extract_quoted(text: str) -> str:
    match = re.search(r"[\"'“”‘’]([^\"'“”‘’]{2,60})[\"'“”‘’]", text or "")
    return match.group(1).strip() if match else ""


def _clean_entity(candidate: str) -> str:
    candidate = re.split(r"[，。,；;？?\n]", candidate or "")[0].strip(" :：")
    for token in _STOP_TOKENS:
        idx = candidate.lower().find(token.lower())
        if idx > 0:
            candidate = candidate[:idx].strip()
    candidate = re.sub(r"\s+", " ", candidate).strip()
    return candidate


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


def _build_intent(
    subtype: str,
    target_type: str,
    target_id: str,
    confidence: float,
    reasons: List[str],
) -> RCAIntent:
    return RCAIntent(
        route_type="rca",
        subtype=subtype,
        target_type=target_type,
        target_id=target_id,
        confidence=confidence,
        reasons=reasons,
    )


def classify_intent(question: str, history_text: str = "") -> RCAIntent:
    question = (question or "").strip()
    history_text = (history_text or "").strip()
    if not _contains_rca_language(question):
        return RCAIntent(route_type="general")

    reasons: List[str] = ["rca_language"]
    question_lower = question.lower()

    current_order_id = _extract_order_id(question)
    current_supplier = _extract_named_entity(question, _KW_SUPPLIER) if _contains_any(question, _KW_SUPPLIER) else ""
    current_carrier = _extract_named_entity(question, _KW_CARRIER) if _contains_any(question, _KW_CARRIER) else ""
    current_product = _extract_named_entity(question, _KW_PRODUCT) if _contains_any(question, _KW_PRODUCT) else ""

    if current_supplier:
        return _build_intent(
            "supplier_risk",
            "Supplier",
            current_supplier,
            0.95,
            reasons + ["supplier_in_question"],
        )

    if current_carrier:
        return _build_intent(
            "carrier_delay",
            "Carrier",
            current_carrier,
            0.93,
            reasons + ["carrier_in_question"],
        )

    if current_product:
        return _build_intent(
            "product_impact",
            "Product",
            current_product,
            0.9,
            reasons + ["product_in_question"],
        )

    if current_order_id:
        return _build_intent(
            "order_delay",
            "Order",
            current_order_id,
            0.98,
            reasons + ["order_id_in_question"],
        )

    history_order_id = _extract_order_id(history_text)
    if history_order_id and (_contains_any(question, _ORDER_PRONOUNS) or _contains_any(question, _KW_ORDER)):
        return _build_intent(
            "order_delay",
            "Order",
            history_order_id,
            0.8,
            reasons + ["order_from_history"],
        )

    if _contains_any(question, _SUPPLIER_PRONOUNS):
        history_supplier = _extract_named_entity(history_text, _KW_SUPPLIER)
        if history_supplier:
            return _build_intent(
                "supplier_risk",
                "Supplier",
                history_supplier,
                0.75,
                reasons + ["supplier_from_history"],
            )

    if _contains_any(question, _CARRIER_PRONOUNS):
        history_carrier = _extract_named_entity(history_text, _KW_CARRIER)
        if history_carrier:
            return _build_intent(
                "carrier_delay",
                "Carrier",
                history_carrier,
                0.74,
                reasons + ["carrier_from_history"],
            )

    if _contains_any(question, _PRODUCT_PRONOUNS):
        history_product = _extract_named_entity(history_text, _KW_PRODUCT)
        if history_product:
            return _build_intent(
                "product_impact",
                "Product",
                history_product,
                0.72,
                reasons + ["product_from_history"],
            )

    return RCAIntent(route_type="general")
