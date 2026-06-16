import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .collectors import run_probe
from .schemas import RCAAnomaly
from .scorers import score_causes

ProgressCallback = Optional[Callable[[str, Dict[str, Any]], None]]


_PROBE_CATALOG: Dict[str, List[Dict[str, str]]] = {
    "order_delay": [
        {
            "name": "order_event_timeline",
            "label": "Order Event Timeline",
            "description": "Collect direct supplier notice, inspection, and delay-event evidence for the order.",
        },
        {
            "name": "order_supply_dependency",
            "label": "Order Supply Dependency",
            "description": "Inspect upstream components and single-source dependency for the delayed order.",
        },
        {
            "name": "order_supplier_exposure",
            "label": "Order Supplier Exposure",
            "description": "Measure which upstream suppliers touch the most delayed or exposed downstream orders.",
        },
        {
            "name": "order_carrier_delay",
            "label": "Order Carrier Delay",
            "description": "Check whether the assigned carrier shows broad late-delivery behavior.",
        },
        {
            "name": "order_component_reuse",
            "label": "Order Component Reuse",
            "description": "Test whether shared components amplify the disruption across products.",
        },
    ],
    "supplier_risk": [
        {
            "name": "supplier_event_timeline",
            "label": "Supplier Event Timeline",
            "description": "Collect direct supplier notice, inspection, and downstream delay evidence for the supplier.",
        },
        {
            "name": "supplier_dependency",
            "label": "Supplier Dependency",
            "description": "Inspect single-source dependency and missing replacement coverage around the supplier.",
        },
        {
            "name": "supplier_quality_spread",
            "label": "Supplier Quality Spread",
            "description": "Check whether the supplier's components are shared across many products and magnify defect risk.",
        },
        {
            "name": "supplier_delay_propagation",
            "label": "Supplier Delay Propagation",
            "description": "Trace whether the supplier's footprint clusters on delayed logistics chains.",
        },
        {
            "name": "supplier_product_concentration",
            "label": "Supplier Product Concentration",
            "description": "Measure whether profit exposure is concentrated on a small number of downstream products.",
        },
    ],
    "carrier_delay": [
        {
            "name": "carrier_event_timeline",
            "label": "Carrier Event Timeline",
            "description": "Collect direct delay-event evidence triggered by the carrier.",
        },
        {
            "name": "carrier_route_hotspot",
            "label": "Carrier Route Hotspot",
            "description": "Identify delayed route, province, city, and transport-mode clusters.",
        },
        {
            "name": "carrier_upstream_coverage",
            "label": "Carrier Upstream Coverage",
            "description": "Measure how many upstream products and suppliers are exposed through the carrier.",
        },
    ],
    "product_impact": [
        {
            "name": "product_event_timeline",
            "label": "Product Event Timeline",
            "description": "Collect direct supplier notice, inspection, and delay-event evidence for the product.",
        },
        {
            "name": "product_supply_dependency",
            "label": "Product Supply Dependency",
            "description": "Inspect supplier count, defect rate, and single-source component dependency for the product.",
        },
        {
            "name": "product_component_reuse",
            "label": "Product Component Reuse",
            "description": "Test whether shared components amplify the product impact across the portfolio.",
        },
        {
            "name": "product_logistics_exposure",
            "label": "Product Logistics Exposure",
            "description": "Inspect carrier and transport-mode delay concentration for the product.",
        },
        {
            "name": "product_supplier_concentration",
            "label": "Product Supplier Concentration",
            "description": "Measure whether profit exposure concentrates on a small set of upstream suppliers.",
        },
    ],
}

_CAUSE_PROBE_PRIORITY: Dict[str, Dict[str, List[str]]] = {
    "order_delay": {
        "event_chain_break": ["order_event_timeline"],
        "single_source_dependency": ["order_supply_dependency", "order_component_reuse"],
        "supplier_quality_issue": ["order_event_timeline", "order_supplier_exposure", "order_supply_dependency"],
        "carrier_delay": ["order_event_timeline", "order_carrier_delay"],
        "component_concentration": ["order_component_reuse", "order_supply_dependency"],
    },
    "supplier_risk": {
        "event_chain_break": ["supplier_event_timeline"],
        "quality_instability": ["supplier_event_timeline", "supplier_quality_spread", "supplier_dependency"],
        "dependency_concentration": ["supplier_dependency", "supplier_quality_spread"],
        "delay_propagation": ["supplier_event_timeline", "supplier_delay_propagation"],
        "replacement_gap": ["supplier_dependency"],
        "cost_volatility": ["supplier_product_concentration"],
    },
    "carrier_delay": {
        "event_chain_break": ["carrier_event_timeline"],
        "route_congestion": ["carrier_event_timeline", "carrier_route_hotspot"],
        "mode_instability": ["carrier_event_timeline", "carrier_route_hotspot"],
        "exposure_concentration": ["carrier_event_timeline", "carrier_upstream_coverage", "carrier_route_hotspot"],
    },
    "product_impact": {
        "event_chain_break": ["product_event_timeline"],
        "single_source_dependency": ["product_supply_dependency"],
        "upstream_supplier_risk": ["product_event_timeline", "product_supply_dependency", "product_supplier_concentration"],
        "component_concentration": ["product_component_reuse"],
        "logistics_delay_exposure": ["product_event_timeline", "product_logistics_exposure"],
        "margin_exposure": ["product_supplier_concentration", "product_logistics_exposure"],
    },
}

_STEP_LIMITS = {
    "order_delay": 3,
    "supplier_risk": 3,
    "carrier_delay": 2,
    "product_impact": 3,
}


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _emit(callback: ProgressCallback, stage: str, message: str, **payload: Any) -> None:
    if callback is None:
        return
    try:
        data = {"stage": stage, "message": message}
        data.update(payload)
        callback("status", data)
    except Exception:
        return


def list_available_probes(anomaly_type: str) -> List[Dict[str, str]]:
    return [dict(item) for item in _PROBE_CATALOG.get(anomaly_type, [])]


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    cleaned = str(text).strip()
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if not match:
        return {}
    candidate = match.group(0)
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _brief_causes(candidates: List[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for cause in candidates[:limit]:
        items.append(
            {
                "cause": cause.get("cause"),
                "label": cause.get("label", cause.get("cause")),
                "score": round(_to_float(cause.get("score")), 2),
                "evidence": list(cause.get("evidence") or [])[:1],
            }
        )
    return items


def _evidence_snapshot(evidence: Dict[str, Any]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    validation = evidence.get("validation")
    if isinstance(validation, dict):
        snapshot["validation"] = validation
    graph_metrics = evidence.get("graph_metrics")
    if isinstance(graph_metrics, dict):
        snapshot["graph_metrics"] = graph_metrics

    for key, value in evidence.items():
        if key in {"validation", "graph_metrics", "policy", "evidence_graph"}:
            continue
        if isinstance(value, list):
            snapshot[key] = {"count": len(value), "sample": value[:1]}
        elif isinstance(value, dict) and value:
            snapshot[key] = dict(list(value.items())[:6])
    return snapshot


def _fallback_probe_choice(
    anomaly_type: str,
    candidates: List[Dict[str, Any]],
    used_probes: List[str],
) -> Dict[str, Any]:
    catalog = list_available_probes(anomaly_type)
    available = [item for item in catalog if item["name"] not in used_probes]
    if not available:
        return {"stop": True, "reason": "no_probe_remaining"}

    priority = _CAUSE_PROBE_PRIORITY.get(anomaly_type, {})
    for cause in candidates:
        cause_key = str(cause.get("cause") or "")
        for probe_name in priority.get(cause_key, []):
            if probe_name in used_probes:
                continue
            selected = next((item for item in available if item["name"] == probe_name), None)
            if selected:
                return {
                    "stop": False,
                    "probe": selected["name"],
                    "probe_label": selected["label"],
                    "hypothesis": cause.get("label", cause_key),
                    "reason": f"Need targeted evidence for {cause.get('label', cause_key)}.",
                    "decision_source": "fallback",
                }

    selected = available[0]
    return {
        "stop": False,
        "probe": selected["name"],
        "probe_label": selected["label"],
        "hypothesis": selected["description"],
        "reason": "Collect the next highest-signal probe from the safe catalog.",
        "decision_source": "fallback",
    }


def _plan_probe_with_llm(
    llm: Any,
    anomaly: RCAAnomaly,
    evidence: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    used_probes: List[str],
) -> Dict[str, Any]:
    fallback = _fallback_probe_choice(anomaly.anomaly_type, candidates, used_probes)
    available = [item for item in list_available_probes(anomaly.anomaly_type) if item["name"] not in used_probes]
    if not llm or not available:
        return fallback

    prompt = f"""You are a root-cause investigation planner for a supply-chain knowledge graph.
Pick the next safe investigation probe from the provided catalog.
Rules:
1. Use only the probe names exactly as listed in available_probes.
2. Prefer probes that can distinguish between the top competing causes.
3. If the evidence is already sufficient and clearly separated, return stop=true.
4. Return JSON only.

Return schema:
{{
  "stop": false,
  "probe": "probe_name",
  "hypothesis": "short hypothesis being tested",
  "reason": "why this probe is the best next step"
}}

Anomaly:
{json.dumps(anomaly.to_dict(), ensure_ascii=False)}

Available probes:
{json.dumps(available, ensure_ascii=False)}

Current candidate causes:
{json.dumps(_brief_causes(candidates, limit=4), ensure_ascii=False)}

Current evidence snapshot:
{json.dumps(_evidence_snapshot(evidence), ensure_ascii=False)}

Completed investigation steps:
{json.dumps(steps[-3:], ensure_ascii=False)}
"""
    try:
        message = llm.invoke(prompt)
        content = getattr(message, "content", "") or ""
        parsed = _extract_json_object(content)
    except Exception:
        return fallback

    probe_name = str(parsed.get("probe") or "").strip()
    selected = next((item for item in available if item["name"] == probe_name), None)
    if parsed.get("stop") is True and steps:
        return {
            "stop": True,
            "reason": str(parsed.get("reason") or "llm_marked_evidence_sufficient"),
            "decision_source": "llm",
        }
    if not selected:
        return fallback
    return {
        "stop": False,
        "probe": selected["name"],
        "probe_label": selected["label"],
        "hypothesis": str(parsed.get("hypothesis") or selected["description"]),
        "reason": str(parsed.get("reason") or "Probe selected by LLM investigation planner."),
        "decision_source": "llm",
    }


def _confidence_gap(candidates: List[Dict[str, Any]]) -> Tuple[float, float]:
    top = _to_float(candidates[0].get("score")) if candidates else 0.0
    second = _to_float(candidates[1].get("score")) if len(candidates) > 1 else 0.0
    return top, top - second


def _should_stop(
    anomaly_type: str,
    candidates: List[Dict[str, Any]],
    steps: List[Dict[str, Any]],
    remaining_probes: int,
) -> Tuple[bool, str]:
    if remaining_probes <= 0:
        return True, "all_safe_probes_exhausted"
    if len(steps) >= _STEP_LIMITS.get(anomaly_type, 3):
        return True, "step_limit_reached"
    if not steps:
        return False, "minimum_one_probe"
    top_score, gap = _confidence_gap(candidates)
    if top_score >= 0.84 and gap >= 0.12:
        return True, "top_cause_separated"
    return False, "more_evidence_needed"


def _first_row(rows: Any) -> Dict[str, Any]:
    if isinstance(rows, list) and rows:
        first = rows[0]
        return first if isinstance(first, dict) else {}
    return {}


def _summarize_probe_findings(probe_name: str, patch: Dict[str, Any]) -> List[str]:
    findings: List[str] = []
    event_summary = patch.get("event_summary") or {}

    if probe_name in {"order_event_timeline", "supplier_event_timeline", "carrier_event_timeline", "product_event_timeline"}:
        if event_summary:
            findings.append(
                "Direct events: "
                f"notices={int(event_summary.get('supplier_notice_count') or 0)}, "
                f"fail_inspections={int(event_summary.get('fail_inspection_count') or 0)}, "
                f"delay_events={int(event_summary.get('delay_event_count') or 0)}."
            )
        top_chain = _first_row(patch.get("event_chain") or [])
        if top_chain:
            findings.append(str(top_chain.get("narrative") or "Built one direct evidence chain."))
        top_event = _first_row(patch.get("direct_evidence") or [])
        if top_event:
            findings.append(
                f"Top direct evidence is {top_event.get('event_type')} {top_event.get('id')} with severity "
                f"{top_event.get('severity') or top_event.get('result')}."
            )
        return findings[:3]

    if probe_name == "order_supply_dependency":
        single_rows = patch.get("single_source_components") or []
        if single_rows:
            top = _first_row(single_rows)
            findings.append(
                f"Found {len(single_rows)} single-source components; top component {top.get('component')} depends on {top.get('sole_supplier')}."
            )
        component_rows = patch.get("components") or []
        if component_rows:
            top = _first_row(component_rows)
            findings.append(
                f"Observed upstream path product={top.get('product')} component={top.get('component')} supplier={top.get('supplier')}."
            )
    elif probe_name == "order_supplier_exposure":
        top = _first_row(patch.get("supplier_exposure") or [])
        if top:
            findings.append(
                f"Supplier {top.get('supplier')} touches {int(top.get('affected_orders') or 0)} affected orders and profit exposure {round(_to_float(top.get('profit_exposure')), 2)}."
            )
    elif probe_name == "order_carrier_delay":
        carrier = patch.get("carrier_context") or {}
        if carrier:
            findings.append(
                f"Carrier {carrier.get('carrier')} shows {int(carrier.get('delayed_orders') or 0)} delayed orders with average delay {round(_to_float(carrier.get('avg_delay_days')), 2)} days."
            )
    elif probe_name == "order_component_reuse":
        top = _first_row(patch.get("shared_components") or [])
        if top:
            findings.append(
                f"Component {top.get('component')} is reused by {int(top.get('used_in_products') or 0)} products."
            )
    elif probe_name == "supplier_dependency":
        single_rows = patch.get("single_source_components") or []
        replacement = patch.get("replacement_gap") or []
        findings.append(f"Detected {len(single_rows)} single-source component paths around the supplier.")
        if replacement:
            uncovered = sum(1 for row in replacement if int(row.get("alternative_suppliers") or 0) <= 0)
            findings.append(f"{uncovered} components have no alternative supplier.")
    elif probe_name == "supplier_quality_spread":
        top = _first_row(patch.get("component_share") or [])
        if top:
            findings.append(
                f"Component {top.get('component')} spans {int(top.get('shared_products') or 0)} products with average defect rate {round(_to_float(top.get('avg_defect_rate')), 4)}."
            )
    elif probe_name == "supplier_delay_propagation":
        top = _first_row(patch.get("delay_mix") or [])
        if top:
            findings.append(
                f"Carrier {top.get('carrier')} carries {int(top.get('delayed_orders') or 0)} delayed orders linked to the supplier."
            )
    elif probe_name == "supplier_product_concentration":
        top = _first_row(patch.get("product_concentration") or [])
        if top:
            findings.append(
                f"Top exposed downstream product is {top.get('product')} with profit exposure {round(_to_float(top.get('profit_exposure')), 2)}."
            )
    elif probe_name == "carrier_route_hotspot":
        top = _first_row(patch.get("routes") or [])
        if top:
            findings.append(
                f"Hotspot route is {top.get('province')}/{top.get('city')} via {top.get('transport_mode')} with {int(top.get('delayed_orders') or 0)} delayed orders."
            )
    elif probe_name == "carrier_upstream_coverage":
        coverage = patch.get("upstream_coverage") or {}
        if coverage:
            findings.append(
                f"The carrier connects {int(coverage.get('products') or 0)} products and {int(coverage.get('suppliers') or 0)} suppliers."
            )
    elif probe_name == "product_supply_dependency":
        single_rows = patch.get("single_source_components") or []
        supply_rows = patch.get("supply_path") or []
        findings.append(f"Detected {len(single_rows)} single-source components for the product.")
        if supply_rows:
            top = _first_row(supply_rows)
            findings.append(
                f"Component {top.get('component')} has supplier_count={int(top.get('supplier_count') or 0)} and avg_defect_rate={round(_to_float(top.get('avg_defect_rate')), 4)}."
            )
    elif probe_name == "product_component_reuse":
        top = _first_row(patch.get("component_share") or [])
        if top:
            findings.append(
                f"Component {top.get('component')} is shared across {int(top.get('shared_products') or 0)} products."
            )
    elif probe_name == "product_logistics_exposure":
        logistics = _first_row(patch.get("logistics_exposure") or [])
        if logistics:
            findings.append(
                f"Carrier {logistics.get('carrier')} contributes {int(logistics.get('late_orders') or 0)} late orders with average delay {round(_to_float(logistics.get('avg_delay_days')), 2)} days."
            )
        mode = _first_row(patch.get("mode_exposure") or [])
        if mode:
            findings.append(
                f"Most exposed mode is {mode.get('transport_mode')} / {mode.get('ship_mode')}."
            )
    elif probe_name == "product_supplier_concentration":
        top = _first_row(patch.get("supplier_concentration") or [])
        if top:
            findings.append(
                f"Supplier {top.get('supplier')} has product-linked profit exposure {round(_to_float(top.get('profit_exposure')), 2)}."
            )

    if not findings:
        findings.append(f"Probe returned evidence keys: {', '.join(sorted(patch.keys())) or 'none'}.")
    return findings[:3]


def _score_map(candidates: List[Dict[str, Any]]) -> Dict[str, float]:
    return {str(item.get("cause") or ""): _to_float(item.get("score")) for item in candidates}


def _describe_score_shift(
    before: List[Dict[str, Any]],
    after: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    before_map = _score_map(before)
    after_map = _score_map(after)
    labels = {
        str(item.get("cause") or ""): item.get("label", item.get("cause"))
        for item in before + after
    }
    supported: List[Dict[str, Any]] = []
    weakened: List[Dict[str, Any]] = []
    for cause_key, after_score in after_map.items():
        delta = round(after_score - before_map.get(cause_key, 0.0), 2)
        item = {
            "cause": cause_key,
            "label": labels.get(cause_key, cause_key),
            "delta": delta,
            "score": round(after_score, 2),
        }
        if delta >= 0.03:
            supported.append(item)
        elif delta <= -0.03:
            weakened.append(item)
    supported.sort(key=lambda item: (-item["delta"], -item["score"]))
    weakened.sort(key=lambda item: (item["delta"], -item["score"]))
    return supported[:3], weakened[:3]


def _merge_evidence(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        merged[key] = value
    return merged


def _top_cause_brief(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not candidates:
        return {}
    top = candidates[0]
    return {
        "cause": top.get("cause"),
        "label": top.get("label", top.get("cause")),
        "score": round(_to_float(top.get("score")), 2),
    }


def run_investigation(
    llm: Any,
    anomaly: RCAAnomaly,
    seed_evidence: Dict[str, Any],
    initial_candidates: List[Dict[str, Any]],
    progress_callback: ProgressCallback = None,
) -> Dict[str, Any]:
    current_evidence = dict(seed_evidence)
    current_candidates = list(initial_candidates)
    current_actions = list((score_causes(anomaly.anomaly_type, current_evidence) or {}).get("recommended_actions") or [])
    steps: List[Dict[str, Any]] = []
    query_traces: List[Dict[str, Any]] = []
    used_probes: List[str] = []
    remaining = list_available_probes(anomaly.anomaly_type)
    top_before = _top_cause_brief(current_candidates)
    stop_reason = "no_probe_catalog"

    if not remaining:
        return {
            "evidence": current_evidence,
            "candidate_causes": current_candidates,
            "recommended_actions": current_actions,
            "steps": steps,
            "summary": {
                "step_count": 0,
                "used_probes": [],
                "stop_reason": stop_reason,
                "initial_top_cause": top_before,
                "final_top_cause": top_before,
                "confidence_delta": 0.0,
                "decision_mode": "none",
            },
            "query_traces": query_traces,
        }

    while True:
        should_stop, stop_reason = _should_stop(
            anomaly.anomaly_type,
            current_candidates,
            steps,
            len(remaining),
        )
        if should_stop:
            break

        decision = _plan_probe_with_llm(
            llm=llm,
            anomaly=anomaly,
            evidence=current_evidence,
            candidates=current_candidates,
            steps=steps,
            used_probes=used_probes,
        )
        if decision.get("stop") and steps:
            stop_reason = str(decision.get("reason") or "llm_marked_evidence_sufficient")
            break

        probe_name = str(decision.get("probe") or "").strip()
        probe_label = str(decision.get("probe_label") or probe_name)
        if not probe_name:
            stop_reason = "probe_selection_failed"
            break

        _emit(
            progress_callback,
            "rca-investigation",
            f"Step {len(steps) + 1}: investigating via {probe_label}",
            step=len(steps) + 1,
            probe=probe_name,
        )

        patch, traces = run_probe(anomaly, probe_name)
        query_traces.extend(traces)
        used_probes.append(probe_name)
        remaining = [item for item in remaining if item["name"] != probe_name]
        next_evidence = _merge_evidence(current_evidence, patch)
        next_scoring = score_causes(anomaly.anomaly_type, next_evidence)
        next_candidates = list(next_scoring.get("candidate_causes") or [])
        next_actions = list(next_scoring.get("recommended_actions") or [])
        supported, weakened = _describe_score_shift(current_candidates, next_candidates)
        findings = _summarize_probe_findings(probe_name, patch)

        step = {
            "step": len(steps) + 1,
            "probe": probe_name,
            "probe_label": probe_label,
            "decision_source": decision.get("decision_source", "fallback"),
            "hypothesis": decision.get("hypothesis") or probe_label,
            "reason": decision.get("reason") or "",
            "evidence_keys": sorted(patch.keys()),
            "findings": findings,
            "supported_causes": supported,
            "weakened_causes": weakened,
            "top_cause_after": _top_cause_brief(next_candidates),
        }
        steps.append(step)
        current_evidence = next_evidence
        current_candidates = next_candidates
        current_actions = next_actions

    top_after = _top_cause_brief(current_candidates)
    confidence_delta = round(
        _to_float(top_after.get("score")) - _to_float(top_before.get("score")),
        2,
    )
    decision_mode = "llm" if any(step.get("decision_source") == "llm" for step in steps) else "fallback"
    return {
        "evidence": current_evidence,
        "candidate_causes": current_candidates,
        "recommended_actions": current_actions,
        "steps": steps,
        "summary": {
            "step_count": len(steps),
            "used_probes": used_probes,
            "stop_reason": stop_reason,
            "initial_top_cause": top_before,
            "final_top_cause": top_after,
            "confidence_delta": confidence_delta,
            "decision_mode": decision_mode,
        },
        "query_traces": query_traces,
    }
