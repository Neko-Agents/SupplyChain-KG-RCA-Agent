from typing import Any, Dict


EXPANSION_POLICIES: Dict[str, Dict[str, Any]] = {
    "order_delay": {
        "max_hops": 3,
        "max_nodes": 60,
        "relations": [
            "CONTAINS_PRODUCT",
            "SHIPPED_BY",
            "USED_IN",
            "SUPPLIES_COMPONENT",
            "PLACED_ORDER",
        ],
        "focus_node_types": ["Order", "Product", "Component", "Supplier", "Carrier", "Customer"],
    },
    "supplier_risk": {
        "max_hops": 3,
        "max_nodes": 60,
        "relations": [
            "SUPPLIES_COMPONENT",
            "USED_IN",
            "CONTAINS_PRODUCT",
            "SHIPPED_BY",
        ],
        "focus_node_types": ["Supplier", "Component", "Product", "Order", "Carrier"],
    },
    "carrier_delay": {
        "max_hops": 3,
        "max_nodes": 60,
        "relations": [
            "SHIPPED_BY",
            "CONTAINS_PRODUCT",
            "PLACED_ORDER",
            "USED_IN",
            "SUPPLIES_COMPONENT",
        ],
        "focus_node_types": ["Carrier", "Order", "Customer", "Product", "Supplier"],
    },
    "product_impact": {
        "max_hops": 3,
        "max_nodes": 60,
        "relations": [
            "CONTAINS_PRODUCT",
            "USED_IN",
            "SUPPLIES_COMPONENT",
            "SHIPPED_BY",
            "BELONGS_TO_CATEGORY",
            "BELONGS_TO_DEPARTMENT",
        ],
        "focus_node_types": ["Product", "Order", "Component", "Supplier", "Carrier"],
    },
}


def get_policy(anomaly_type: str) -> Dict[str, Any]:
    return EXPANSION_POLICIES.get(anomaly_type, {"max_hops": 2, "max_nodes": 40, "relations": []})
