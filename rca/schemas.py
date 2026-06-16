from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class RCAIntent:
    route_type: str = "general"
    subtype: str = ""
    target_type: str = ""
    target_id: str = ""
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route_type": self.route_type,
            "subtype": self.subtype,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
        }


@dataclass
class RCAAnomaly:
    anomaly_type: str
    target_type: str
    target_id: str
    symptom: str
    source: str = "qa_trigger"
    severity: str = "unknown"
    question: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.anomaly_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "symptom": self.symptom,
            "source": self.source,
            "severity": self.severity,
            "question": self.question,
        }
