from typing import Any, Dict, List


_SUSPICIOUS_TOKENS = [
    "\u951b",
    "\u9286",
    "\u95c2",
    "\u20ac",
    "\ufffd",
]


def _format_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "0.00"


def _format_score(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return "0.00"


def _sanitize_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""
    cleaned = cleaned.replace("```markdown", "").replace("```", "")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return "\n".join(line.rstrip() for line in cleaned.split("\n")).strip()


def _looks_noisy(text: str) -> bool:
    if not text:
        return True
    if any(token in text for token in _SUSPICIOUS_TOKENS):
        return True
    if text.count("{") + text.count("}") > 10:
        return True
    return False


def _summarize_context(anomaly: Dict[str, Any], evidence: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    validation = evidence.get("validation", {})
    context = evidence.get("context") or evidence.get("overview") or {}
    anomaly_type = anomaly.get("type", "")

    if anomaly_type == "order_delay":
        if validation.get("delay_days") is not None:
            lines.append(
                f"\u5df2\u786e\u8ba4\u8ba2\u5355\u5b58\u5728\u5ef6\u8fdf\uff0c\u5e73\u5747\u5ef6\u8fdf\u7ea6 {validation.get('delay_days')} \u5929\u3002"
            )
        products = context.get("products") or []
        carriers = context.get("carriers") or []
        if products:
            lines.append(
                f"\u5173\u8054\u4ea7\u54c1\uff1a{', '.join(products[:4])}\u3002"
            )
        if carriers:
            lines.append(
                f"\u6d89\u53ca\u627f\u8fd0\u5546\uff1a{', '.join(carriers[:3])}\u3002"
            )
    elif anomaly_type == "supplier_risk":
        supplier = validation.get("supplier") or context.get("supplier") or anomaly.get("target_id")
        lines.append(f"\u5df2\u5b9a\u4f4d\u76ee\u6807\u4f9b\u5e94\u5546\uff1a{supplier}\u3002")
        lines.append(
            f"\u5173\u8054\u5229\u6da6\u66b4\u9732\u7ea6 {_format_money(validation.get('profit_exposure'))}\uff0c\u5e73\u5747\u6b21\u54c1\u7387 {validation.get('avg_defect_rate', 0)}\u3002"
        )
    elif anomaly_type == "carrier_delay":
        carrier = validation.get("carrier") or context.get("carrier") or anomaly.get("target_id")
        lines.append(f"\u5df2\u5b9a\u4f4d\u76ee\u6807\u627f\u8fd0\u5546\uff1a{carrier}\u3002")
        lines.append(
            f"\u5ef6\u8fdf\u8ba2\u5355\u6570 {validation.get('delayed_orders', 0)}\uff0c\u5e73\u5747\u8d85\u671f\u7ea6 {validation.get('avg_delay_days', 0)} \u5929\u3002"
        )
    elif anomaly_type == "product_impact":
        product = validation.get("product") or context.get("product") or anomaly.get("target_id")
        lines.append(f"\u5df2\u5b9a\u4f4d\u76ee\u6807\u4ea7\u54c1\uff1a{product}\u3002")
        lines.append(
            f"\u76f8\u5173\u5229\u6da6\u66b4\u9732\u7ea6 {_format_money(validation.get('profit'))}\uff0c\u5ef6\u8fdf\u8ba2\u5355\u6570 {validation.get('late_orders', 0)}\u3002"
        )
    return lines


def _build_deterministic_report(
    anomaly: Dict[str, Any],
    evidence: Dict[str, Any],
    candidate_causes: List[Dict[str, Any]],
    recommended_actions: List[str],
) -> str:
    validation = evidence.get("validation", {})
    lines: List[str] = []

    lines.append("**\u5f02\u5e38\u786e\u8ba4**")
    if validation.get("is_anomaly"):
        lines.append("\u5df2\u786e\u8ba4\u5f53\u524d\u5bf9\u8c61\u5b58\u5728\u5f02\u5e38\uff0c\u5efa\u8bae\u7ee7\u7eed\u8fdb\u884c\u6839\u56e0\u5904\u7f6e\u3002")
    else:
        lines.append("\u5f53\u524d\u8bc1\u636e\u4e0d\u8db3\u4ee5\u5b8c\u5168\u786e\u8ba4\u5f02\u5e38\uff0c\u4f46\u4ecd\u53ef\u53c2\u8003\u5df2\u6709\u8bc1\u636e\u8fdb\u884c\u6392\u67e5\u3002")
    lines.extend(f"- {item}" for item in _summarize_context(anomaly, evidence))

    lines.append("")
    lines.append("**\u6839\u56e0\u5224\u65ad**")
    if not candidate_causes:
        lines.append("- \u6682\u672a\u5f62\u6210\u7a33\u5b9a\u7684\u5019\u9009\u6839\u56e0\uff0c\u5efa\u8bae\u8865\u5145\u66f4\u591a\u4e0a\u4e0b\u6e38\u8bc1\u636e\u3002")
    else:
        for index, cause in enumerate(candidate_causes[:3], start=1):
            lines.append(
                f"{index}. {cause.get('label', cause.get('cause'))}\uff0c\u53ef\u4fe1\u5ea6 {_format_score(cause.get('score'))}\u3002"
            )
            if cause.get("explanation_hint"):
                lines.append(f"   {cause.get('explanation_hint')}")
            for evidence_item in cause.get("evidence", [])[:3]:
                lines.append(f"   \u8bc1\u636e\uff1a{evidence_item}")

    lines.append("")
    lines.append("**\u5904\u7f6e\u5efa\u8bae**")
    if not recommended_actions:
        lines.append("- \u6682\u65e0\u660e\u786e\u52a8\u4f5c\u5efa\u8bae\uff0c\u5efa\u8bae\u5148\u8865\u5145\u4f9b\u5e94\u3001\u7269\u6d41\u548c\u8ba2\u5355\u5c65\u7ea6\u8bc1\u636e\u3002")
    else:
        for action in recommended_actions[:5]:
            lines.append(f"- {action}")

    return "\n".join(lines).strip()


def render_report(
    llm: Any,
    anomaly: Dict[str, Any],
    evidence: Dict[str, Any],
    candidate_causes: List[Dict[str, Any]],
    recommended_actions: List[str],
    history_text: str = "",
) -> str:
    deterministic = _build_deterministic_report(
        anomaly=anomaly,
        evidence=evidence,
        candidate_causes=candidate_causes,
        recommended_actions=recommended_actions,
    )
    if not llm:
        return deterministic
    prompt = f"""你是供应链根因分析助手。请基于结构化结果生成一份更自然、更有条理的中文 RCA 报告。

要求：
1. 只能使用已提供的证据，不要虚构数据。
2. 必须保留三个小节标题：异常确认、根因判断、处置建议。
3. 根因判断部分优先解释前 2-3 个最可能原因，并说明它们为什么比其他原因更重要。
4. 处置建议要和根因一一对应，避免泛泛而谈。
5. 输出不要出现 JSON、花括号、代码块或多余符号。
6. 语言要自然，但保持专业。

对话上下文：
{history_text or "无"}

结构化 RCA 结果：
{deterministic}
"""
    try:
        message = llm.invoke(prompt)
        content = _sanitize_text(getattr(message, "content", "") or "")
        if content and not _looks_noisy(content):
            return content
    except Exception:
        pass
    return deterministic
