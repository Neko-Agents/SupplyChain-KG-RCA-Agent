from typing import Any, Callable, Dict, List, Optional

from .collectors import collect_seed_evidence
from .investigator import run_investigation
from .renderer import render_report
from .router import classify_intent
from .schemas import RCAAnomaly, RCAIntent
from .scorers import score_causes

ProgressCallback = Optional[Callable[[str, Dict[str, Any]], None]]


def _build_evidence_overview(evidence: Dict[str, Any]) -> Dict[str, Any]:
    sections: Dict[str, Any] = {}
    for key, value in evidence.items():
        if isinstance(value, list):
            sections[key] = {
                "count": len(value),
                "sample": value[:2],
            }

    headline_metrics: Dict[str, Any] = {}
    for key in (
        "validation",
        "context",
        "overview",
        "carrier_context",
        "upstream_coverage",
        "graph_metrics",
    ):
        value = evidence.get(key)
        if isinstance(value, dict) and value:
            headline_metrics[key] = value

    return {
        "sections": sections,
        "headline_metrics": headline_metrics,
    }


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _estimate_severity(
    validation: Dict[str, Any],
    graph_metrics: Dict[str, Any],
    candidate_causes: List[Dict[str, Any]],
) -> str:
    score = 0
    profit_exposure = max(
        _to_float(validation.get("profit")),
        _to_float(validation.get("profit_exposure")),
        _to_float(validation.get("profit_at_risk")),
    )
    delay_days = max(
        _to_float(validation.get("delay_days")),
        _to_float(validation.get("avg_delay_days")),
    )
    delayed_orders = max(
        _to_float(validation.get("delayed_orders")),
        _to_float(validation.get("late_orders")),
        _to_float(validation.get("delayed_orders_touched")),
    )
    single_source = _to_float(graph_metrics.get("single_source_components"))
    component_span = _to_float(graph_metrics.get("max_component_product_span"))
    top_cause_score = max((_to_float(item.get("score")) for item in candidate_causes), default=0.0)

    if profit_exposure >= 10_000_000:
        score += 3
    elif profit_exposure >= 1_000_000:
        score += 2
    elif profit_exposure > 0:
        score += 1

    if delayed_orders >= 20:
        score += 2
    elif delayed_orders > 0:
        score += 1

    if delay_days >= 5:
        score += 2
    elif delay_days > 0:
        score += 1

    if single_source >= 3:
        score += 2
    elif single_source > 0:
        score += 1

    if component_span >= 4:
        score += 1

    if top_cause_score >= 0.8:
        score += 2
    elif top_cause_score >= 0.6:
        score += 1

    if score >= 8:
        return "critical"
    if score >= 5:
        return "high"
    if score >= 3:
        return "medium"
    return "low"


def _build_risk_signals(candidate_causes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    for index, cause in enumerate(candidate_causes[:4], start=1):
        score = _to_float(cause.get("score"))
        confidence = "high" if score >= 0.75 else "medium" if score >= 0.5 else "watch"
        evidence_lines = [str(item) for item in (cause.get("evidence") or [])[:2]]
        signals.append(
            {
                "id": f"signal-{index}",
                "signal_type": cause.get("cause"),
                "label": cause.get("label", cause.get("cause")),
                "confidence": confidence,
                "score": round(score, 2),
                "evidence": evidence_lines,
            }
        )
    return signals


def _build_incident_summary(
    anomaly: Dict[str, Any],
    severity: str,
    candidate_causes: List[Dict[str, Any]],
    recommended_actions: List[str],
) -> Dict[str, Any]:
    top_cause = candidate_causes[0] if candidate_causes else {}
    return {
        "incident_id": f"incident:{anomaly.get('type')}:{anomaly.get('target_id')}",
        "title": f"{anomaly.get('target_type')} {anomaly.get('target_id')} {anomaly.get('type')}",
        "severity": severity,
        "target_type": anomaly.get("target_type"),
        "target_id": anomaly.get("target_id"),
        "signal_count": len(candidate_causes),
        "top_cause": {
            "cause": top_cause.get("cause"),
            "label": top_cause.get("label"),
            "score": top_cause.get("score"),
        }
        if top_cause
        else {},
        "recommended_action_count": len(recommended_actions),
    }


def _minimal_investigation() -> Dict[str, Any]:
    return {
        "summary": {
            "step_count": 0,
            "used_probes": [],
            "stop_reason": "not_started",
            "initial_top_cause": {},
            "final_top_cause": {},
            "confidence_delta": 0.0,
            "decision_mode": "none",
        },
        "steps": [],
    }


def _find_target_node_id(
    graph_data: Dict[str, Any], target_type: str, target_id: str
) -> str:
    target_id_lower = str(target_id or "").strip().lower()
    for node in graph_data.get("nodes") or []:
        labels = node.get("labels") or []
        if target_type not in labels:
            continue
        props = node.get("props") or {}
        candidate = props.get("id") if target_type == "Order" else props.get("name") or props.get("id")
        candidate_text = str(candidate or "").strip().lower()
        if candidate_text == target_id_lower:
            return str(node.get("id") or "")
        if target_type != "Order" and candidate_text and target_id_lower in candidate_text:
            return str(node.get("id") or "")
    return ""


def _augment_graph_with_rca_layers(
    anomaly: Dict[str, Any],
    evidence_graph: Dict[str, Any],
    validation: Dict[str, Any],
    risk_signals: List[Dict[str, Any]],
    candidate_causes: List[Dict[str, Any]],
    recommended_actions: List[str],
    incident_summary: Dict[str, Any],
    investigation_summary: Dict[str, Any],
    investigation_steps: List[Dict[str, Any]],
) -> Dict[str, Any]:
    graph_copy = {
        "nodes": list((evidence_graph or {}).get("nodes") or []),
        "edges": list((evidence_graph or {}).get("edges") or []),
        "meta": dict((evidence_graph or {}).get("meta") or {}),
    }
    target_type = str(anomaly.get("target_type") or "")
    target_id = str(anomaly.get("target_id") or "")
    target_node_id = _find_target_node_id(graph_copy, target_type, target_id)

    incident_node_id = incident_summary.get("incident_id") or f"incident:{target_type}:{target_id}"
    graph_copy["nodes"].append(
        {
            "id": incident_node_id,
            "labels": ["RCAIncident"],
            "props": {
                "title": incident_summary.get("title"),
                "severity": incident_summary.get("severity"),
                "anomaly_type": anomaly.get("type"),
                "target_type": target_type,
                "target_id": target_id,
                "validated": bool(validation.get("is_anomaly")),
            },
        }
    )
    if target_node_id:
        graph_copy["edges"].append(
            {
                "id": f"edge:{incident_node_id}:target",
                "source": incident_node_id,
                "target": target_node_id,
                "type": "TARGETS",
                "props": {"virtual": True},
            }
        )

    for index, signal in enumerate(risk_signals, start=1):
        signal_node_id = f"{incident_node_id}:signal:{index}"
        graph_copy["nodes"].append(
            {
                "id": signal_node_id,
                "labels": ["RiskSignal"],
                "props": {
                    "label": signal.get("label"),
                    "signal_type": signal.get("signal_type"),
                    "confidence": signal.get("confidence"),
                    "score": signal.get("score"),
                    "evidence_summary": " | ".join(signal.get("evidence") or []),
                },
            }
        )
        graph_copy["edges"].append(
            {
                "id": f"edge:{incident_node_id}:signal:{index}",
                "source": incident_node_id,
                "target": signal_node_id,
                "type": "HAS_SIGNAL",
                "props": {"virtual": True},
            }
        )
        if target_node_id:
            graph_copy["edges"].append(
                {
                    "id": f"edge:{signal_node_id}:target",
                    "source": signal_node_id,
                    "target": target_node_id,
                    "type": "OBSERVED_ON",
                    "props": {"virtual": True},
                }
            )

    for index, action in enumerate(recommended_actions[:3], start=1):
        action_node_id = f"{incident_node_id}:action:{index}"
        graph_copy["nodes"].append(
            {
                "id": action_node_id,
                "labels": ["SuggestedAction"],
                "props": {
                    "text": action,
                    "priority": index,
                },
            }
        )
        graph_copy["edges"].append(
            {
                "id": f"edge:{incident_node_id}:action:{index}",
                "source": incident_node_id,
                "target": action_node_id,
                "type": "RECOMMENDS_ACTION",
                "props": {"virtual": True},
            }
        )

    graph_copy["meta"]["augmented_with_rca"] = True
    graph_copy["meta"]["virtual_layers"] = ["incident", "risk_signal", "action"]
    graph_copy["meta"]["target_node_id"] = target_node_id
    graph_copy["meta"]["incident_summary"] = incident_summary
    graph_copy["meta"]["top_risk_signals"] = risk_signals[:3]
    graph_copy["meta"]["investigation_summary"] = investigation_summary
    graph_copy["meta"]["investigation_steps"] = investigation_steps[:4]
    graph_copy["meta"]["candidate_causes"] = [
        {
            "cause": item.get("cause"),
            "label": item.get("label"),
            "score": item.get("score"),
            "evidence": list(item.get("evidence") or []),
            "explanation_hint": item.get("explanation_hint", ""),
            "supporting_nodes": list(item.get("supporting_nodes") or []),
            "supporting_edges": list(item.get("supporting_edges") or []),
            "evidence_chain": list(item.get("evidence_chain") or []),
            "source_records": list(item.get("source_records") or []),
            "evidence_mode": item.get("evidence_mode", "structural"),
        }
        for item in candidate_causes[:3]
    ]
    graph_copy["meta"]["node_count"] = len(graph_copy["nodes"])
    graph_copy["meta"]["edge_count"] = len(graph_copy["edges"])
    return graph_copy


class RCAEngine:
    def __init__(self, llm: Any):
        self.llm = llm

    def detect_intent(self, question: str, history_text: str = "") -> RCAIntent:
        return classify_intent(question, history_text=history_text)

    def _emit(self, callback: ProgressCallback, event: str, payload: Dict[str, Any]) -> None:
        if callback is None:
            return
        try:
            callback(event, payload)
        except Exception:
            return

    def _build_route_trace(self, intent: RCAIntent) -> Dict[str, Any]:
        route = intent.to_dict()
        route["engine"] = "rca"
        route["summary"] = f"RCA -> {intent.subtype} ({intent.target_type}:{intent.target_id})"
        return route

    def _build_structured_result(
        self,
        intent: RCAIntent,
        anomaly: RCAAnomaly,
        evidence: Dict[str, Any],
        candidate_causes: List[Dict[str, Any]],
        recommended_actions: List[str],
        investigation: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        investigation = investigation or _minimal_investigation()
        validation = evidence.get("validation", {})
        graph_metrics = evidence.get("graph_metrics", {})
        severity = _estimate_severity(validation, graph_metrics, candidate_causes)
        anomaly.severity = severity
        anomaly_data = anomaly.to_dict()
        risk_signals = _build_risk_signals(candidate_causes)
        incident_summary = _build_incident_summary(
            anomaly_data,
            severity,
            candidate_causes,
            recommended_actions,
        )
        evidence_graph = _augment_graph_with_rca_layers(
            anomaly_data,
            evidence.get("evidence_graph", {}),
            validation,
            risk_signals,
            candidate_causes,
            recommended_actions,
            incident_summary,
            investigation.get("summary", {}),
            investigation.get("steps", []),
        )
        return {
            "handled": True,
            "engine": "rca",
            "route": self._build_route_trace(intent),
            "intent": intent.to_dict(),
            "anomaly": anomaly_data,
            "validation": validation,
            "evidence_overview": _build_evidence_overview(evidence),
            "graph_metrics": graph_metrics,
            "incident_summary": incident_summary,
            "risk_signals": risk_signals,
            "evidence_graph": evidence_graph,
            "candidate_causes": candidate_causes,
            "recommended_actions": recommended_actions,
            "investigation": investigation.get("summary", {}),
            "investigation_steps": investigation.get("steps", []),
        }

    def build_anomaly(self, question: str, intent: RCAIntent) -> RCAAnomaly:
        symptom_map = {
            "order_delay": "user_reported_order_delay",
            "supplier_risk": "user_reported_supplier_risk",
            "carrier_delay": "user_reported_carrier_delay",
            "product_impact": "user_reported_product_impact",
        }
        return RCAAnomaly(
            anomaly_type=intent.subtype,
            target_type=intent.target_type,
            target_id=intent.target_id,
            symptom=symptom_map.get(intent.subtype, "user_reported_anomaly"),
            question=question,
        )

    def run(
        self,
        question: str,
        history_text: str = "",
        progress_callback: ProgressCallback = None,
        intent: Optional[RCAIntent] = None,
        render_reply: bool = True,
    ) -> Dict[str, Any]:
        intent = intent or self.detect_intent(question, history_text=history_text)
        if intent.route_type != "rca" or not intent.subtype or not intent.target_id:
            return {"handled": False}

        anomaly = self.build_anomaly(question, intent)
        self._emit(
            progress_callback,
            "status",
            {"stage": "rca-routing", "message": f"\u5df2\u8bc6\u522b RCA \u95ee\u9898\uff0c\u8fdb\u5165 {intent.subtype} \u5206\u6790\u94fe\u8def"},
        )

        evidence, query_traces = collect_seed_evidence(anomaly)
        validation = evidence.get("validation", {})
        if not validation.get("found"):
            reply = (
                f"\u672a\u627e\u5230\u53ef\u7528\u4e8e\u6839\u56e0\u5206\u6790\u7684\u76ee\u6807\u5bf9\u8c61\uff1a"
                f"{anomaly.target_type} {anomaly.target_id}\u3002"
            )
            structured = self._build_structured_result(
                intent,
                anomaly,
                evidence,
                [],
                [],
                investigation=_minimal_investigation(),
            )
            trace = {
                "tool": "RCA_Engine",
                "engine": "rca",
                "type": "pipeline",
                "mode": "rca",
                "route": self._build_route_trace(intent),
                "route_summary": self._build_route_trace(intent)["summary"],
                "intent": intent.to_dict(),
                "anomaly": structured.get("anomaly", anomaly.to_dict()),
                "validation": validation,
                "incident_summary": structured.get("incident_summary", {}),
                "risk_signals": structured.get("risk_signals", []),
                "investigation": structured.get("investigation", {}),
                "investigation_steps": structured.get("investigation_steps", []),
                "evidence_graph_meta": (structured.get("evidence_graph") or {}).get("meta", {}),
                "queries": query_traces,
            }
            return {"handled": True, "reply": reply, "trace": trace, "rca": structured}

        self._emit(
            progress_callback,
            "status",
            {"stage": "rca-evidence", "message": "\u5df2\u5b8c\u6210\u521d\u59cb\u5f02\u5e38\u786e\u8ba4\uff0c\u6b63\u5728\u751f\u6210\u9996\u8f6e\u5047\u8bbe"},
        )
        seed_scoring = score_causes(anomaly.anomaly_type, evidence)
        seed_candidates = seed_scoring.get("candidate_causes", [])

        self._emit(
            progress_callback,
            "status",
            {"stage": "rca-investigation", "message": "\u6b63\u5728\u6309\u5047\u8bbe\u9010\u6b65\u8c03\u67e5\u5e76\u8865\u5145\u56fe\u8c31\u8bc1\u636e"},
        )
        investigation = run_investigation(
            llm=self.llm,
            anomaly=anomaly,
            seed_evidence=evidence,
            initial_candidates=seed_candidates,
            progress_callback=progress_callback,
        )
        evidence = investigation.get("evidence", evidence)
        query_traces.extend(investigation.get("query_traces") or [])
        candidate_causes = investigation.get("candidate_causes", seed_candidates)
        recommended_actions = investigation.get(
            "recommended_actions",
            seed_scoring.get("recommended_actions", []),
        )

        reply = ""
        if render_reply:
            reply = render_report(
                self.llm,
                anomaly=anomaly.to_dict(),
                evidence=evidence,
                candidate_causes=candidate_causes,
                recommended_actions=recommended_actions,
                history_text="",
            )
            self._emit(
                progress_callback,
                "status",
                {"stage": "rca-render", "message": "\u5df2\u5b8c\u6210 RCA \u6839\u56e0\u62a5\u544a\u751f\u6210"},
            )

        trace = {
            "tool": "RCA_Engine",
            "engine": "rca",
            "type": "pipeline",
            "mode": "rca",
            "route": self._build_route_trace(intent),
            "route_summary": self._build_route_trace(intent)["summary"],
            "intent": intent.to_dict(),
            "anomaly": anomaly.to_dict(),
            "validation": validation,
            "policy": evidence.get("policy", {}),
            "graph_metrics": evidence.get("graph_metrics", {}),
            "candidate_causes": candidate_causes,
            "recommended_actions": recommended_actions,
            "investigation": investigation.get("summary", {}),
            "investigation_steps": investigation.get("steps", []),
            "queries": query_traces,
        }
        structured = self._build_structured_result(
            intent,
            anomaly,
            evidence,
            candidate_causes,
            recommended_actions,
            investigation=investigation,
        )
        trace["anomaly"] = structured.get("anomaly", trace["anomaly"])
        trace["incident_summary"] = structured.get("incident_summary", {})
        trace["risk_signals"] = structured.get("risk_signals", [])
        trace["investigation"] = structured.get("investigation", {})
        trace["investigation_steps"] = structured.get("investigation_steps", [])
        trace["evidence_graph_meta"] = (structured.get("evidence_graph") or {}).get("meta", {})
        return {
            "handled": True,
            "reply": reply,
            "trace": trace,
            "rca": structured,
            "candidate_causes": candidate_causes,
            "recommended_actions": recommended_actions,
        }
