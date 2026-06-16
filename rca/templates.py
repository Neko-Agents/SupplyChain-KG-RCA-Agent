from typing import Any, Dict, List


CAUSE_TEMPLATES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "order_delay": {
        "single_source_dependency": {
            "label": "单一供应依赖",
            "explanation_hint": "关键组件依赖单一供应商，上游波动会直接传导到订单履约。",
            "actions": ["评估替代供应商", "提高关键组件安全库存", "对受影响订单单独排程"],
        },
        "supplier_quality_issue": {
            "label": "供应商质量问题",
            "explanation_hint": "供应商质量波动可能导致返工、补货和交付时间拉长。",
            "actions": ["核查问题供应商质量表现", "提升来料复检频次", "对问题组件设置专项监控"],
        },
        "carrier_delay": {
            "label": "物流承运延误",
            "explanation_hint": "承运链路出现延迟放大，运输环节可能是订单异常的直接原因。",
            "actions": ["检查承运商时效表现", "评估替换运输方式", "调整受影响订单发运优先级"],
        },
        "component_concentration": {
            "label": "关键组件集中风险",
            "explanation_hint": "多个产品共享同一关键组件，组件问题会同时放大到多个订单。",
            "actions": ["识别共享组件影响范围", "优先保障共享关键组件供应", "拆分受影响生产排程"],
        },
    },
    "supplier_risk": {
        "quality_instability": {
            "label": "质量不稳定",
            "explanation_hint": "平均缺陷率或高缺陷组件占比较高，说明该供应商质量波动明显。",
            "actions": ["执行专项质量审查", "重新评估供应商分级", "提升关键组件检验频次"],
        },
        "dependency_concentration": {
            "label": "依赖集中",
            "explanation_hint": "供应商承担了较多单一来源组件或高暴露组件，存在明显单点风险。",
            "actions": ["推进双供应商策略", "优先完成高暴露组件替代验证", "建立关键组件库存缓冲"],
        },
        "delay_propagation": {
            "label": "延迟传导",
            "explanation_hint": "问题已经沿供应链向订单端扩散，体现为明显的延迟订单暴露。",
            "actions": ["盘点受影响订单", "设置专项履约跟踪", "对高风险订单重新排程"],
        },
        "replacement_gap": {
            "label": "替代缺口",
            "explanation_hint": "关键组件缺乏可替代供应商，降低了供应链韧性。",
            "actions": ["建立候选供应商池", "优先补齐替代覆盖缺口", "为替代难度高的组件设应急预案"],
        },
        "cost_volatility": {
            "label": "制造成本波动",
            "explanation_hint": "制造成本偏高或波动明显时，往往意味着供给稳定性和履约能力存在压力。",
            "actions": ["复核关键组件成本结构", "比较同类供应商成本水平", "识别高成本组件的替代可能"],
        },
    },
    "carrier_delay": {
        "route_congestion": {
            "label": "线路拥堵或区域时效问题",
            "explanation_hint": "延迟集中在特定区域或线路，说明路径效率存在瓶颈。",
            "actions": ["检查高延误区域和线路", "切换高风险区域承运资源", "重新评估区域运输 SLA"],
        },
        "mode_instability": {
            "label": "运输方式不稳定",
            "explanation_hint": "某类运输方式上的延迟明显更高，说明模式选择可能不合理。",
            "actions": ["复核运输方式匹配度", "替换高波动运输模式", "对异常模式设置时效预警"],
        },
        "exposure_concentration": {
            "label": "承运暴露集中",
            "explanation_hint": "高价值订单集中依赖同一承运商，会放大运输波动对业务的影响。",
            "actions": ["分散高价值订单承运分配", "建立承运商风险上限", "优先切换高暴露订单线路"],
        },
    },
    "product_impact": {
        "single_source_dependency": {
            "label": "产品单一供应依赖",
            "explanation_hint": "产品依赖的关键组件存在单一来源，是受影响的重要上游原因。",
            "actions": ["梳理关键组件单一来源情况", "优先推进替代供应商", "提高关键部件安全库存"],
        },
        "upstream_supplier_risk": {
            "label": "上游供应商风险",
            "explanation_hint": "产品关联的上游供应商存在明显质量或履约风险。",
            "actions": ["按产品维度回溯高风险供应商", "优先治理高缺陷供应商", "对高风险供应商影响的产品单独监控"],
        },
        "component_concentration": {
            "label": "关键组件共享集中",
            "explanation_hint": "产品依赖的组件被多个产品共享，导致局部组件问题被放大。",
            "actions": ["识别共享组件的跨产品影响", "优先保障共享组件供应", "为共享组件建立缓冲库存"],
        },
        "logistics_delay_exposure": {
            "label": "物流延误暴露",
            "explanation_hint": "产品关联订单中的物流延迟较多，运输环节可能是主要影响因子。",
            "actions": ["复核主要承运商表现", "切换高延误运输路径", "对高时效订单增加物流缓冲"],
        },
        "margin_exposure": {
            "label": "高利润暴露",
            "explanation_hint": "利润暴露高意味着该产品值得优先处置，但它更像优先级放大因素而非直接根因。",
            "actions": ["优先保障高利润产品资源", "纳入重点供应监控名单", "将关键订单设为优先履约对象"],
        },
    },
}


def get_cause_templates(anomaly_type: str) -> Dict[str, Dict[str, Any]]:
    return CAUSE_TEMPLATES.get(anomaly_type, {})


def merge_actions(candidate_causes: List[Dict[str, Any]], limit: int = 6) -> List[str]:
    actions: List[str] = []
    for cause in candidate_causes:
        for action in cause.get("suggested_actions", []):
            if action not in actions:
                actions.append(action)
            if len(actions) >= limit:
                return actions
    return actions
