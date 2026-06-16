from typing import Any, Dict, List, Tuple

from kg_tools import graph

from .policies import get_policy
from .schemas import RCAAnomaly


def _query(name: str, cypher: str, params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows = graph.query(cypher, params=params)
    return rows, {"name": name, "cypher": cypher.strip(), "params": dict(params)}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _empty_graph(target_type: str, target_id: str, reason: str = "") -> Dict[str, Any]:
    return {
        "nodes": [],
        "edges": [],
        "meta": {
            "mode": "rca_evidence",
            "target_type": target_type,
            "target_id": target_id,
            "node_count": 0,
            "edge_count": 0,
            "label_counts": {},
            "relation_counts": {},
            "topology": {},
            "reason": reason,
        },
    }


def _count_labels(nodes: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for node in nodes:
        for label in node.get("labels") or []:
            counts[label] = counts.get(label, 0) + 1
    return counts


def _count_relations(edges: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for edge in edges:
        rel_type = str(edge.get("type") or "")
        if rel_type:
            counts[rel_type] = counts.get(rel_type, 0) + 1
    return counts


def _derive_topology_metrics(
    nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> Dict[str, Any]:
    node_labels: Dict[str, str] = {}
    for node in nodes:
        labels = node.get("labels") or []
        node_labels[str(node.get("id"))] = labels[0] if labels else "Unknown"

    supplier_to_component: Dict[str, set[str]] = {}
    component_to_supplier: Dict[str, set[str]] = {}
    component_to_product: Dict[str, set[str]] = {}
    carrier_to_order: Dict[str, set[str]] = {}

    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        rel_type = str(edge.get("type") or "")
        source_label = node_labels.get(source, "")
        target_label = node_labels.get(target, "")

        if rel_type == "SUPPLIES_COMPONENT" and source_label == "Supplier" and target_label == "Component":
            supplier_to_component.setdefault(source, set()).add(target)
            component_to_supplier.setdefault(target, set()).add(source)
        elif rel_type == "USED_IN" and source_label == "Component" and target_label == "Product":
            component_to_product.setdefault(source, set()).add(target)
        elif rel_type == "SHIPPED_BY" and source_label == "Order" and target_label == "Carrier":
            carrier_to_order.setdefault(target, set()).add(source)

    label_counts = _count_labels(nodes)
    component_reuse = [len(products) for products in component_to_product.values()]
    supplier_span = [len(components) for components in supplier_to_component.values()]
    carrier_span = [len(orders) for orders in carrier_to_order.values()]
    single_source_components = sum(
        1
        for component_id, suppliers in component_to_supplier.items()
        if len(suppliers) <= 1 and len(component_to_product.get(component_id, set())) > 0
    )

    return {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "supplier_count": label_counts.get("Supplier", 0),
        "component_count": label_counts.get("Component", 0),
        "product_count": label_counts.get("Product", 0),
        "order_count": label_counts.get("Order", 0),
        "carrier_count": label_counts.get("Carrier", 0),
        "customer_count": label_counts.get("Customer", 0),
        "single_source_components": single_source_components,
        "max_component_product_span": max(component_reuse) if component_reuse else 0,
        "max_supplier_component_span": max(supplier_span) if supplier_span else 0,
        "max_carrier_order_span": max(carrier_span) if carrier_span else 0,
    }


def _normalize_graph_rows(
    rows: List[Dict[str, Any]], target_type: str, target_id: str
) -> Dict[str, Any]:
    if not rows:
        return _empty_graph(target_type, target_id, reason="no_graph_rows")

    record = rows[0] or {}
    nodes = record.get("nodes") or []
    edges = record.get("edges") or []
    label_counts = _count_labels(nodes)
    relation_counts = _count_relations(edges)
    topology = _derive_topology_metrics(nodes, edges)
    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "mode": "rca_evidence",
            "target_type": target_type,
            "target_id": target_id,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "label_counts": label_counts,
            "relation_counts": relation_counts,
            "topology": topology,
        },
    }


_EVENT_RELATION_TYPES = (
    "PLACED_ORDER|CONTAINS_PRODUCT|SHIPPED_BY|USED_IN|SUPPLIES_COMPONENT|"
    "ISSUED_NOTICE|AFFECTS_COMPONENT|AFFECTS_PRODUCT|AFFECTS_ORDER|"
    "UNDERWENT_INSPECTION|INSPECTED_COMPONENT|INSPECTED_PRODUCT|IMPACTS_ORDER|"
    "HAS_DELAY_EVENT|TRIGGERED_DELAY_EVENT|IMPACTS_PRODUCT|RELATED_TO_SUPPLIER|SUPPORTED_BY"
)


def _severity_rank(value: Any) -> int:
    text = str(value or "").strip().lower()
    if text in {"critical", "high", "fail"}:
        return 3
    if text in {"medium", "warn"}:
        return 2
    if text in {"low", "pass"}:
        return 1
    return 0


def _dedupe_dict_rows(rows: List[Dict[str, Any]], key: str = "id") -> List[Dict[str, Any]]:
    seen: set[str] = set()
    items: List[Dict[str, Any]] = []
    for row in rows:
        value = str(row.get(key) or "")
        marker = value or str(row)
        if marker in seen:
            continue
        seen.add(marker)
        items.append(row)
    return items


def _sort_direct_evidence(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (
            -_severity_rank(item.get("severity") or item.get("result")),
            -_to_float(item.get("delay_hours")),
            -_to_float(item.get("observed_defect_rate")),
            str(item.get("id") or ""),
        ),
    )


def _merge_supporting_nodes(*items: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        for node in item.get("supporting_nodes") or []:
            label = str(node.get("label") or "")
            value = str(node.get("value") or "")
            marker = (label, value)
            if not label or not value or marker in seen:
                continue
            seen.add(marker)
            merged.append({"label": label, "value": value})
    return merged


def _merge_supporting_edges(*items: Dict[str, Any]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for item in items:
        for edge in item.get("supporting_edges") or []:
            value = str(edge or "")
            if not value or value in seen:
                continue
            seen.add(value)
            merged.append(value)
    return merged


def _merge_source_records(*items: Dict[str, Any]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        for record in item.get("source_records") or []:
            value = str(record.get("id") or "")
            marker = value or str(record)
            if marker in seen:
                continue
            seen.add(marker)
            merged.append(record)
    return merged


def _build_event_item(
    event_type: str,
    raw: Dict[str, Any],
    supporting_nodes: List[Dict[str, Any]],
    supporting_edges: List[str],
) -> Dict[str, Any]:
    source_ref = str(raw.get("source_ref") or "")
    item = dict(raw)
    item["event_type"] = event_type
    item["supporting_nodes"] = supporting_nodes
    item["supporting_edges"] = supporting_edges
    item["source_records"] = (
        [
            {
                "id": source_ref,
                "summary": raw.get("source_summary"),
                "source_row_key": raw.get("source_row_key"),
                "created_at": raw.get("source_created_at"),
            }
        ]
        if source_ref
        else []
    )
    return item


def _compose_event_summary(
    notices: List[Dict[str, Any]],
    inspections: List[Dict[str, Any]],
    delays: List[Dict[str, Any]],
) -> Dict[str, Any]:
    fail_inspections = sum(1 for item in inspections if str(item.get("result") or "").upper() == "FAIL")
    high_severity_events = sum(
        1
        for item in [*notices, *inspections, *delays]
        if _severity_rank(item.get("severity") or item.get("result")) >= 3
    )
    return {
        "supplier_notice_count": len(notices),
        "quality_inspection_count": len(inspections),
        "fail_inspection_count": fail_inspections,
        "delay_event_count": len(delays),
        "high_severity_event_count": high_severity_events,
        "max_delay_hours": max((_to_float(item.get("delay_hours")) for item in delays), default=0.0),
        "max_observed_defect_rate": max(
            (_to_float(item.get("observed_defect_rate")) for item in inspections),
            default=0.0,
        ),
    }


def _build_event_chain(
    target_kind: str,
    notices: List[Dict[str, Any]],
    inspections: List[Dict[str, Any]],
    delays: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    chains: List[Dict[str, Any]] = []
    top_notice = notices[0] if notices else {}
    top_inspection = inspections[0] if inspections else {}
    top_delay = delays[0] if delays else {}

    if top_notice and top_inspection:
        supplier = top_notice.get("supplier_name") or top_inspection.get("supplier_name") or "unknown supplier"
        chains.append(
            {
                "chain_type": f"{target_kind}_upstream_quality_chain",
                "severity": "high"
                if max(
                    _severity_rank(top_notice.get("severity")),
                    _severity_rank(top_inspection.get("severity") or top_inspection.get("result")),
                )
                >= 3
                else "medium",
                "narrative": (
                    f"{supplier} 先触发供应商预警 {top_notice.get('id')}，随后质量抽检 "
                    f"{top_inspection.get('id')} 结果为 {top_inspection.get('result')}。"
                ),
                "supporting_nodes": _merge_supporting_nodes(top_notice, top_inspection),
                "supporting_edges": _merge_supporting_edges(top_notice, top_inspection),
                "source_records": _merge_source_records(top_notice, top_inspection),
            }
        )

    if top_inspection and top_delay:
        supplier = top_inspection.get("supplier_name") or top_delay.get("supplier_name") or "unknown supplier"
        chains.append(
            {
                "chain_type": f"{target_kind}_inspection_to_delay_chain",
                "severity": "high"
                if max(
                    _severity_rank(top_inspection.get("severity") or top_inspection.get("result")),
                    _severity_rank(top_delay.get("severity")),
                )
                >= 3
                else "medium",
                "narrative": (
                    f"{supplier} 相关的质量抽检 {top_inspection.get('id')} 后，订单侧出现延迟事件 "
                    f"{top_delay.get('id')}，原因为 {top_delay.get('reason_code')}。"
                ),
                "supporting_nodes": _merge_supporting_nodes(top_inspection, top_delay),
                "supporting_edges": _merge_supporting_edges(top_inspection, top_delay),
                "source_records": _merge_source_records(top_inspection, top_delay),
            }
        )

    if top_notice and top_delay:
        supplier = top_notice.get("supplier_name") or top_delay.get("supplier_name") or "unknown supplier"
        chains.append(
            {
                "chain_type": f"{target_kind}_notice_to_delay_chain",
                "severity": "high"
                if max(_severity_rank(top_notice.get("severity")), _severity_rank(top_delay.get("severity"))) >= 3
                else "medium",
                "narrative": (
                    f"{supplier} 的供应商预警 {top_notice.get('id')} 与延迟事件 {top_delay.get('id')} "
                    f"形成了直接的时间链证据。"
                ),
                "supporting_nodes": _merge_supporting_nodes(top_notice, top_delay),
                "supporting_edges": _merge_supporting_edges(top_notice, top_delay),
                "source_records": _merge_source_records(top_notice, top_delay),
            }
        )

    return chains[:3]


def _shape_event_probe_result(
    target_kind: str,
    notices: List[Dict[str, Any]],
    inspections: List[Dict[str, Any]],
    delays: List[Dict[str, Any]],
) -> Dict[str, Any]:
    direct_evidence = _sort_direct_evidence([*notices, *inspections, *delays])
    source_records = _merge_source_records(*direct_evidence)
    return {
        "event_summary": _compose_event_summary(notices, inspections, delays),
        "direct_evidence": direct_evidence[:12],
        "event_chain": _build_event_chain(target_kind, notices, inspections, delays),
        "source_records": source_records[:12],
    }


def _collect_order_event_summary(order_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (o:Order {id: $order_id})
    OPTIONAL MATCH (n:SupplierNotice)-[:AFFECTS_ORDER]->(o)
    OPTIONAL MATCH (q:QualityInspection)-[:IMPACTS_ORDER]->(o)
    OPTIONAL MATCH (o)-[:HAS_DELAY_EVENT]->(d:DelayEvent)
    RETURN
        count(DISTINCT n) AS supplier_notice_count,
        count(DISTINCT CASE WHEN coalesce(n.severity, '') = 'high' THEN n END) AS high_notice_count,
        count(DISTINCT q) AS quality_inspection_count,
        count(DISTINCT CASE WHEN coalesce(q.result, '') = 'FAIL' THEN q END) AS fail_inspection_count,
        count(DISTINCT d) AS delay_event_count,
        count(DISTINCT CASE WHEN coalesce(d.severity, '') = 'high' THEN d END) AS high_delay_event_count,
        max(coalesce(d.delay_hours, 0)) AS max_delay_hours,
        max(coalesce(q.observed_defect_rate, 0)) AS max_observed_defect_rate
    """
    rows, trace = _query("collect_order_event_summary", cypher, {"order_id": order_id})
    return (rows[0] if rows else {}), [trace]


def _collect_supplier_event_summary(supplier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    OPTIONAL MATCH (s)-[:ISSUED_NOTICE]->(n:SupplierNotice)
    OPTIONAL MATCH (s)-[:UNDERWENT_INSPECTION]->(q:QualityInspection)
    OPTIONAL MATCH (d:DelayEvent)-[:RELATED_TO_SUPPLIER]->(s)
    RETURN
        count(DISTINCT n) AS supplier_notice_count,
        count(DISTINCT CASE WHEN coalesce(n.severity, '') = 'high' THEN n END) AS high_notice_count,
        count(DISTINCT q) AS quality_inspection_count,
        count(DISTINCT CASE WHEN coalesce(q.result, '') = 'FAIL' THEN q END) AS fail_inspection_count,
        count(DISTINCT d) AS delay_event_count,
        count(DISTINCT CASE WHEN coalesce(d.severity, '') = 'high' THEN d END) AS high_delay_event_count,
        max(coalesce(d.delay_hours, 0)) AS max_delay_hours,
        max(coalesce(q.observed_defect_rate, 0)) AS max_observed_defect_rate
    """
    rows, trace = _query("collect_supplier_event_summary", cypher, {"supplier_name": supplier_name})
    return (rows[0] if rows else {}), [trace]


def _collect_carrier_event_summary(carrier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (car:Carrier)
    WHERE car.name CONTAINS $carrier_name
    OPTIONAL MATCH (car)-[:TRIGGERED_DELAY_EVENT]->(d:DelayEvent)
    RETURN
        count(DISTINCT d) AS delay_event_count,
        count(DISTINCT CASE WHEN coalesce(d.severity, '') = 'high' THEN d END) AS high_delay_event_count,
        max(coalesce(d.delay_hours, 0)) AS max_delay_hours
    """
    rows, trace = _query("collect_carrier_event_summary", cypher, {"carrier_name": carrier_name})
    return (rows[0] if rows else {}), [trace]


def _collect_product_event_summary(product_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    OPTIONAL MATCH (n:SupplierNotice)-[:AFFECTS_PRODUCT]->(p)
    OPTIONAL MATCH (q:QualityInspection)-[:INSPECTED_PRODUCT]->(p)
    OPTIONAL MATCH (d:DelayEvent)-[:IMPACTS_PRODUCT]->(p)
    RETURN
        count(DISTINCT n) AS supplier_notice_count,
        count(DISTINCT CASE WHEN coalesce(n.severity, '') = 'high' THEN n END) AS high_notice_count,
        count(DISTINCT q) AS quality_inspection_count,
        count(DISTINCT CASE WHEN coalesce(q.result, '') = 'FAIL' THEN q END) AS fail_inspection_count,
        count(DISTINCT d) AS delay_event_count,
        count(DISTINCT CASE WHEN coalesce(d.severity, '') = 'high' THEN d END) AS high_delay_event_count,
        max(coalesce(d.delay_hours, 0)) AS max_delay_hours,
        max(coalesce(q.observed_defect_rate, 0)) AS max_observed_defect_rate
    """
    rows, trace = _query("collect_product_event_summary", cypher, {"product_name": product_name})
    return (rows[0] if rows else {}), [trace]


def _run_order_event_probe(order_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (o:Order {id: $order_id})
    OPTIONAL MATCH (sn:Supplier)-[:ISSUED_NOTICE]->(n:SupplierNotice)-[:AFFECTS_ORDER]->(o)
    OPTIONAL MATCH (n)-[:SUPPORTED_BY]->(nsrc:SourceRecord)
    OPTIONAL MATCH (sq:Supplier)-[:UNDERWENT_INSPECTION]->(q:QualityInspection)-[:IMPACTS_ORDER]->(o)
    OPTIONAL MATCH (q)-[:SUPPORTED_BY]->(qsrc:SourceRecord)
    OPTIONAL MATCH (o)-[:HAS_DELAY_EVENT]->(d:DelayEvent)<-[:TRIGGERED_DELAY_EVENT]-(car:Carrier)
    OPTIONAL MATCH (d)-[:SUPPORTED_BY]->(dsrc:SourceRecord)
    RETURN
        [item IN collect(DISTINCT CASE WHEN n IS NULL THEN NULL ELSE {
            id: n.id, notice_type: n.notice_type, severity: n.severity, reason_code: n.reason_code,
            summary: n.summary, supplier_name: coalesce(sn.name, n.supplier_name), component_name: n.component_name,
            product_id: n.product_id, order_id: n.order_id, source_ref: n.source_ref,
            source_summary: nsrc.summary, source_row_key: nsrc.source_row_key, source_created_at: nsrc.created_at
        } END) WHERE item IS NOT NULL] AS notices,
        [item IN collect(DISTINCT CASE WHEN q IS NULL THEN NULL ELSE {
            id: q.id, result: q.result, severity: q.severity, inspection_time: q.inspection_time,
            batch_id: q.batch_id, observed_defect_rate: q.observed_defect_rate, failure_mode: q.failure_mode,
            supplier_name: coalesce(sq.name, q.supplier_name), component_name: q.component_name,
            product_id: q.product_id, order_id: q.order_id, source_ref: q.source_ref,
            source_summary: qsrc.summary, source_row_key: qsrc.source_row_key, source_created_at: qsrc.created_at
        } END) WHERE item IS NOT NULL] AS inspections,
        [item IN collect(DISTINCT CASE WHEN d IS NULL THEN NULL ELSE {
            id: d.id, severity: d.severity, occurred_at: d.occurred_at, reason_code: d.reason_code,
            delay_stage: d.delay_stage, delay_hours: d.delay_hours, order_id: d.order_id,
            carrier_name: car.name, supplier_name: d.supplier_name, product_id: d.product_id,
            source_ref: d.source_ref, source_summary: dsrc.summary, source_row_key: dsrc.source_row_key,
            source_created_at: dsrc.created_at
        } END) WHERE item IS NOT NULL] AS delays
    """
    rows, trace = _query("probe_order_event_chain", cypher, {"order_id": order_id})
    row = rows[0] if rows else {}
    notices = [
        _build_event_item(
            "SupplierNotice",
            raw,
            [
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Component", "value": raw.get("component_name")},
                {"label": "Order", "value": raw.get("order_id")},
            ],
            ["Supplier-ISSUED_NOTICE->SupplierNotice", "SupplierNotice-AFFECTS_ORDER->Order", "SupplierNotice-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("notices") or [])
    ]
    inspections = [
        _build_event_item(
            "QualityInspection",
            raw,
            [
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Component", "value": raw.get("component_name")},
                {"label": "Order", "value": raw.get("order_id")},
            ],
            ["Supplier-UNDERWENT_INSPECTION->QualityInspection", "QualityInspection-IMPACTS_ORDER->Order", "QualityInspection-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("inspections") or [])
    ]
    delays = [
        _build_event_item(
            "DelayEvent",
            raw,
            [
                {"label": "Carrier", "value": raw.get("carrier_name")},
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Order", "value": raw.get("order_id")},
            ],
            ["Order-HAS_DELAY_EVENT->DelayEvent", "Carrier-TRIGGERED_DELAY_EVENT->DelayEvent", "DelayEvent-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("delays") or [])
    ]
    return _shape_event_probe_result("order", notices, inspections, delays), [trace]


def _run_supplier_event_probe(supplier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    OPTIONAL MATCH (s)-[:ISSUED_NOTICE]->(n:SupplierNotice)
    OPTIONAL MATCH (n)-[:SUPPORTED_BY]->(nsrc:SourceRecord)
    OPTIONAL MATCH (s)-[:UNDERWENT_INSPECTION]->(q:QualityInspection)
    OPTIONAL MATCH (q)-[:SUPPORTED_BY]->(qsrc:SourceRecord)
    OPTIONAL MATCH (d:DelayEvent)-[:RELATED_TO_SUPPLIER]->(s)
    OPTIONAL MATCH (d)-[:SUPPORTED_BY]->(dsrc:SourceRecord)
    OPTIONAL MATCH (car:Carrier)-[:TRIGGERED_DELAY_EVENT]->(d)
    RETURN
        [item IN collect(DISTINCT CASE WHEN n IS NULL THEN NULL ELSE {
            id: n.id, notice_type: n.notice_type, severity: n.severity, reason_code: n.reason_code,
            summary: n.summary, supplier_name: s.name, component_name: n.component_name, product_id: n.product_id,
            order_id: n.order_id, source_ref: n.source_ref, source_summary: nsrc.summary,
            source_row_key: nsrc.source_row_key, source_created_at: nsrc.created_at
        } END) WHERE item IS NOT NULL] AS notices,
        [item IN collect(DISTINCT CASE WHEN q IS NULL THEN NULL ELSE {
            id: q.id, result: q.result, severity: q.severity, inspection_time: q.inspection_time,
            batch_id: q.batch_id, observed_defect_rate: q.observed_defect_rate, failure_mode: q.failure_mode,
            supplier_name: s.name, component_name: q.component_name, product_id: q.product_id, order_id: q.order_id,
            source_ref: q.source_ref, source_summary: qsrc.summary, source_row_key: qsrc.source_row_key,
            source_created_at: qsrc.created_at
        } END) WHERE item IS NOT NULL] AS inspections,
        [item IN collect(DISTINCT CASE WHEN d IS NULL THEN NULL ELSE {
            id: d.id, severity: d.severity, occurred_at: d.occurred_at, reason_code: d.reason_code,
            delay_stage: d.delay_stage, delay_hours: d.delay_hours, order_id: d.order_id, carrier_name: car.name,
            supplier_name: s.name, product_id: d.product_id, source_ref: d.source_ref,
            source_summary: dsrc.summary, source_row_key: dsrc.source_row_key, source_created_at: dsrc.created_at
        } END) WHERE item IS NOT NULL] AS delays
    """
    rows, trace = _query("probe_supplier_event_chain", cypher, {"supplier_name": supplier_name})
    row = rows[0] if rows else {}
    notices = [
        _build_event_item(
            "SupplierNotice",
            raw,
            [
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Component", "value": raw.get("component_name")},
                {"label": "Order", "value": raw.get("order_id")},
            ],
            ["Supplier-ISSUED_NOTICE->SupplierNotice", "SupplierNotice-AFFECTS_ORDER->Order", "SupplierNotice-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("notices") or [])
    ]
    inspections = [
        _build_event_item(
            "QualityInspection",
            raw,
            [
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Component", "value": raw.get("component_name")},
                {"label": "Product", "value": raw.get("product_id")},
            ],
            ["Supplier-UNDERWENT_INSPECTION->QualityInspection", "QualityInspection-INSPECTED_COMPONENT->Component", "QualityInspection-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("inspections") or [])
    ]
    delays = [
        _build_event_item(
            "DelayEvent",
            raw,
            [
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Carrier", "value": raw.get("carrier_name")},
                {"label": "Order", "value": raw.get("order_id")},
            ],
            ["DelayEvent-RELATED_TO_SUPPLIER->Supplier", "Carrier-TRIGGERED_DELAY_EVENT->DelayEvent", "DelayEvent-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("delays") or [])
    ]
    return _shape_event_probe_result("supplier", notices, inspections, delays), [trace]


def _run_carrier_event_probe(carrier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (car:Carrier)
    WHERE car.name CONTAINS $carrier_name
    OPTIONAL MATCH (car)-[:TRIGGERED_DELAY_EVENT]->(d:DelayEvent)
    OPTIONAL MATCH (d)-[:SUPPORTED_BY]->(dsrc:SourceRecord)
    RETURN
        [item IN collect(DISTINCT CASE WHEN d IS NULL THEN NULL ELSE {
            id: d.id, severity: d.severity, occurred_at: d.occurred_at, reason_code: d.reason_code,
            delay_stage: d.delay_stage, delay_hours: d.delay_hours, order_id: d.order_id, carrier_name: car.name,
            supplier_name: d.supplier_name, product_id: d.product_id, source_ref: d.source_ref,
            source_summary: dsrc.summary, source_row_key: dsrc.source_row_key, source_created_at: dsrc.created_at
        } END) WHERE item IS NOT NULL] AS delays
    """
    rows, trace = _query("probe_carrier_event_chain", cypher, {"carrier_name": carrier_name})
    row = rows[0] if rows else {}
    delays = [
        _build_event_item(
            "DelayEvent",
            raw,
            [
                {"label": "Carrier", "value": raw.get("carrier_name")},
                {"label": "Order", "value": raw.get("order_id")},
                {"label": "Supplier", "value": raw.get("supplier_name")},
            ],
            ["Carrier-TRIGGERED_DELAY_EVENT->DelayEvent", "Order-HAS_DELAY_EVENT->DelayEvent", "DelayEvent-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("delays") or [])
    ]
    return _shape_event_probe_result("carrier", [], [], delays), [trace]


def _run_product_event_probe(product_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    OPTIONAL MATCH (sn:Supplier)-[:ISSUED_NOTICE]->(n:SupplierNotice)-[:AFFECTS_PRODUCT]->(p)
    OPTIONAL MATCH (n)-[:SUPPORTED_BY]->(nsrc:SourceRecord)
    OPTIONAL MATCH (sq:Supplier)-[:UNDERWENT_INSPECTION]->(q:QualityInspection)-[:INSPECTED_PRODUCT]->(p)
    OPTIONAL MATCH (q)-[:SUPPORTED_BY]->(qsrc:SourceRecord)
    OPTIONAL MATCH (d:DelayEvent)-[:IMPACTS_PRODUCT]->(p)
    OPTIONAL MATCH (car:Carrier)-[:TRIGGERED_DELAY_EVENT]->(d)
    OPTIONAL MATCH (d)-[:SUPPORTED_BY]->(dsrc:SourceRecord)
    RETURN
        [item IN collect(DISTINCT CASE WHEN n IS NULL THEN NULL ELSE {
            id: n.id, notice_type: n.notice_type, severity: n.severity, reason_code: n.reason_code,
            summary: n.summary, supplier_name: coalesce(sn.name, n.supplier_name), component_name: n.component_name,
            product_id: p.id, order_id: n.order_id, source_ref: n.source_ref,
            source_summary: nsrc.summary, source_row_key: nsrc.source_row_key, source_created_at: nsrc.created_at
        } END) WHERE item IS NOT NULL] AS notices,
        [item IN collect(DISTINCT CASE WHEN q IS NULL THEN NULL ELSE {
            id: q.id, result: q.result, severity: q.severity, inspection_time: q.inspection_time,
            batch_id: q.batch_id, observed_defect_rate: q.observed_defect_rate, failure_mode: q.failure_mode,
            supplier_name: coalesce(sq.name, q.supplier_name), component_name: q.component_name,
            product_id: p.id, order_id: q.order_id, source_ref: q.source_ref,
            source_summary: qsrc.summary, source_row_key: qsrc.source_row_key, source_created_at: qsrc.created_at
        } END) WHERE item IS NOT NULL] AS inspections,
        [item IN collect(DISTINCT CASE WHEN d IS NULL THEN NULL ELSE {
            id: d.id, severity: d.severity, occurred_at: d.occurred_at, reason_code: d.reason_code,
            delay_stage: d.delay_stage, delay_hours: d.delay_hours, order_id: d.order_id, carrier_name: car.name,
            supplier_name: d.supplier_name, product_id: p.id, source_ref: d.source_ref,
            source_summary: dsrc.summary, source_row_key: dsrc.source_row_key, source_created_at: dsrc.created_at
        } END) WHERE item IS NOT NULL] AS delays
    """
    rows, trace = _query("probe_product_event_chain", cypher, {"product_name": product_name})
    row = rows[0] if rows else {}
    notices = [
        _build_event_item(
            "SupplierNotice",
            raw,
            [
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Component", "value": raw.get("component_name")},
                {"label": "Product", "value": raw.get("product_id")},
            ],
            ["Supplier-ISSUED_NOTICE->SupplierNotice", "SupplierNotice-AFFECTS_PRODUCT->Product", "SupplierNotice-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("notices") or [])
    ]
    inspections = [
        _build_event_item(
            "QualityInspection",
            raw,
            [
                {"label": "Supplier", "value": raw.get("supplier_name")},
                {"label": "Component", "value": raw.get("component_name")},
                {"label": "Product", "value": raw.get("product_id")},
            ],
            ["Supplier-UNDERWENT_INSPECTION->QualityInspection", "QualityInspection-INSPECTED_PRODUCT->Product", "QualityInspection-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("inspections") or [])
    ]
    delays = [
        _build_event_item(
            "DelayEvent",
            raw,
            [
                {"label": "Carrier", "value": raw.get("carrier_name")},
                {"label": "Product", "value": raw.get("product_id")},
                {"label": "Supplier", "value": raw.get("supplier_name")},
            ],
            ["DelayEvent-IMPACTS_PRODUCT->Product", "Carrier-TRIGGERED_DELAY_EVENT->DelayEvent", "DelayEvent-SUPPORTED_BY->SourceRecord"],
        )
        for raw in _dedupe_dict_rows(row.get("delays") or [])
    ]
    return _shape_event_probe_result("product", notices, inspections, delays), [trace]


def _collect_local_evidence_graph(
    target_type: str, target_id: str, max_nodes: int = 80, max_edges: int = 160
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    configs = {
        "Order": {
            "match_clause": "MATCH (seed:Order {id: $target_id})",
            "relation_types": _EVENT_RELATION_TYPES,
            "hop_limit": 3,
        },
        "Supplier": {
            "match_clause": "MATCH (seed:Supplier) WHERE seed.name CONTAINS $target_id",
            "relation_types": _EVENT_RELATION_TYPES,
            "hop_limit": 4,
        },
        "Carrier": {
            "match_clause": "MATCH (seed:Carrier) WHERE seed.name CONTAINS $target_id",
            "relation_types": _EVENT_RELATION_TYPES,
            "hop_limit": 4,
        },
        "Product": {
            "match_clause": "MATCH (seed:Product) WHERE seed.name CONTAINS $target_id",
            "relation_types": _EVENT_RELATION_TYPES + "|BELONGS_TO_CATEGORY|BELONGS_TO_DEPARTMENT",
            "hop_limit": 3,
        },
    }
    config = configs.get(target_type)
    if not config or not target_id:
        return _empty_graph(target_type, target_id, reason="unsupported_graph_target"), []

    cypher = f"""
    {config["match_clause"]}
    CALL (seed) {{
        OPTIONAL MATCH p=(seed)-[:{config["relation_types"]}*1..{config["hop_limit"]}]-(n)
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
    try:
        rows, trace = _query(
            "collect_evidence_graph",
            cypher,
            {"target_id": target_id, "max_nodes": max_nodes, "max_edges": max_edges},
        )
    except Exception as exc:
        graph_data = _empty_graph(target_type, target_id, reason=str(exc))
        graph_data["meta"]["graph_query_failed"] = True
        return graph_data, []

    return _normalize_graph_rows(rows, target_type, target_id), [trace]


def _validate_order_delay(order_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    cypher = """
    MATCH (o:Order {id: $order_id})
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(car:Carrier)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        o.id AS order_id,
        coalesce(o.status, '') AS status,
        collect(DISTINCT car.name) AS carriers,
        max(coalesce(ship.late_risk, 0)) AS late_risk,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
        sum(coalesce(con.net_total, 0)) AS net_total,
        sum(coalesce(con.profit, 0)) AS profit
    """
    rows, trace = _query("validate_order_delay", cypher, {"order_id": order_id})
    if not rows:
        return {"found": False, "is_anomaly": False, "reason": "order_not_found"}, [trace]
    row = rows[0]
    status = str(row.get("status") or "").lower()
    avg_delay_days = _to_float(row.get("avg_delay_days"))
    late_risk = _to_int(row.get("late_risk"))
    is_anomaly = late_risk > 0 or avg_delay_days > 0 or status in {"late", "delayed", "delay"}
    return (
        {
            "found": True,
            "is_anomaly": is_anomaly,
            "order_id": row.get("order_id"),
            "status": row.get("status"),
            "carriers": row.get("carriers") or [],
            "late_risk": late_risk,
            "delay_days": round(avg_delay_days, 2),
            "net_total": round(_to_float(row.get("net_total")), 2),
            "profit": round(_to_float(row.get("profit")), 2),
        },
        [trace],
    )


def _collect_order_delay(order_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    validation, traces = _validate_order_delay(order_id)
    if not validation.get("found"):
        return {"validation": validation}, traces

    context_cypher = """
    MATCH (o:Order {id: $order_id})
    OPTIONAL MATCH (cust:Customer)-[:PLACED_ORDER]->(o)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(p:Product)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(car:Carrier)
    RETURN
        o.id AS order_id,
        collect(DISTINCT cust.name) AS customers,
        collect(DISTINCT p.name) AS products,
        collect(DISTINCT car.name) AS carriers,
        sum(coalesce(con.net_total, 0)) AS net_total,
        sum(coalesce(con.profit, 0)) AS profit
    """
    component_cypher = """
    MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
    OPTIONAL MATCH (comp:Component)-[:USED_IN]->(p)
    OPTIONAL MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp)
    RETURN
        p.name AS product,
        comp.name AS component,
        s.name AS supplier,
        avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate,
        avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost
    """
    shared_component_cypher = """
    MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
    MATCH (comp:Component)-[:USED_IN]->(p)
    MATCH (comp)-[:USED_IN]->(other:Product)
    RETURN
        comp.name AS component,
        count(DISTINCT other) AS used_in_products
    ORDER BY used_in_products DESC
    """
    single_source_cypher = """
    MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
    MATCH (comp:Component)-[:USED_IN]->(p)
    CALL {
        WITH comp
        MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp)
        RETURN collect(DISTINCT s.name) AS suppliers, avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate
    }
    WITH comp, suppliers, avg_defect_rate
    WHERE size(suppliers) = 1
    OPTIONAL MATCH (comp)-[:USED_IN]->(affected:Product)<-[con:CONTAINS_PRODUCT]-(affected_order:Order)
    RETURN
        comp.name AS component,
        suppliers[0] AS sole_supplier,
        avg_defect_rate,
        count(DISTINCT affected) AS affected_products,
        count(DISTINCT affected_order) AS affected_orders,
        sum(coalesce(con.profit, 0)) AS profit_exposure
    ORDER BY affected_orders DESC, profit_exposure DESC
    """
    supplier_exposure_cypher = """
    MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p)
    OPTIONAL MATCH (s)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(prod:Product)<-[con:CONTAINS_PRODUCT]-(affected:Order)
    OPTIONAL MATCH (affected)-[ship:SHIPPED_BY]->(:Carrier)
    RETURN
        s.name AS supplier,
        count(DISTINCT affected) AS affected_orders,
        count(DISTINCT prod) AS affected_products,
        sum(coalesce(con.net_total, 0)) AS net_exposure,
        sum(coalesce(con.profit, 0)) AS profit_exposure,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN affected END) AS delayed_orders_touched
    ORDER BY profit_exposure DESC, affected_orders DESC
    """
    carrier_cypher = """
    MATCH (:Order {id: $order_id})-[:SHIPPED_BY]->(car:Carrier)
    OPTIONAL MATCH (o:Order)-[ship:SHIPPED_BY]->(car)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        car.name AS carrier,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1
            OR (ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL AND ship.days_real > ship.days_scheduled)
            THEN o END) AS delayed_orders,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
        sum(coalesce(con.net_total, 0)) AS net_at_risk,
        sum(coalesce(con.profit, 0)) AS profit_at_risk
    """
    context_rows, context_trace = _query("collect_order_context", context_cypher, {"order_id": order_id})
    component_rows, component_trace = _query("collect_order_components", component_cypher, {"order_id": order_id})
    shared_rows, shared_trace = _query("collect_shared_components", shared_component_cypher, {"order_id": order_id})
    single_rows, single_trace = _query("collect_single_source_components", single_source_cypher, {"order_id": order_id})
    supplier_rows, supplier_trace = _query("collect_supplier_exposure", supplier_exposure_cypher, {"order_id": order_id})
    carrier_rows, carrier_trace = _query("collect_carrier_context", carrier_cypher, {"order_id": order_id})
    evidence_graph, graph_traces = _collect_local_evidence_graph("Order", order_id)
    traces.extend(
        [context_trace, component_trace, shared_trace, single_trace, supplier_trace, carrier_trace]
    )
    traces.extend(graph_traces)
    return (
        {
            "validation": validation,
            "context": context_rows[0] if context_rows else {},
            "components": component_rows,
            "shared_components": shared_rows,
            "single_source_components": single_rows,
            "supplier_exposure": supplier_rows,
            "carrier_context": carrier_rows[0] if carrier_rows else {},
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("order_delay"),
        },
        traces,
    )


def _collect_supplier_risk(supplier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    traces: List[Dict[str, Any]] = []
    overview_cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    OPTIONAL MATCH (s)-[sup:SUPPLIES_COMPONENT]->(comp:Component)
    OPTIONAL MATCH (comp)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(:Carrier)
    RETURN
        s.name AS supplier,
        count(DISTINCT comp) AS components,
        count(DISTINCT p) AS products,
        count(DISTINCT o) AS orders,
        avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate,
        avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost,
        sum(coalesce(con.net_total, 0)) AS net_exposure,
        sum(coalesce(con.profit, 0)) AS profit_exposure,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS delayed_orders_touched
    ORDER BY profit_exposure DESC
    LIMIT 1
    """
    single_source_cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    MATCH (s)-[sup:SUPPLIES_COMPONENT]->(comp:Component)
    CALL {
        WITH comp
        MATCH (other:Supplier)-[:SUPPLIES_COMPONENT]->(comp)
        RETURN count(DISTINCT other) AS supplier_count
    }
    OPTIONAL MATCH (comp)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)
    RETURN
        comp.name AS component,
        supplier_count,
        count(DISTINCT p) AS products,
        count(DISTINCT o) AS orders,
        sum(coalesce(con.profit, 0)) AS profit_exposure,
        avg(coalesce(sup.defect_rate, 0)) AS defect_rate
    ORDER BY supplier_count ASC, profit_exposure DESC
    """
    replacement_gap_cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    MATCH (s)-[:SUPPLIES_COMPONENT]->(comp:Component)
    OPTIONAL MATCH (other:Supplier)-[:SUPPLIES_COMPONENT]->(comp)
    RETURN
        comp.name AS component,
        count(DISTINCT other) - 1 AS alternative_suppliers
    ORDER BY alternative_suppliers ASC, component
    """
    delay_mix_cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    MATCH (s)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)-[ship:SHIPPED_BY]->(car:Carrier)
    RETURN
        car.name AS carrier,
        count(DISTINCT o) AS orders,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS delayed_orders,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
        sum(coalesce(con.profit, 0)) AS profit_exposure
    ORDER BY delayed_orders DESC, profit_exposure DESC
    """
    component_share_cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    MATCH (s)-[sup:SUPPLIES_COMPONENT]->(comp:Component)
    OPTIONAL MATCH (comp)-[:USED_IN]->(p:Product)
    RETURN
        comp.name AS component,
        count(DISTINCT p) AS shared_products,
        avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost,
        avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate
    ORDER BY shared_products DESC, avg_mfg_cost DESC
    """
    product_concentration_cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    MATCH (s)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)
    RETURN
        p.name AS product,
        count(DISTINCT o) AS orders,
        sum(coalesce(con.profit, 0)) AS profit_exposure
    ORDER BY profit_exposure DESC, orders DESC
    """
    overview_rows, overview_trace = _query("collect_supplier_overview", overview_cypher, {"supplier_name": supplier_name})
    single_rows, single_trace = _query("collect_supplier_single_source", single_source_cypher, {"supplier_name": supplier_name})
    replacement_rows, replacement_trace = _query("collect_supplier_replacement_gap", replacement_gap_cypher, {"supplier_name": supplier_name})
    delay_rows, delay_trace = _query("collect_supplier_delay_mix", delay_mix_cypher, {"supplier_name": supplier_name})
    share_rows, share_trace = _query("collect_supplier_component_share", component_share_cypher, {"supplier_name": supplier_name})
    product_rows, product_trace = _query("collect_supplier_product_concentration", product_concentration_cypher, {"supplier_name": supplier_name})
    evidence_graph, graph_traces = _collect_local_evidence_graph("Supplier", supplier_name)
    traces.extend([overview_trace, single_trace, replacement_trace, delay_trace, share_trace, product_trace])
    traces.extend(graph_traces)
    if not overview_rows:
        return {"validation": {"found": False, "is_anomaly": False, "reason": "supplier_not_found"}}, traces
    overview = overview_rows[0]
    avg_defect_rate = _to_float(overview.get("avg_defect_rate"))
    delayed_orders_touched = _to_int(overview.get("delayed_orders_touched"))
    single_source_components = [row for row in single_rows if _to_int(row.get("supplier_count")) <= 1]
    replacement_gap = [row for row in replacement_rows if _to_int(row.get("alternative_suppliers")) <= 0]
    is_anomaly = avg_defect_rate >= 0.03 or delayed_orders_touched > 0 or len(single_source_components) > 0
    return (
        {
            "validation": {
                "found": True,
                "is_anomaly": is_anomaly,
                "supplier": overview.get("supplier"),
                "avg_defect_rate": round(avg_defect_rate, 4),
                "delayed_orders_touched": delayed_orders_touched,
                "profit_exposure": round(_to_float(overview.get("profit_exposure")), 2),
            },
            "overview": overview,
            "single_source_components": single_source_components,
            "replacement_gap": replacement_gap,
            "delay_mix": delay_rows,
            "component_share": share_rows,
            "product_concentration": product_rows,
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("supplier_risk"),
        },
        traces,
    )


def _collect_carrier_delay(carrier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    traces: List[Dict[str, Any]] = []
    overview_cypher = """
    MATCH (car:Carrier)
    WHERE car.name CONTAINS $carrier_name
    OPTIONAL MATCH (o:Order)-[ship:SHIPPED_BY]->(car)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        car.name AS carrier,
        count(DISTINCT o) AS total_orders,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1
            OR (ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL AND ship.days_real > ship.days_scheduled)
            THEN o END) AS delayed_orders,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
        sum(coalesce(con.net_total, 0)) AS net_at_risk,
        sum(coalesce(con.profit, 0)) AS profit_at_risk
    ORDER BY profit_at_risk DESC
    LIMIT 1
    """
    route_cypher = """
    MATCH (car:Carrier)
    WHERE car.name CONTAINS $carrier_name
    MATCH (cust:Customer)-[:PLACED_ORDER]->(o:Order)-[ship:SHIPPED_BY]->(car)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        cust.province AS province,
        cust.city AS city,
        ship.trans_mode AS transport_mode,
        ship.ship_mode AS ship_mode,
        count(DISTINCT o) AS orders,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS delayed_orders,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
        sum(coalesce(con.profit, 0)) AS profit_exposure
    ORDER BY delayed_orders DESC, profit_exposure DESC
    """
    upstream_cypher = """
    MATCH (car:Carrier)
    WHERE car.name CONTAINS $carrier_name
    MATCH (o:Order)-[:SHIPPED_BY]->(car)
    OPTIONAL MATCH (o)-[:CONTAINS_PRODUCT]->(p:Product)
    OPTIONAL MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p)
    RETURN
        count(DISTINCT p) AS products,
        count(DISTINCT s) AS suppliers
    """
    overview_rows, overview_trace = _query("collect_carrier_overview", overview_cypher, {"carrier_name": carrier_name})
    route_rows, route_trace = _query("collect_carrier_routes", route_cypher, {"carrier_name": carrier_name})
    upstream_rows, upstream_trace = _query("collect_carrier_upstream_coverage", upstream_cypher, {"carrier_name": carrier_name})
    evidence_graph, graph_traces = _collect_local_evidence_graph("Carrier", carrier_name)
    traces.extend([overview_trace, route_trace, upstream_trace])
    traces.extend(graph_traces)
    if not overview_rows:
        return {"validation": {"found": False, "is_anomaly": False, "reason": "carrier_not_found"}}, traces
    overview = overview_rows[0]
    delayed_orders = _to_int(overview.get("delayed_orders"))
    avg_delay_days = _to_float(overview.get("avg_delay_days"))
    is_anomaly = delayed_orders > 0 or avg_delay_days > 0
    return (
        {
            "validation": {
                "found": True,
                "is_anomaly": is_anomaly,
                "carrier": overview.get("carrier"),
                "delayed_orders": delayed_orders,
                "avg_delay_days": round(avg_delay_days, 2),
                "profit_at_risk": round(_to_float(overview.get("profit_at_risk")), 2),
            },
            "overview": overview,
            "routes": route_rows,
            "upstream_coverage": upstream_rows[0] if upstream_rows else {},
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("carrier_delay"),
        },
        traces,
    )


def _collect_product_impact(product_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    traces: List[Dict[str, Any]] = []
    overview_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    OPTIONAL MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(:Carrier)
    RETURN
        p.name AS product,
        count(DISTINCT o) AS orders,
        sum(coalesce(con.net_total, 0)) AS net_revenue,
        sum(coalesce(con.profit, 0)) AS profit,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS late_orders
    ORDER BY profit DESC
    LIMIT 1
    """
    supply_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    OPTIONAL MATCH (comp:Component)-[:USED_IN]->(p)
    OPTIONAL MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp)
    RETURN
        comp.name AS component,
        collect(DISTINCT s.name) AS suppliers,
        size(collect(DISTINCT s.name)) AS supplier_count,
        avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate,
        avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost
    """
    exposure_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(car:Carrier)
    RETURN
        car.name AS carrier,
        count(DISTINCT o) AS orders,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS late_orders,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
        sum(coalesce(con.profit, 0)) AS profit_exposure
    ORDER BY late_orders DESC, profit_exposure DESC
    """
    single_source_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    MATCH (comp:Component)-[:USED_IN]->(p)
    CALL {
        WITH comp
        MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(comp)
        RETURN collect(DISTINCT s.name) AS suppliers
    }
    WITH comp, suppliers
    WHERE size(suppliers) = 1
    RETURN comp.name AS component, suppliers[0] AS sole_supplier
    """
    component_share_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    MATCH (comp:Component)-[:USED_IN]->(p)
    MATCH (comp)-[:USED_IN]->(other:Product)
    RETURN
        comp.name AS component,
        count(DISTINCT other) AS shared_products
    ORDER BY shared_products DESC
    """
    supplier_concentration_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p)
    OPTIONAL MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    RETURN
        s.name AS supplier,
        count(DISTINCT o) AS orders,
        sum(coalesce(con.profit, 0)) AS profit_exposure
    ORDER BY profit_exposure DESC, orders DESC
    """
    mode_exposure_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(:Carrier)
    RETURN
        ship.trans_mode AS transport_mode,
        ship.ship_mode AS ship_mode,
        count(DISTINCT o) AS orders,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS late_orders,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days
    ORDER BY late_orders DESC, avg_delay_days DESC
    """
    overview_rows, overview_trace = _query("collect_product_overview", overview_cypher, {"product_name": product_name})
    supply_rows, supply_trace = _query("collect_product_supply_path", supply_cypher, {"product_name": product_name})
    exposure_rows, exposure_trace = _query("collect_product_logistics_exposure", exposure_cypher, {"product_name": product_name})
    single_rows, single_trace = _query("collect_product_single_source", single_source_cypher, {"product_name": product_name})
    share_rows, share_trace = _query("collect_product_component_share", component_share_cypher, {"product_name": product_name})
    supplier_rows, supplier_trace = _query("collect_product_supplier_concentration", supplier_concentration_cypher, {"product_name": product_name})
    mode_rows, mode_trace = _query("collect_product_mode_exposure", mode_exposure_cypher, {"product_name": product_name})
    evidence_graph, graph_traces = _collect_local_evidence_graph("Product", product_name)
    traces.extend([overview_trace, supply_trace, exposure_trace, single_trace, share_trace, supplier_trace, mode_trace])
    traces.extend(graph_traces)
    if not overview_rows:
        return {"validation": {"found": False, "is_anomaly": False, "reason": "product_not_found"}}, traces
    overview = overview_rows[0]
    late_orders = _to_int(overview.get("late_orders"))
    profit = _to_float(overview.get("profit"))
    is_anomaly = late_orders > 0 or profit > 0 or len(single_rows) > 0
    return (
        {
            "validation": {
                "found": True,
                "is_anomaly": is_anomaly,
                "product": overview.get("product"),
                "late_orders": late_orders,
                "profit": round(profit, 2),
            },
            "overview": overview,
            "supply_path": supply_rows,
            "logistics_exposure": exposure_rows,
            "single_source_components": single_rows,
            "component_share": share_rows,
            "supplier_concentration": supplier_rows,
            "mode_exposure": mode_rows,
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("product_impact"),
        },
        traces,
    )


def collect_evidence(anomaly: RCAAnomaly) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if anomaly.anomaly_type == "order_delay":
        return _collect_order_delay(anomaly.target_id)
    if anomaly.anomaly_type == "supplier_risk":
        return _collect_supplier_risk(anomaly.target_id)
    if anomaly.anomaly_type == "carrier_delay":
        return _collect_carrier_delay(anomaly.target_id)
    if anomaly.anomaly_type == "product_impact":
        return _collect_product_impact(anomaly.target_id)
    return (
        {"validation": {"found": False, "is_anomaly": False, "reason": "unsupported_anomaly_type"}},
        [],
    )


def collect_seed_evidence(anomaly: RCAAnomaly) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if anomaly.anomaly_type == "order_delay":
        return _collect_order_delay_seed(anomaly.target_id)
    if anomaly.anomaly_type == "supplier_risk":
        return _collect_supplier_risk_seed(anomaly.target_id)
    if anomaly.anomaly_type == "carrier_delay":
        return _collect_carrier_delay_seed(anomaly.target_id)
    if anomaly.anomaly_type == "product_impact":
        return _collect_product_impact_seed(anomaly.target_id)
    return (
        {"validation": {"found": False, "is_anomaly": False, "reason": "unsupported_anomaly_type"}},
        [],
    )


def run_probe(anomaly: RCAAnomaly, probe_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if anomaly.anomaly_type == "order_delay":
        return _run_order_delay_probe(anomaly.target_id, probe_name)
    if anomaly.anomaly_type == "supplier_risk":
        return _run_supplier_risk_probe(anomaly.target_id, probe_name)
    if anomaly.anomaly_type == "carrier_delay":
        return _run_carrier_delay_probe(anomaly.target_id, probe_name)
    if anomaly.anomaly_type == "product_impact":
        return _run_product_impact_probe(anomaly.target_id, probe_name)
    return {}, []


def _collect_order_delay_seed(order_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    validation, traces = _validate_order_delay(order_id)
    if not validation.get("found"):
        return {"validation": validation}, traces

    context_cypher = """
    MATCH (o:Order {id: $order_id})
    OPTIONAL MATCH (cust:Customer)-[:PLACED_ORDER]->(o)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(p:Product)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(car:Carrier)
    RETURN
        o.id AS order_id,
        collect(DISTINCT cust.name) AS customers,
        collect(DISTINCT p.name) AS products,
        collect(DISTINCT car.name) AS carriers,
        sum(coalesce(con.net_total, 0)) AS net_total,
        sum(coalesce(con.profit, 0)) AS profit
    """
    context_rows, context_trace = _query("collect_order_seed_context", context_cypher, {"order_id": order_id})
    event_summary, event_traces = _collect_order_event_summary(order_id)
    evidence_graph, graph_traces = _collect_local_evidence_graph("Order", order_id)
    traces.extend([context_trace])
    traces.extend(event_traces)
    traces.extend(graph_traces)
    return (
        {
            "validation": validation,
            "context": context_rows[0] if context_rows else {},
            "event_summary": event_summary,
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("order_delay"),
        },
        traces,
    )


def _collect_supplier_risk_seed(supplier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    traces: List[Dict[str, Any]] = []
    overview_cypher = """
    MATCH (s:Supplier)
    WHERE s.name CONTAINS $supplier_name
    OPTIONAL MATCH (s)-[sup:SUPPLIES_COMPONENT]->(comp:Component)
    OPTIONAL MATCH (comp)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(:Carrier)
    RETURN
        s.name AS supplier,
        count(DISTINCT comp) AS components,
        count(DISTINCT p) AS products,
        count(DISTINCT o) AS orders,
        avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate,
        avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost,
        sum(coalesce(con.net_total, 0)) AS net_exposure,
        sum(coalesce(con.profit, 0)) AS profit_exposure,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS delayed_orders_touched
    ORDER BY profit_exposure DESC
    LIMIT 1
    """
    overview_rows, overview_trace = _query("collect_supplier_seed_overview", overview_cypher, {"supplier_name": supplier_name})
    event_summary, event_traces = _collect_supplier_event_summary(supplier_name)
    evidence_graph, graph_traces = _collect_local_evidence_graph("Supplier", supplier_name)
    traces.extend([overview_trace])
    traces.extend(event_traces)
    traces.extend(graph_traces)
    if not overview_rows:
        return {"validation": {"found": False, "is_anomaly": False, "reason": "supplier_not_found"}}, traces
    overview = overview_rows[0]
    avg_defect_rate = _to_float(overview.get("avg_defect_rate"))
    delayed_orders_touched = _to_int(overview.get("delayed_orders_touched"))
    is_anomaly = avg_defect_rate >= 0.03 or delayed_orders_touched > 0
    return (
        {
            "validation": {
                "found": True,
                "is_anomaly": is_anomaly,
                "supplier": overview.get("supplier"),
                "avg_defect_rate": round(avg_defect_rate, 4),
                "delayed_orders_touched": delayed_orders_touched,
                "profit_exposure": round(_to_float(overview.get("profit_exposure")), 2),
            },
            "overview": overview,
            "event_summary": event_summary,
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("supplier_risk"),
        },
        traces,
    )


def _collect_carrier_delay_seed(carrier_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    traces: List[Dict[str, Any]] = []
    overview_cypher = """
    MATCH (car:Carrier)
    WHERE car.name CONTAINS $carrier_name
    OPTIONAL MATCH (o:Order)-[ship:SHIPPED_BY]->(car)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        car.name AS carrier,
        count(DISTINCT o) AS total_orders,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1
            OR (ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL AND ship.days_real > ship.days_scheduled)
            THEN o END) AS delayed_orders,
        avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
            THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
        sum(coalesce(con.net_total, 0)) AS net_at_risk,
        sum(coalesce(con.profit, 0)) AS profit_at_risk
    ORDER BY profit_at_risk DESC
    LIMIT 1
    """
    overview_rows, overview_trace = _query("collect_carrier_seed_overview", overview_cypher, {"carrier_name": carrier_name})
    event_summary, event_traces = _collect_carrier_event_summary(carrier_name)
    evidence_graph, graph_traces = _collect_local_evidence_graph("Carrier", carrier_name)
    traces.extend([overview_trace])
    traces.extend(event_traces)
    traces.extend(graph_traces)
    if not overview_rows:
        return {"validation": {"found": False, "is_anomaly": False, "reason": "carrier_not_found"}}, traces
    overview = overview_rows[0]
    delayed_orders = _to_int(overview.get("delayed_orders"))
    avg_delay_days = _to_float(overview.get("avg_delay_days"))
    is_anomaly = delayed_orders > 0 or avg_delay_days > 0
    return (
        {
            "validation": {
                "found": True,
                "is_anomaly": is_anomaly,
                "carrier": overview.get("carrier"),
                "delayed_orders": delayed_orders,
                "avg_delay_days": round(avg_delay_days, 2),
                "profit_at_risk": round(_to_float(overview.get("profit_at_risk")), 2),
            },
            "overview": overview,
            "event_summary": event_summary,
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("carrier_delay"),
        },
        traces,
    )


def _collect_product_impact_seed(product_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    traces: List[Dict[str, Any]] = []
    overview_cypher = """
    MATCH (p:Product)
    WHERE p.name CONTAINS $product_name
    OPTIONAL MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(:Carrier)
    RETURN
        p.name AS product,
        count(DISTINCT o) AS orders,
        sum(coalesce(con.net_total, 0)) AS net_revenue,
        sum(coalesce(con.profit, 0)) AS profit,
        count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS late_orders
    ORDER BY profit DESC
    LIMIT 1
    """
    overview_rows, overview_trace = _query("collect_product_seed_overview", overview_cypher, {"product_name": product_name})
    event_summary, event_traces = _collect_product_event_summary(product_name)
    evidence_graph, graph_traces = _collect_local_evidence_graph("Product", product_name)
    traces.extend([overview_trace])
    traces.extend(event_traces)
    traces.extend(graph_traces)
    if not overview_rows:
        return {"validation": {"found": False, "is_anomaly": False, "reason": "product_not_found"}}, traces
    overview = overview_rows[0]
    late_orders = _to_int(overview.get("late_orders"))
    profit = _to_float(overview.get("profit"))
    is_anomaly = late_orders > 0 or profit > 0
    return (
        {
            "validation": {
                "found": True,
                "is_anomaly": is_anomaly,
                "product": overview.get("product"),
                "late_orders": late_orders,
                "profit": round(profit, 2),
            },
            "overview": overview,
            "event_summary": event_summary,
            "evidence_graph": evidence_graph,
            "graph_metrics": evidence_graph.get("meta", {}).get("topology", {}),
            "policy": get_policy("product_impact"),
        },
        traces,
    )


def _run_order_delay_probe(order_id: str, probe_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if probe_name == "order_event_timeline":
        return _run_order_event_probe(order_id)

    if probe_name == "order_supply_dependency":
        component_cypher = """
        MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
        OPTIONAL MATCH (comp:Component)-[:USED_IN]->(p)
        OPTIONAL MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp)
        RETURN
            p.name AS product,
            comp.name AS component,
            s.name AS supplier,
            avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate,
            avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost
        """
        single_source_cypher = """
        MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
        MATCH (comp:Component)-[:USED_IN]->(p)
        CALL {
            WITH comp
            MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp)
            RETURN collect(DISTINCT s.name) AS suppliers, avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate
        }
        WITH comp, suppliers, avg_defect_rate
        WHERE size(suppliers) = 1
        OPTIONAL MATCH (comp)-[:USED_IN]->(affected:Product)<-[con:CONTAINS_PRODUCT]-(affected_order:Order)
        RETURN
            comp.name AS component,
            suppliers[0] AS sole_supplier,
            avg_defect_rate,
            count(DISTINCT affected) AS affected_products,
            count(DISTINCT affected_order) AS affected_orders,
            sum(coalesce(con.profit, 0)) AS profit_exposure
        ORDER BY affected_orders DESC, profit_exposure DESC
        """
        component_rows, component_trace = _query("probe_order_components", component_cypher, {"order_id": order_id})
        single_rows, single_trace = _query("probe_order_single_source", single_source_cypher, {"order_id": order_id})
        return {
            "components": component_rows,
            "single_source_components": single_rows,
        }, [component_trace, single_trace]

    if probe_name == "order_supplier_exposure":
        supplier_exposure_cypher = """
        MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
        MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p)
        OPTIONAL MATCH (s)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(prod:Product)<-[con:CONTAINS_PRODUCT]-(affected:Order)
        OPTIONAL MATCH (affected)-[ship:SHIPPED_BY]->(:Carrier)
        RETURN
            s.name AS supplier,
            count(DISTINCT affected) AS affected_orders,
            count(DISTINCT prod) AS affected_products,
            sum(coalesce(con.net_total, 0)) AS net_exposure,
            sum(coalesce(con.profit, 0)) AS profit_exposure,
            count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN affected END) AS delayed_orders_touched
        ORDER BY profit_exposure DESC, affected_orders DESC
        """
        rows, trace = _query("probe_order_supplier_exposure", supplier_exposure_cypher, {"order_id": order_id})
        return {"supplier_exposure": rows}, [trace]

    if probe_name == "order_carrier_delay":
        carrier_cypher = """
        MATCH (:Order {id: $order_id})-[:SHIPPED_BY]->(car:Carrier)
        OPTIONAL MATCH (o:Order)-[ship:SHIPPED_BY]->(car)
        OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
        RETURN
            car.name AS carrier,
            count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1
                OR (ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL AND ship.days_real > ship.days_scheduled)
                THEN o END) AS delayed_orders,
            avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
                THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
            sum(coalesce(con.net_total, 0)) AS net_at_risk,
            sum(coalesce(con.profit, 0)) AS profit_at_risk
        """
        rows, trace = _query("probe_order_carrier_context", carrier_cypher, {"order_id": order_id})
        return {"carrier_context": rows[0] if rows else {}}, [trace]

    if probe_name == "order_component_reuse":
        shared_component_cypher = """
        MATCH (o:Order {id: $order_id})-[:CONTAINS_PRODUCT]->(p:Product)
        MATCH (comp:Component)-[:USED_IN]->(p)
        MATCH (comp)-[:USED_IN]->(other:Product)
        RETURN
            comp.name AS component,
            count(DISTINCT other) AS used_in_products
        ORDER BY used_in_products DESC
        """
        rows, trace = _query("probe_order_shared_components", shared_component_cypher, {"order_id": order_id})
        return {"shared_components": rows}, [trace]

    return {}, []


def _run_supplier_risk_probe(supplier_name: str, probe_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if probe_name == "supplier_event_timeline":
        return _run_supplier_event_probe(supplier_name)

    if probe_name == "supplier_dependency":
        single_source_cypher = """
        MATCH (s:Supplier)
        WHERE s.name CONTAINS $supplier_name
        MATCH (s)-[sup:SUPPLIES_COMPONENT]->(comp:Component)
        CALL {
            WITH comp
            MATCH (other:Supplier)-[:SUPPLIES_COMPONENT]->(comp)
            RETURN count(DISTINCT other) AS supplier_count
        }
        OPTIONAL MATCH (comp)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)
        RETURN
            comp.name AS component,
            supplier_count,
            count(DISTINCT p) AS products,
            count(DISTINCT o) AS orders,
            sum(coalesce(con.profit, 0)) AS profit_exposure,
            avg(coalesce(sup.defect_rate, 0)) AS defect_rate
        ORDER BY supplier_count ASC, profit_exposure DESC
        """
        replacement_gap_cypher = """
        MATCH (s:Supplier)
        WHERE s.name CONTAINS $supplier_name
        MATCH (s)-[:SUPPLIES_COMPONENT]->(comp:Component)
        OPTIONAL MATCH (other:Supplier)-[:SUPPLIES_COMPONENT]->(comp)
        RETURN
            comp.name AS component,
            count(DISTINCT other) - 1 AS alternative_suppliers
        ORDER BY alternative_suppliers ASC, component
        """
        single_rows, single_trace = _query("probe_supplier_single_source", single_source_cypher, {"supplier_name": supplier_name})
        replacement_rows, replacement_trace = _query("probe_supplier_replacement_gap", replacement_gap_cypher, {"supplier_name": supplier_name})
        single_source_components = [row for row in single_rows if _to_int(row.get("supplier_count")) <= 1]
        replacement_gap = [row for row in replacement_rows if _to_int(row.get("alternative_suppliers")) <= 0]
        return {
            "single_source_components": single_source_components,
            "replacement_gap": replacement_gap,
        }, [single_trace, replacement_trace]

    if probe_name == "supplier_quality_spread":
        component_share_cypher = """
        MATCH (s:Supplier)
        WHERE s.name CONTAINS $supplier_name
        MATCH (s)-[sup:SUPPLIES_COMPONENT]->(comp:Component)
        OPTIONAL MATCH (comp)-[:USED_IN]->(p:Product)
        RETURN
            comp.name AS component,
            count(DISTINCT p) AS shared_products,
            avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost,
            avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate
        ORDER BY shared_products DESC, avg_mfg_cost DESC
        """
        rows, trace = _query("probe_supplier_component_share", component_share_cypher, {"supplier_name": supplier_name})
        return {"component_share": rows}, [trace]

    if probe_name == "supplier_delay_propagation":
        delay_mix_cypher = """
        MATCH (s:Supplier)
        WHERE s.name CONTAINS $supplier_name
        MATCH (s)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)-[ship:SHIPPED_BY]->(car:Carrier)
        RETURN
            car.name AS carrier,
            count(DISTINCT o) AS orders,
            count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS delayed_orders,
            avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
                THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
            sum(coalesce(con.profit, 0)) AS profit_exposure
        ORDER BY delayed_orders DESC, profit_exposure DESC
        """
        rows, trace = _query("probe_supplier_delay_mix", delay_mix_cypher, {"supplier_name": supplier_name})
        return {"delay_mix": rows}, [trace]

    if probe_name == "supplier_product_concentration":
        product_concentration_cypher = """
        MATCH (s:Supplier)
        WHERE s.name CONTAINS $supplier_name
        MATCH (s)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)
        RETURN
            p.name AS product,
            count(DISTINCT o) AS orders,
            sum(coalesce(con.profit, 0)) AS profit_exposure
        ORDER BY profit_exposure DESC, orders DESC
        """
        rows, trace = _query("probe_supplier_product_concentration", product_concentration_cypher, {"supplier_name": supplier_name})
        return {"product_concentration": rows}, [trace]

    return {}, []


def _run_carrier_delay_probe(carrier_name: str, probe_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if probe_name == "carrier_event_timeline":
        return _run_carrier_event_probe(carrier_name)

    if probe_name == "carrier_route_hotspot":
        route_cypher = """
        MATCH (car:Carrier)
        WHERE car.name CONTAINS $carrier_name
        MATCH (cust:Customer)-[:PLACED_ORDER]->(o:Order)-[ship:SHIPPED_BY]->(car)
        OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
        RETURN
            cust.province AS province,
            cust.city AS city,
            ship.trans_mode AS transport_mode,
            ship.ship_mode AS ship_mode,
            count(DISTINCT o) AS orders,
            count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS delayed_orders,
            avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
                THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
            sum(coalesce(con.profit, 0)) AS profit_exposure
        ORDER BY delayed_orders DESC, profit_exposure DESC
        """
        rows, trace = _query("probe_carrier_routes", route_cypher, {"carrier_name": carrier_name})
        return {"routes": rows}, [trace]

    if probe_name == "carrier_upstream_coverage":
        upstream_cypher = """
        MATCH (car:Carrier)
        WHERE car.name CONTAINS $carrier_name
        MATCH (o:Order)-[:SHIPPED_BY]->(car)
        OPTIONAL MATCH (o)-[:CONTAINS_PRODUCT]->(p:Product)
        OPTIONAL MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p)
        RETURN
            count(DISTINCT p) AS products,
            count(DISTINCT s) AS suppliers
        """
        rows, trace = _query("probe_carrier_upstream_coverage", upstream_cypher, {"carrier_name": carrier_name})
        return {"upstream_coverage": rows[0] if rows else {}}, [trace]

    return {}, []


def _run_product_impact_probe(product_name: str, probe_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if probe_name == "product_event_timeline":
        return _run_product_event_probe(product_name)

    if probe_name == "product_supply_dependency":
        supply_cypher = """
        MATCH (p:Product)
        WHERE p.name CONTAINS $product_name
        OPTIONAL MATCH (comp:Component)-[:USED_IN]->(p)
        OPTIONAL MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp)
        RETURN
            comp.name AS component,
            collect(DISTINCT s.name) AS suppliers,
            size(collect(DISTINCT s.name)) AS supplier_count,
            avg(coalesce(sup.defect_rate, 0)) AS avg_defect_rate,
            avg(coalesce(sup.mfg_cost, 0)) AS avg_mfg_cost
        """
        single_source_cypher = """
        MATCH (p:Product)
        WHERE p.name CONTAINS $product_name
        MATCH (comp:Component)-[:USED_IN]->(p)
        CALL {
            WITH comp
            MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(comp)
            RETURN collect(DISTINCT s.name) AS suppliers
        }
        WITH comp, suppliers
        WHERE size(suppliers) = 1
        RETURN comp.name AS component, suppliers[0] AS sole_supplier
        """
        supply_rows, supply_trace = _query("probe_product_supply_path", supply_cypher, {"product_name": product_name})
        single_rows, single_trace = _query("probe_product_single_source", single_source_cypher, {"product_name": product_name})
        return {
            "supply_path": supply_rows,
            "single_source_components": single_rows,
        }, [supply_trace, single_trace]

    if probe_name == "product_component_reuse":
        component_share_cypher = """
        MATCH (p:Product)
        WHERE p.name CONTAINS $product_name
        MATCH (comp:Component)-[:USED_IN]->(p)
        MATCH (comp)-[:USED_IN]->(other:Product)
        RETURN
            comp.name AS component,
            count(DISTINCT other) AS shared_products
        ORDER BY shared_products DESC
        """
        rows, trace = _query("probe_product_component_share", component_share_cypher, {"product_name": product_name})
        return {"component_share": rows}, [trace]

    if probe_name == "product_logistics_exposure":
        exposure_cypher = """
        MATCH (p:Product)
        WHERE p.name CONTAINS $product_name
        MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
        OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(car:Carrier)
        RETURN
            car.name AS carrier,
            count(DISTINCT o) AS orders,
            count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS late_orders,
            avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
                THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days,
            sum(coalesce(con.profit, 0)) AS profit_exposure
        ORDER BY late_orders DESC, profit_exposure DESC
        """
        mode_exposure_cypher = """
        MATCH (p:Product)
        WHERE p.name CONTAINS $product_name
        MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
        OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(:Carrier)
        RETURN
            ship.trans_mode AS transport_mode,
            ship.ship_mode AS ship_mode,
            count(DISTINCT o) AS orders,
            count(DISTINCT CASE WHEN coalesce(ship.late_risk, 0) = 1 THEN o END) AS late_orders,
            avg(CASE WHEN ship.days_real IS NOT NULL AND ship.days_scheduled IS NOT NULL
                THEN ship.days_real - ship.days_scheduled END) AS avg_delay_days
        ORDER BY late_orders DESC, avg_delay_days DESC
        """
        exposure_rows, exposure_trace = _query("probe_product_logistics_exposure", exposure_cypher, {"product_name": product_name})
        mode_rows, mode_trace = _query("probe_product_mode_exposure", mode_exposure_cypher, {"product_name": product_name})
        return {
            "logistics_exposure": exposure_rows,
            "mode_exposure": mode_rows,
        }, [exposure_trace, mode_trace]

    if probe_name == "product_supplier_concentration":
        supplier_concentration_cypher = """
        MATCH (p:Product)
        WHERE p.name CONTAINS $product_name
        MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p)
        OPTIONAL MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
        RETURN
            s.name AS supplier,
            count(DISTINCT o) AS orders,
            sum(coalesce(con.profit, 0)) AS profit_exposure
        ORDER BY profit_exposure DESC, orders DESC
        """
        rows, trace = _query("probe_product_supplier_concentration", supplier_concentration_cypher, {"product_name": product_name})
        return {"supplier_concentration": rows}, [trace]

    return {}, []
