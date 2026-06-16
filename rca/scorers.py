from typing import Any, Dict, List

from .templates import get_cause_templates, merge_actions


def _score(value: float) -> float:
    return round(max(0.0, min(value, 0.99)), 2)


def _topology(evidence: Dict[str, Any]) -> Dict[str, Any]:
    return evidence.get("graph_metrics") or {}


def _metric(metrics: Dict[str, Any], key: str) -> float:
    try:
        return float(metrics.get(key) or 0)
    except Exception:
        return 0.0


def _event_summary(evidence: Dict[str, Any]) -> Dict[str, Any]:
    return evidence.get("event_summary") or {}


def _event_metric(evidence: Dict[str, Any], key: str) -> float:
    try:
        return float((_event_summary(evidence).get(key) or 0))
    except Exception:
        return 0.0


def _direct_evidence(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(evidence.get("direct_evidence") or [])


def _event_chains(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(evidence.get("event_chain") or [])


def _support_bundle(evidence: Dict[str, Any], event_types: List[str]) -> Dict[str, Any]:
    direct = [
        item
        for item in _direct_evidence(evidence)
        if str(item.get("event_type") or "") in set(event_types)
    ]
    supporting_nodes: List[Dict[str, Any]] = []
    supporting_edges: List[str] = []
    source_records: List[Dict[str, Any]] = []
    seen_nodes: set[tuple[str, str]] = set()
    seen_edges: set[str] = set()
    seen_sources: set[str] = set()

    for item in direct[:4]:
        for node in item.get("supporting_nodes") or []:
            label = str(node.get("label") or "")
            value = str(node.get("value") or "")
            marker = (label, value)
            if not label or not value or marker in seen_nodes:
                continue
            seen_nodes.add(marker)
            supporting_nodes.append({"label": label, "value": value})
        for edge in item.get("supporting_edges") or []:
            edge_text = str(edge or "")
            if not edge_text or edge_text in seen_edges:
                continue
            seen_edges.add(edge_text)
            supporting_edges.append(edge_text)
        for record in item.get("source_records") or []:
            source_id = str(record.get("id") or "")
            marker = source_id or str(record)
            if marker in seen_sources:
                continue
            seen_sources.add(marker)
            source_records.append(record)

    return {
        "supporting_nodes": supporting_nodes[:8],
        "supporting_edges": supporting_edges[:8],
        "source_records": source_records[:8],
        "evidence_chain": [dict(item) for item in _event_chains(evidence)[:2]],
    }


def _mk_cause(
    anomaly_type: str,
    cause_key: str,
    score: float,
    evidence_lines: List[str],
    support_bundle: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    template = get_cause_templates(anomaly_type).get(cause_key, {})
    support_bundle = support_bundle or {}
    return {
        "cause": cause_key,
        "label": template.get("label", cause_key),
        "score": _score(score),
        "evidence": evidence_lines,
        "explanation_hint": template.get("explanation_hint", ""),
        "suggested_actions": template.get("actions", []),
        "supporting_nodes": support_bundle.get("supporting_nodes", []),
        "supporting_edges": support_bundle.get("supporting_edges", []),
        "evidence_chain": support_bundle.get("evidence_chain", []),
        "source_records": support_bundle.get("source_records", []),
        "evidence_mode": "direct" if support_bundle.get("evidence_chain") else "structural",
    }


def _score_order_delay(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    causes: List[Dict[str, Any]] = []
    metrics = _topology(evidence)
    single_rows = evidence.get("single_source_components", [])
    supplier_rows = evidence.get("supplier_exposure", [])
    carrier = evidence.get("carrier_context", {})
    shared_rows = evidence.get("shared_components", [])
    fail_inspections = _event_metric(evidence, "fail_inspection_count")
    notice_count = _event_metric(evidence, "supplier_notice_count")
    delay_event_count = _event_metric(evidence, "delay_event_count")
    max_delay_hours = _event_metric(evidence, "max_delay_hours")

    if single_rows:
        top = single_rows[0]
        score = 0.58 + min(0.25, 0.07 * len(single_rows)) + min(
            0.12, 0.01 * float(top.get("affected_orders") or 0)
        )
        score += min(0.08, 0.03 * _metric(metrics, "single_source_components"))
        score += min(0.08, 0.02 * _metric(metrics, "max_component_product_span"))
        score += min(0.1, 0.05 * fail_inspections) + min(0.08, 0.03 * notice_count)
        evidence_lines = [
            f"组件 {top.get('component')} 仅由 {top.get('sole_supplier')} 供应",
            f"证据子图识别到 {_metric(metrics, 'single_source_components'):.0f} 个单一来源组件",
        ]
        if fail_inspections > 0 or notice_count > 0:
            evidence_lines.append(
                f"同链路上还有 {int(notice_count)} 条供应商预警、{int(fail_inspections)} 条失败抽检作为直接事件证据"
            )
        causes.append(
            _mk_cause(
                "order_delay",
                "single_source_dependency",
                score,
                evidence_lines,
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection"]),
            )
        )

    supplier_candidates = [
        row for row in supplier_rows if float(row.get("delayed_orders_touched") or 0) > 0
    ]
    if supplier_candidates or fail_inspections > 0 or notice_count > 0:
        top = supplier_candidates[0] if supplier_candidates else {}
        score = 0.42 + min(0.25, 0.03 * float(top.get("delayed_orders_touched") or 0))
        score += min(0.08, 0.02 * _metric(metrics, "supplier_count"))
        score += min(0.08, 0.02 * _metric(metrics, "max_supplier_component_span"))
        score += min(0.12, 0.05 * fail_inspections) + min(0.08, 0.03 * notice_count)
        evidence_lines = []
        if top:
            evidence_lines.extend(
                [
                    f"供应商 {top.get('supplier')} 触达延迟订单 {int(top.get('delayed_orders_touched') or 0)} 个",
                    f"利润暴露 {round(float(top.get('profit_exposure') or 0), 2)}",
                ]
            )
        if fail_inspections > 0 or notice_count > 0:
            evidence_lines.append(
                f"订单侧检测到 {int(notice_count)} 条供应商预警与 {int(fail_inspections)} 条失败抽检"
            )
        causes.append(
            _mk_cause(
                "order_delay",
                "supplier_quality_issue",
                score,
                evidence_lines or ["存在供应商质量异常的直接事件信号"],
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection"]),
            )
        )

    delayed_orders = float(carrier.get("delayed_orders") or 0)
    if delayed_orders > 0 or delay_event_count > 0:
        score = 0.38 + min(0.28, delayed_orders * 0.01) + min(
            0.12, max(0.0, float(carrier.get("avg_delay_days") or 0)) * 0.03
        )
        score += min(0.10, 0.02 * _metric(metrics, "max_carrier_order_span"))
        score += min(0.14, 0.04 * delay_event_count) + min(0.08, max_delay_hours / 48.0 * 0.03)
        evidence_lines = []
        if carrier:
            evidence_lines.extend(
                [
                    f"承运商 {carrier.get('carrier')} 历史延迟订单 {int(delayed_orders)} 个",
                    f"平均超期 {round(float(carrier.get('avg_delay_days') or 0), 2)} 天",
                ]
            )
        if delay_event_count > 0:
            evidence_lines.append(
                f"订单本身已有 {int(delay_event_count)} 条延迟事件，最长 {round(max_delay_hours, 1)} 小时"
            )
        causes.append(
            _mk_cause(
                "order_delay",
                "carrier_delay",
                score,
                evidence_lines or ["存在直接延迟事件，运输环节是强候选因素"],
                _support_bundle(evidence, ["DelayEvent"]),
            )
        )

    shared_candidates = [
        row for row in shared_rows if float(row.get("used_in_products") or 0) >= 2
    ]
    if shared_candidates:
        top = shared_candidates[0]
        score = 0.35 + min(0.26, 0.08 * (float(top.get("used_in_products") or 0) - 1))
        score += min(0.10, 0.03 * _metric(metrics, "max_component_product_span"))
        score += min(0.08, 0.03 * fail_inspections)
        causes.append(
            _mk_cause(
                "order_delay",
                "component_concentration",
                score,
                [
                    f"组件 {top.get('component')} 被 {int(top.get('used_in_products') or 0)} 个产品复用",
                    f"证据子图的组件最大复用跨度为 {_metric(metrics, 'max_component_product_span'):.0f}",
                ],
                _support_bundle(evidence, ["QualityInspection"]),
            )
        )

    if not causes:
        causes.append(
            _mk_cause(
                "order_delay",
                "carrier_delay",
                0.28,
                ["当前已确认订单存在异常，但显著根因证据仍然不足"],
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection", "DelayEvent"]),
            )
        )
    return sorted(causes, key=lambda item: item["score"], reverse=True)


def _score_supplier_risk(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    causes: List[Dict[str, Any]] = []
    metrics = _topology(evidence)
    overview = evidence.get("overview", {})
    avg_defect = float(overview.get("avg_defect_rate") or 0)
    avg_cost = float(overview.get("avg_mfg_cost") or 0)
    single_rows = evidence.get("single_source_components", [])
    delay_rows = evidence.get("delay_mix", [])
    replacement_gap = evidence.get("replacement_gap", [])
    share_rows = evidence.get("component_share", [])
    product_rows = evidence.get("product_concentration", [])
    fail_inspections = _event_metric(evidence, "fail_inspection_count")
    notice_count = _event_metric(evidence, "supplier_notice_count")
    delay_event_count = _event_metric(evidence, "delay_event_count")

    if avg_defect > 0 or fail_inspections > 0 or notice_count > 0:
        high_defect_components = [
            row for row in single_rows if float(row.get("defect_rate") or 0) >= avg_defect
        ]
        score = 0.25 + min(0.45, avg_defect * 20) + min(0.15, 0.03 * len(high_defect_components))
        score += min(0.08, 0.01 * _metric(metrics, "component_count"))
        score += min(0.08, 0.01 * _metric(metrics, "product_count"))
        score += min(0.14, 0.05 * fail_inspections) + min(0.08, 0.03 * notice_count)
        evidence_lines = [f"平均缺陷率 {round(avg_defect, 4)}"]
        if high_defect_components:
            evidence_lines.append(f"高缺陷组件 {len(high_defect_components)} 个")
        if fail_inspections > 0 or notice_count > 0:
            evidence_lines.append(f"事件层记录到 {int(notice_count)} 条预警、{int(fail_inspections)} 条失败抽检")
        causes.append(
            _mk_cause(
                "supplier_risk",
                "quality_instability",
                score,
                evidence_lines,
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection"]),
            )
        )

    if single_rows or share_rows:
        top_single = single_rows[0] if single_rows else {}
        top_shared = share_rows[0] if share_rows else {}
        score = 0.42 + min(0.22, 0.05 * len(single_rows)) + min(
            0.15, 0.03 * float(top_shared.get("shared_products") or 0)
        )
        score += min(0.10, 0.03 * _metric(metrics, "single_source_components"))
        score += min(0.10, 0.02 * _metric(metrics, "max_component_product_span"))
        evidence_lines: List[str] = []
        if single_rows:
            evidence_lines.append(f"单一来源组件 {len(single_rows)} 个")
            evidence_lines.append(f"最高暴露组件 {top_single.get('component')}")
        if top_shared:
            evidence_lines.append(f"组件 {top_shared.get('component')} 覆盖 {int(top_shared.get('shared_products') or 0)} 个产品")
        causes.append(
            _mk_cause(
                "supplier_risk",
                "dependency_concentration",
                score,
                evidence_lines,
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection"]),
            )
        )

    delayed_orders = max((float(row.get("delayed_orders") or 0) for row in delay_rows), default=0.0)
    if delayed_orders > 0 or delay_event_count > 0:
        top_delay = delay_rows[0] if delay_rows else {}
        score = 0.34 + min(0.35, delayed_orders * 0.02)
        score += min(0.08, 0.01 * _metric(metrics, "carrier_count"))
        score += min(0.08, 0.015 * _metric(metrics, "product_count"))
        score += min(0.14, 0.04 * delay_event_count)
        causes.append(
            _mk_cause(
                "supplier_risk",
                "delay_propagation",
                score,
                [
                    f"单条物流链最高延迟订单数 {int(delayed_orders)}",
                    f"主要关联承运商 {top_delay.get('carrier')}",
                    f"事件层中已观测到 {int(delay_event_count)} 条关联延迟事件",
                ],
                _support_bundle(evidence, ["DelayEvent", "SupplierNotice", "QualityInspection"]),
            )
        )

    if replacement_gap:
        uncovered = len([row for row in replacement_gap if int(row.get("alternative_suppliers") or 0) <= 0])
        score = 0.30 + min(0.35, 0.08 * uncovered)
        score += min(0.10, 0.03 * _metric(metrics, "single_source_components"))
        causes.append(
            _mk_cause(
                "supplier_risk",
                "replacement_gap",
                score,
                [f"{uncovered} 个组件缺少可替代供应商"],
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection"]),
            )
        )

    if avg_cost > 0 and product_rows:
        top_product = product_rows[0]
        score = 0.22 + min(0.22, avg_cost / 5000.0) + min(
            0.14, float(top_product.get("profit_exposure") or 0) / 30000000.0
        )
        score += min(0.08, 0.01 * _metric(metrics, "product_count"))
        causes.append(
            _mk_cause(
                "supplier_risk",
                "cost_volatility",
                score,
                [
                    f"平均制造成本 {round(avg_cost, 2)}",
                    f"高暴露产品 {top_product.get('product')} 利润暴露 {round(float(top_product.get('profit_exposure') or 0), 2)}",
                ],
                _support_bundle(evidence, ["SupplierNotice"]),
            )
        )

    return sorted(causes, key=lambda item: item["score"], reverse=True)


def _score_carrier_delay(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    causes: List[Dict[str, Any]] = []
    metrics = _topology(evidence)
    overview = evidence.get("overview", {})
    routes = evidence.get("routes", [])
    delayed_orders = float(overview.get("delayed_orders") or 0)
    avg_delay = float(overview.get("avg_delay_days") or 0)
    profit_at_risk = float(overview.get("profit_at_risk") or 0)
    delay_event_count = _event_metric(evidence, "delay_event_count")
    max_delay_hours = _event_metric(evidence, "max_delay_hours")

    if routes:
        top_route = routes[0]
        score = 0.42 + min(0.3, float(top_route.get("delayed_orders") or 0) * 0.02)
        score += min(0.08, 0.01 * _metric(metrics, "order_count"))
        score += min(0.08, 0.01 * _metric(metrics, "product_count"))
        score += min(0.14, 0.04 * delay_event_count)
        causes.append(
            _mk_cause(
                "carrier_delay",
                "route_congestion",
                score,
                [
                    f"区域 {top_route.get('province')}/{top_route.get('city')} 延迟最集中",
                    f"证据子图覆盖订单 {_metric(metrics, 'order_count'):.0f} 个",
                    f"事件层记录到 {int(delay_event_count)} 条承运商直连延迟事件",
                ],
                _support_bundle(evidence, ["DelayEvent"]),
            )
        )

    if avg_delay > 0 or delay_event_count > 0:
        score = 0.36 + min(0.3, avg_delay * 0.04)
        score += min(0.10, 0.02 * _metric(metrics, "max_carrier_order_span"))
        score += min(0.08, max_delay_hours / 48.0 * 0.03)
        top_mode = routes[0] if routes else {}
        causes.append(
            _mk_cause(
                "carrier_delay",
                "mode_instability",
                score,
                [
                    f"平均超期 {round(avg_delay, 2)} 天",
                    f"主要运输方式 {top_mode.get('transport_mode')}",
                    f"事件层最大延迟 {round(max_delay_hours, 1)} 小时",
                ],
                _support_bundle(evidence, ["DelayEvent"]),
            )
        )

    if delayed_orders > 0 or profit_at_risk > 0:
        score = 0.35 + min(0.28, delayed_orders * 0.01) + min(0.08, profit_at_risk / 1000000.0)
        score += min(0.08, 0.01 * _metric(metrics, "supplier_count"))
        score += min(0.08, 0.01 * _metric(metrics, "product_count"))
        causes.append(
            _mk_cause(
                "carrier_delay",
                "exposure_concentration",
                score,
                [
                    f"延迟订单 {int(delayed_orders)} 个，利润暴露 {round(profit_at_risk, 2)}",
                    f"上游供应商覆盖 {_metric(metrics, 'supplier_count'):.0f} 个",
                ],
                _support_bundle(evidence, ["DelayEvent"]),
            )
        )

    return sorted(causes, key=lambda item: item["score"], reverse=True)


def _score_product_impact(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    causes: List[Dict[str, Any]] = []
    metrics = _topology(evidence)
    overview = evidence.get("overview", {})
    single_rows = evidence.get("single_source_components", [])
    supply_rows = evidence.get("supply_path", [])
    logistics_rows = evidence.get("logistics_exposure", [])
    share_rows = evidence.get("component_share", [])
    supplier_rows = evidence.get("supplier_concentration", [])
    mode_rows = evidence.get("mode_exposure", [])
    profit = float(overview.get("profit") or 0)
    fail_inspections = _event_metric(evidence, "fail_inspection_count")
    notice_count = _event_metric(evidence, "supplier_notice_count")
    delay_event_count = _event_metric(evidence, "delay_event_count")
    max_delay_hours = _event_metric(evidence, "max_delay_hours")

    if single_rows:
        score = 0.50 + min(0.25, 0.08 * len(single_rows))
        score += min(0.08, 0.03 * _metric(metrics, "single_source_components"))
        score += min(0.08, 0.03 * fail_inspections) + min(0.06, 0.02 * notice_count)
        top = single_rows[0]
        causes.append(
            _mk_cause(
                "product_impact",
                "single_source_dependency",
                score,
                [
                    f"组件 {top.get('component')} 仅由 {top.get('sole_supplier')} 供应",
                    f"证据子图存在 {_metric(metrics, 'single_source_components'):.0f} 个单一来源组件",
                ],
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection"]),
            )
        )

    risky_suppliers = [row for row in supply_rows if float(row.get("avg_defect_rate") or 0) >= 0.005]
    if risky_suppliers or supplier_rows or fail_inspections > 0 or notice_count > 0:
        top_supply = risky_suppliers[0] if risky_suppliers else (supply_rows[0] if supply_rows else {})
        top_supplier = supplier_rows[0] if supplier_rows else {}
        score = 0.32 + min(0.22, float(top_supply.get("avg_defect_rate") or 0) * 20) + min(
            0.18, float(top_supplier.get("profit_exposure") or 0) / 30000000.0
        )
        score += min(0.08, 0.01 * _metric(metrics, "supplier_count"))
        score += min(0.08, 0.02 * _metric(metrics, "max_supplier_component_span"))
        score += min(0.12, 0.05 * fail_inspections) + min(0.08, 0.03 * notice_count)
        evidence_lines: List[str] = []
        if top_supply:
            evidence_lines.append(
                f"组件 {top_supply.get('component')} 的平均缺陷率 {round(float(top_supply.get('avg_defect_rate') or 0), 4)}"
            )
        if top_supplier:
            evidence_lines.append(
                f"上游供应商 {top_supplier.get('supplier')} 的利润暴露 {round(float(top_supplier.get('profit_exposure') or 0), 2)}"
            )
        if fail_inspections > 0 or notice_count > 0:
            evidence_lines.append(f"事件层记录到 {int(notice_count)} 条预警、{int(fail_inspections)} 条失败抽检")
        causes.append(
            _mk_cause(
                "product_impact",
                "upstream_supplier_risk",
                score,
                evidence_lines or ["上游供应商存在直接质量异常事件"],
                _support_bundle(evidence, ["SupplierNotice", "QualityInspection"]),
            )
        )

    shared_candidates = [row for row in share_rows if float(row.get("shared_products") or 0) >= 2]
    if shared_candidates:
        top = shared_candidates[0]
        score = 0.30 + min(0.28, 0.06 * (float(top.get("shared_products") or 0) - 1))
        score += min(0.10, 0.03 * _metric(metrics, "max_component_product_span"))
        causes.append(
            _mk_cause(
                "product_impact",
                "component_concentration",
                score,
                [
                    f"组件 {top.get('component')} 被 {int(top.get('shared_products') or 0)} 个产品共用",
                    f"组件复用跨度 {_metric(metrics, 'max_component_product_span'):.0f}",
                ],
                _support_bundle(evidence, ["QualityInspection"]),
            )
        )

    if logistics_rows or delay_event_count > 0:
        top = logistics_rows[0] if logistics_rows else {}
        late_orders = float(top.get("late_orders") or 0)
        score = 0.28 + min(0.22, late_orders * 0.02) + min(
            0.16, max(0.0, float(top.get("avg_delay_days") or 0)) * 0.05
        )
        score += min(0.08, 0.01 * _metric(metrics, "order_count"))
        score += min(0.08, 0.01 * _metric(metrics, "carrier_count"))
        score += min(0.14, 0.04 * delay_event_count) + min(0.08, max_delay_hours / 48.0 * 0.03)
        evidence_lines = []
        if top:
            evidence_lines.extend(
                [
                    f"承运商 {top.get('carrier')} 相关延迟订单 {int(late_orders)} 个",
                    f"平均超期 {round(float(top.get('avg_delay_days') or 0), 2)} 天",
                ]
            )
        if mode_rows:
            top_mode = mode_rows[0]
            evidence_lines.append(
                f"主要异常运输方式 {top_mode.get('transport_mode')} / {top_mode.get('ship_mode')}"
            )
        if delay_event_count > 0:
            evidence_lines.append(f"事件层记录到 {int(delay_event_count)} 条产品延迟事件，最长 {round(max_delay_hours, 1)} 小时")
        causes.append(
            _mk_cause(
                "product_impact",
                "logistics_delay_exposure",
                score,
                evidence_lines or ["产品层面已经出现直接延迟事件"],
                _support_bundle(evidence, ["DelayEvent"]),
            )
        )

    if profit > 0:
        score = 0.16 + min(0.18, profit / 150000000.0)
        score += min(0.08, 0.01 * _metric(metrics, "order_count"))
        causes.append(
            _mk_cause(
                "product_impact",
                "margin_exposure",
                score,
                [f"该产品利润暴露 {round(profit, 2)}"],
                _support_bundle(evidence, ["SupplierNotice", "DelayEvent"]),
            )
        )

    return sorted(causes, key=lambda item: item["score"], reverse=True)


def score_causes(anomaly_type: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    if anomaly_type == "order_delay":
        candidates = _score_order_delay(evidence)
    elif anomaly_type == "supplier_risk":
        candidates = _score_supplier_risk(evidence)
    elif anomaly_type == "carrier_delay":
        candidates = _score_carrier_delay(evidence)
    elif anomaly_type == "product_impact":
        candidates = _score_product_impact(evidence)
    else:
        candidates = []

    return {
        "candidate_causes": candidates,
        "recommended_actions": merge_actions(candidates),
    }
