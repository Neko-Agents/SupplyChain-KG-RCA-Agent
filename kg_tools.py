import os
import json
import contextvars
import re
from typing import Any, Dict, List, Optional
from langchain_core.tools import tool
from langchain_neo4j import Neo4jGraph

NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "88888888")

graph = Neo4jGraph(
    url=NEO4J_URI,
    username=NEO4J_USER,
    password=NEO4J_PASSWORD,
)

_last_trace: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "last_trace", default=None
)


def set_last_trace(trace: Dict[str, Any]) -> None:
    _last_trace.set(trace)


def get_last_trace() -> Optional[Dict[str, Any]]:
    return _last_trace.get()


def _json(result: List[Dict[str, Any]]) -> str:
    return json.dumps(result, ensure_ascii=False)


@tool
def calculate_ripple_effect(supplier_name: str = "") -> str:
    """
    Supplier disruption ripple-effect analysis.
    前端可用查询：输入供应商名称，查看断供影响（影响零部件/产品/订单数量与金额风险）。
    业务可用解读：用于评估单一供应商中断对营收与订单履约的连锁冲击。
    """
    supplier_name = supplier_name.strip()
    if not supplier_name or supplier_name.lower() in ["unknown", "none", "null"]:
        return (
            "Missing supplier name. Ask the user which supplier to analyze "
            "(example: 评估长江存储的断供风险)."
        )

    cypher = """
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(c:Component)-[:USED_IN]->(p:Product)
    MATCH (p)<-[cp:CONTAINS_PRODUCT]-(o:Order)
    WHERE s.name CONTAINS $supplier_name
    RETURN
        s.name AS supplier,
        count(DISTINCT c) AS affected_components,
        count(DISTINCT p) AS affected_products,
        count(DISTINCT o) AS affected_orders,
        sum(cp.gross_total) AS total_financial_risk
    """
    try:
        set_last_trace(
            {"tool": "calculate_ripple_effect", "type": "cypher", "cypher": cypher}
        )
        result = graph.query(cypher, params={"supplier_name": supplier_name})
        if not result:
            return f"No ripple data found for supplier: {supplier_name}."
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"Ripple-effect query failed: {str(e)}"


@tool
def trace_order_root_cause(order_id: str = "") -> str:
    """
    Trace upstream suppliers/components for a specific order.
    前端可用查询：输入订单ID，追溯上游供应商与关键零部件。
    业务可用解读：用于定位订单异常的上游责任与关键零部件来源。
    """
    order_id = order_id.strip()
    if not order_id or order_id.lower() in ["unknown", "none", "null"]:
        return (
            "Missing order id. Ask the user for a concrete order id "
            "(example: ORD-2024-100000)."
        )

    cypher = """
    MATCH (o:Order {id: $order_id})-[con:CONTAINS_PRODUCT]->(p:Product)
    MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp:Component)-[:USED_IN]->(p)
    RETURN o.id AS order, p.name AS product, comp.name AS component,
           s.name AS root_cause_supplier, s.city AS city
    """
    try:
        set_last_trace({"tool": "trace_order_root_cause", "type": "cypher", "cypher": cypher})
        result = graph.query(cypher, params={"order_id": order_id})
        if not result:
            return f"No upstream chain found for order: {order_id}."
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"Root-cause query failed: {str(e)}"


@tool
def analyze_dynamic_bottleneck(scenario: str = "general") -> str:
    """
    Multi-view bottleneck analysis using betweenness centrality.
    scenario: production | finance | general
    前端可用查询：选择场景（生产/财务/综合）识别关键瓶颈供应商（中心性）。
    业务可用解读：用于找出供应链关键节点，识别最易引发阻塞的供应商。
    """
    scenario = (scenario or "general").strip().lower()
    projection_strategies = {
        "production": {
            "labels": ["Supplier", "Component", "Product"],
            "rels": ["SUPPLIES_COMPONENT", "USED_IN"],
        },
        "finance": {
            "labels": ["Supplier", "Component", "Product", "Order"],
            "rels": ["SUPPLIES_COMPONENT", "USED_IN", "CONTAINS_PRODUCT"],
        },
        "general": {
            "labels": [
                "Customer",
                "Order",
                "Product",
                "Category",
                "Department",
                "Supplier",
                "Component",
                "Carrier",
            ],
            "rels": [
                "PLACED_ORDER",
                "CONTAINS_PRODUCT",
                "BELONGS_TO_CATEGORY",
                "BELONGS_TO_DEPARTMENT",
                "SUPPLIES_COMPONENT",
                "USED_IN",
                "SHIPPED_BY",
            ],
        },
    }

    if scenario not in projection_strategies:
        scenario = "general"

    config = projection_strategies[scenario]
    graph_name = "sc_bottleneck_temp"
    cypher = """
    CALL gds.graph.drop($graph_name, false) YIELD graphName
    WITH $graph_name AS graph_name, $labels AS labels, $rels AS rels
    CALL gds.graph.project(graph_name, labels, rels) YIELD graphName
    CALL gds.betweenness.stream($graph_name)
    YIELD nodeId, score
    MATCH (n:Supplier) WHERE id(n) = nodeId
    WITH n, score
    ORDER BY score DESC
    LIMIT 3
    WITH collect({core_supplier: n.name, bottleneck_score: round(score * 100) / 100}) AS rows, $graph_name AS graph_name
    CALL gds.graph.drop(graph_name, false) YIELD graphName
    UNWIND rows AS row
    RETURN row.core_supplier AS core_supplier, row.bottleneck_score AS bottleneck_score
    """
    try:
        set_last_trace(
            {
                "tool": "analyze_dynamic_bottleneck",
                "type": "algorithm",
                "name": "gds.betweenness",
                "cypher": cypher,
                "params": {"labels": config["labels"], "rels": config["rels"]},
            }
        )
        result = graph.query(
            cypher,
            params={
                "graph_name": graph_name,
                "labels": config["labels"],
                "rels": config["rels"],
            },
        )
        if not result:
            return f"No bottleneck data under scenario: {scenario}."
        context = (
            f"[Context] Scenario: {scenario}. Higher scores indicate "
            "more critical bottleneck suppliers.\n"
        )
        return context + json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return f"Bottleneck analysis failed: {str(e)}"


@tool
def supplier_ripple_effect(supplier_name: str) -> str:
    """
    Estimate downstream impact when a supplier is disrupted.
    前端可用查询：输入供应商名称，查看断供下游影响与收入/利润风险。
    业务可用解读：用于量化供应商中断带来的订单与利润暴露。
    """
    supplier_name = (supplier_name or "").strip()
    if not supplier_name:
        return "Missing supplier name. Ask the user to specify the supplier."

    cypher = """
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(c:Component)-[:USED_IN]->(p:Product)
    MATCH (p)<-[cp:CONTAINS_PRODUCT]-(o:Order)
    WHERE s.name CONTAINS $supplier_name
    RETURN
        s.name AS supplier,
        count(DISTINCT c) AS affected_components,
        count(DISTINCT p) AS affected_products,
        count(DISTINCT o) AS affected_orders,
        sum(cp.gross_total) AS total_gross_risk,
        sum(cp.net_total) AS total_net_risk,
        sum(cp.profit) AS total_profit_risk
    """
    set_last_trace({"tool": "supplier_ripple_effect", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"supplier_name": supplier_name})
    if not result:
        return f"No ripple-effect data found for supplier: {supplier_name}."
    return _json(result)


@tool
def supplier_affected_orders(supplier_name: str, top_k: int = 20) -> str:
    """
    List affected orders for a supplier disruption.
    前端可用查询：输入供应商名称，列出受影响订单（含客户、产品、金额）。
    业务可用解读：用于输出需优先干预的受影响订单清单。
    """
    supplier_name = (supplier_name or "").strip()
    if not supplier_name:
        return "Missing supplier name. Ask the user to specify the supplier."
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 20
    top_k = max(1, min(top_k, 200))

    cypher = """
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    MATCH (c:Customer)-[:PLACED_ORDER]->(o)
    WHERE s.name CONTAINS $supplier_name
    RETURN
        o.id AS order_id,
        o.status AS order_status,
        o.order_date AS order_date,
        o.actual_date AS actual_date,
        c.name AS customer,
        c.segment AS segment,
        p.name AS product,
        con.net_total AS net_total,
        con.gross_total AS gross_total,
        con.profit AS profit
    ORDER BY con.net_total DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "supplier_affected_orders", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"supplier_name": supplier_name, "top_k": top_k})
    if not result:
        return f"No affected orders found for supplier: {supplier_name}."
    return _json(result)


@tool
def supplier_affected_products(supplier_name: str, top_k: int = 20) -> str:
    """
    List affected products for a supplier disruption.
    前端可用查询：输入供应商名称，列出受影响产品及相关订单/利润。
    业务可用解读：用于识别最受影响的产品线以安排替代供应或排产。
    """
    supplier_name = (supplier_name or "").strip()
    if not supplier_name:
        return "Missing supplier name. Ask the user to specify the supplier."
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 20
    top_k = max(1, min(top_k, 200))

    cypher = """
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    WHERE s.name CONTAINS $supplier_name
    RETURN
        p.name AS product,
        count(DISTINCT o) AS orders,
        sum(con.net_total) AS net_revenue,
        sum(con.profit) AS profit
    ORDER BY net_revenue DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "supplier_affected_products", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"supplier_name": supplier_name, "top_k": top_k})
    if not result:
        return f"No affected products found for supplier: {supplier_name}."
    return _json(result)


@tool
def compare_supplier_risk(supplier_names: str) -> str:
    """
    Compare downstream impact between multiple suppliers.
    Input example: "长江存储 (YMTC), 立讯精密 (Luxshare)"
    """
    raw = (supplier_names or "").strip()
    if not raw:
        return "Missing supplier names. Provide two supplier names separated by comma."

    names = [
        part.strip()
        for part in re.split(r"[，,、/|]", raw)
        if part and part.strip()
    ]
    deduped: List[str] = []
    for name in names:
        if name not in deduped:
            deduped.append(name)
    names = deduped[:5]
    if len(names) < 2:
        return "Need at least two supplier names to compare."

    cypher = """
    UNWIND $supplier_names AS supplier_name
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(c:Component)-[:USED_IN]->(p:Product)
    MATCH (p)<-[cp:CONTAINS_PRODUCT]-(o:Order)
    WHERE s.name CONTAINS supplier_name
    RETURN
        supplier_name AS requested_supplier,
        collect(DISTINCT s.name)[0] AS matched_supplier,
        count(DISTINCT c) AS affected_components,
        count(DISTINCT p) AS affected_products,
        count(DISTINCT o) AS affected_orders,
        sum(cp.gross_total) AS total_gross_risk,
        sum(cp.net_total) AS total_net_risk,
        sum(cp.profit) AS total_profit_risk
    ORDER BY total_gross_risk DESC, affected_orders DESC
    """
    set_last_trace({"tool": "compare_supplier_risk", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"supplier_names": names})
    if not result:
        return f"No supplier comparison data found for: {', '.join(names)}."
    return _json(result)


@tool
def order_root_cause(order_id: str) -> str:
    """
    Trace root-cause suppliers/components for a given order.
    前端可用查询：输入订单ID，查看根因供应商、零件与物流信息。
    业务可用解读：用于订单异常溯源与责任划分。
    """
    order_id = (order_id or "").strip()
    if not order_id:
        return "Missing order id. Ask the user to specify the order id."

    cypher = """
    MATCH (o:Order {id: $order_id})-[con:CONTAINS_PRODUCT]->(p:Product)
    MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp:Component)-[:USED_IN]->(p)
    OPTIONAL MATCH (o)-[ship:SHIPPED_BY]->(car:Carrier)
    RETURN
        o.id AS order_id,
        p.name AS product,
        comp.name AS component,
        s.name AS supplier,
        s.city AS supplier_city,
        sup.defect_rate AS defect_rate,
        ship.late_risk AS late_risk,
        car.name AS carrier
    """
    set_last_trace({"tool": "order_root_cause", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"order_id": order_id})
    if not result:
        return f"No root-cause path found for order: {order_id}."
    return _json(result)


@tool
def analyze_key_bottlenecks(scenario: str = "general") -> str:
    """
    Find key bottleneck suppliers by betweenness centrality.
    scenario: general | production | finance
    前端可用查询：选择场景（综合/生产/财务）识别关键瓶颈供应商（中心性）。
    业务可用解读：用于识别必须优先保障与备份的关键供应商。
    """
    scenario = (scenario or "general").strip().lower()
    projection_strategies = {
        "production": {
            "labels": ["Supplier", "Component", "Product"],
            "rels": ["SUPPLIES_COMPONENT", "USED_IN"],
        },
        "finance": {
            "labels": ["Supplier", "Component", "Product", "Order"],
            "rels": ["SUPPLIES_COMPONENT", "USED_IN", "CONTAINS_PRODUCT"],
        },
        "general": {
            "labels": [
                "Customer",
                "Order",
                "Product",
                "Category",
                "Department",
                "Supplier",
                "Component",
                "Carrier",
            ],
            "rels": [
                "PLACED_ORDER",
                "CONTAINS_PRODUCT",
                "BELONGS_TO_CATEGORY",
                "BELONGS_TO_DEPARTMENT",
                "SUPPLIES_COMPONENT",
                "USED_IN",
                "SHIPPED_BY",
            ],
        },
    }
    if scenario not in projection_strategies:
        scenario = "general"
    config = projection_strategies[scenario]
    graph_name = "sc_bottleneck_temp"

    cypher = """
    CALL gds.graph.drop($graph_name, false) YIELD graphName
    WITH $graph_name AS graph_name, $labels AS labels, $rels AS rels
    CALL gds.graph.project(graph_name, labels, rels) YIELD graphName
    CALL gds.betweenness.stream($graph_name)
    YIELD nodeId, score
    MATCH (n:Supplier) WHERE id(n) = nodeId
    WITH n, score
    ORDER BY score DESC
    LIMIT 5
    WITH collect({core_supplier: n.name, bottleneck_score: round(score * 100) / 100}) AS rows, $graph_name AS graph_name
    CALL gds.graph.drop(graph_name, false) YIELD graphName
    UNWIND rows AS row
    RETURN row.core_supplier AS core_supplier, row.bottleneck_score AS bottleneck_score
    """
    set_last_trace(
        {
            "tool": "analyze_key_bottlenecks",
            "type": "algorithm",
            "name": "gds.betweenness",
            "cypher": cypher,
            "params": {"labels": config["labels"], "rels": config["rels"]},
        }
    )
    result = graph.query(
        cypher,
        params={
            "graph_name": graph_name,
            "labels": config["labels"],
            "rels": config["rels"],
        },
    )
    if not result:
        return f"No bottleneck results under scenario: {scenario}."
    return _json(result)


@tool
def estimate_delay_loss(min_delay_days: int = 1, carrier_name: str = "") -> str:
    """
    Estimate financial exposure from delayed shipments.
    前端可用查询：设置延误天数（可选承运商）估算延误订单的财务风险。
    业务可用解读：用于评估物流延误对收入与利润的影响规模。
    """
    try:
        min_delay_days = int(min_delay_days)
    except Exception:
        min_delay_days = 1
    carrier_name = (carrier_name or "").strip()

    cypher = """
    MATCH (o:Order)-[ship:SHIPPED_BY]->(car:Carrier)
    WHERE (ship.days_real - ship.days_scheduled) >= $min_delay_days
      AND ($carrier_name = "" OR car.name CONTAINS $carrier_name)
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        car.name AS carrier,
        count(DISTINCT o) AS delayed_orders,
        avg(ship.days_real - ship.days_scheduled) AS avg_delay_days,
        sum(con.net_total) AS net_at_risk,
        sum(con.gross_total) AS gross_at_risk,
        sum(con.profit) AS profit_at_risk
    ORDER BY net_at_risk DESC
    LIMIT 10
    """
    set_last_trace({"tool": "estimate_delay_loss", "type": "cypher", "cypher": cypher})
    result = graph.query(
        cypher,
        params={"min_delay_days": min_delay_days, "carrier_name": carrier_name},
    )
    if not result:
        return "No delayed-shipment loss data found."
    return _json(result)


@tool
def estimate_supplier_disruption_loss(supplier_name: str) -> str:
    """
    Estimate potential economic loss if a supplier is disrupted.
    前端可用查询：输入供应商名称，估算断供的潜在经济损失（毛利/净额/利润）。
    业务可用解读：用于供应商风险评分与应急预案优先级排序。
    """
    supplier_name = (supplier_name or "").strip()
    if not supplier_name:
        return "Missing supplier name. Ask the user to specify the supplier."

    cypher = """
    MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)
    MATCH (p)<-[con:CONTAINS_PRODUCT]-(o:Order)
    WHERE s.name CONTAINS $supplier_name
    RETURN
        s.name AS supplier,
        count(DISTINCT o) AS affected_orders,
        sum(con.gross_total) AS gross_exposure,
        sum(con.net_total) AS net_exposure,
        sum(con.profit) AS profit_exposure
    """
    set_last_trace({"tool": "estimate_supplier_disruption_loss", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"supplier_name": supplier_name})
    if not result:
        return f"No loss estimate found for supplier: {supplier_name}."
    return _json(result)


@tool
def top_products_by_profit(top_k: int = 5) -> str:
    """
    Rank products by total profit contribution.
    前端可用查询：查看利润贡献排名的产品TopN。
    业务可用解读：用于识别利润贡献核心产品并优先保障。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    top_k = max(1, min(top_k, 20))

    cypher = """
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p:Product)
    RETURN
        p.name AS product,
        sum(con.profit) AS total_profit,
        sum(con.net_total) AS total_net,
        count(DISTINCT o) AS orders
    ORDER BY total_profit DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "top_products_by_profit", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No product profit data found."
    return _json(result)


@tool
def top_customers_by_revenue(top_k: int = 5) -> str:
    """
    Rank customers by total spending.
    前端可用查询：查看客户消费额排名TopN。
    业务可用解读：用于识别重点客户与资源倾斜对象。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    top_k = max(1, min(top_k, 20))

    cypher = """
    MATCH (c:Customer)-[:PLACED_ORDER]->(o:Order)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        c.name AS customer,
        sum(con.net_total) AS total_spend,
        count(DISTINCT o) AS orders
    ORDER BY total_spend DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "top_customers_by_revenue", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No customer spending data found."
    return _json(result)


@tool
def suppliers_with_high_defect_rate(min_defect_rate: float = 0.05, top_k: int = 5) -> str:
    """
    Identify suppliers with high defect rates.
    前端可用查询：设置缺陷率阈值，查看高缺陷率供应商TopN。
    业务可用解读：用于质量风险监控与供应商改进推进。
    """
    try:
        min_defect_rate = float(min_defect_rate)
    except Exception:
        min_defect_rate = 0.05
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    top_k = max(1, min(top_k, 20))

    cypher = """
    MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(c:Component)
    WHERE sup.defect_rate >= $min_defect_rate
    RETURN
        s.name AS supplier,
        avg(sup.defect_rate) AS avg_defect_rate,
        count(DISTINCT c) AS components
    ORDER BY avg_defect_rate DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "suppliers_with_high_defect_rate", "type": "cypher", "cypher": cypher})
    result = graph.query(
        cypher, params={"min_defect_rate": min_defect_rate, "top_k": top_k}
    )
    if not result:
        return "No high-defect suppliers found."
    return _json(result)


@tool
def carrier_delay_performance(min_delay_days: int = 1, top_k: int = 5) -> str:
    """
    Rank carriers by delayed orders and exposure.
    前端可用查询：查看承运商延误表现TopN（延误订单数、风险金额）。
    业务可用解读：用于承运商绩效评估与替换决策依据。
    """
    try:
        min_delay_days = int(min_delay_days)
    except Exception:
        min_delay_days = 1
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    top_k = max(1, min(top_k, 20))

    cypher = """
    MATCH (o:Order)-[ship:SHIPPED_BY]->(car:Carrier)
    WHERE (ship.days_real - ship.days_scheduled) >= $min_delay_days
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        car.name AS carrier,
        count(DISTINCT o) AS delayed_orders,
        avg(ship.days_real - ship.days_scheduled) AS avg_delay_days,
        sum(con.net_total) AS net_at_risk,
        sum(con.gross_total) AS gross_at_risk,
        sum(con.profit) AS profit_at_risk
    ORDER BY net_at_risk DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "carrier_delay_performance", "type": "cypher", "cypher": cypher})
    result = graph.query(
        cypher, params={"min_delay_days": min_delay_days, "top_k": top_k}
    )
    if not result:
        return "No carrier delay performance data found."
    return _json(result)


@tool
def segment_financial_exposure(top_k: int = 5) -> str:
    """
    Exposure by customer segment.
    前端可用查询：查看客户细分维度的订单与利润暴露TopN。
    业务可用解读：用于识别高价值客户细分并优化服务策略。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    top_k = max(1, min(top_k, 20))

    cypher = """
    MATCH (c:Customer)-[:PLACED_ORDER]->(o:Order)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        c.segment AS segment,
        count(DISTINCT o) AS orders,
        sum(con.net_total) AS net_revenue,
        sum(con.profit) AS profit
    ORDER BY net_revenue DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "segment_financial_exposure", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No customer segment exposure data found."
    return _json(result)


@tool
def category_profitability(top_k: int = 5) -> str:
    """
    Profitability by product category.
    前端可用查询：查看产品品类利润贡献TopN。
    业务可用解读：用于品类结构优化与资源投放决策。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    top_k = max(1, min(top_k, 20))

    cypher = """
    MATCH (p:Product)-[:BELONGS_TO_CATEGORY]->(cat:Category)
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
    RETURN
        cat.name AS category,
        sum(con.net_total) AS net_revenue,
        sum(con.profit) AS profit,
        avg(con.profit_ratio) AS avg_margin
    ORDER BY profit DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "category_profitability", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No category profitability data found."
    return _json(result)


@tool
def supplier_risk_profile(top_k: int = 5) -> str:
    """
    Supplier profile combining defects, cost, and exposure.
    前端可用查询：查看供应商风险画像TopN（缺陷率/成本/暴露）。
    业务可用解读：用于供应商分级管理与风险预警。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 5
    top_k = max(1, min(top_k, 20))

    cypher = """
    MATCH (s:Supplier)-[sup:SUPPLIES_COMPONENT]->(comp:Component)
    OPTIONAL MATCH (comp)-[:USED_IN]->(p:Product)<-[con:CONTAINS_PRODUCT]-(o:Order)
    RETURN
        s.name AS supplier,
        avg(sup.defect_rate) AS avg_defect_rate,
        avg(sup.mfg_cost) AS avg_mfg_cost,
        count(DISTINCT comp) AS components,
        count(DISTINCT o) AS orders,
        sum(con.net_total) AS net_exposure,
        sum(con.profit) AS profit_exposure
    ORDER BY net_exposure DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "supplier_risk_profile", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No supplier risk profile data found."
    return _json(result)


@tool
def order_status_summary(top_k: int = 10) -> str:
    """
    Summary by order status.
    前端可用查询：查看订单状态汇总的订单数与利润TopN。
    业务可用解读：用于监控订单履约结构与异常分布。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 10
    top_k = max(1, min(top_k, 50))

    cypher = """
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        o.status AS status,
        count(DISTINCT o) AS orders,
        sum(con.net_total) AS net_revenue,
        sum(con.profit) AS profit
    ORDER BY net_revenue DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "order_status_summary", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No order status summary data found."
    return _json(result)


@tool
def late_risk_products(top_k: int = 10) -> str:
    """
    Products most associated with late risk.
    前端可用查询：查看与延误风险关联度最高的产品TopN。
    业务可用解读：用于识别易延误产品并优化供应与排产。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 10
    top_k = max(1, min(top_k, 50))

    cypher = """
    MATCH (o:Order)-[ship:SHIPPED_BY]->(:Carrier)
    WHERE ship.late_risk = 1
    MATCH (o)-[con:CONTAINS_PRODUCT]->(p:Product)
    RETURN
        p.name AS product,
        count(DISTINCT o) AS late_orders,
        sum(con.net_total) AS net_at_risk,
        sum(con.profit) AS profit_at_risk
    ORDER BY late_orders DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "late_risk_products", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No late-risk product data found."
    return _json(result)


@tool
def late_risk_carrier_modes(top_k: int = 10) -> str:
    """
    Late-risk exposure by carrier and transport mode.
    前端可用查询：查看承运商与运输/船运模式的延误风险TopN。
    业务可用解读：用于优化运输方式与承运商组合。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 10
    top_k = max(1, min(top_k, 50))

    cypher = """
    MATCH (o:Order)-[ship:SHIPPED_BY]->(car:Carrier)
    WHERE ship.late_risk = 1
    OPTIONAL MATCH (o)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        car.name AS carrier,
        ship.trans_mode AS transport_mode,
        ship.ship_mode AS ship_mode,
        count(DISTINCT o) AS late_orders,
        avg(ship.days_real - ship.days_scheduled) AS avg_delay_days,
        sum(con.net_total) AS net_at_risk
    ORDER BY net_at_risk DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "late_risk_carrier_modes", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No late-risk carrier mode data found."
    return _json(result)


@tool
def region_revenue(top_k: int = 10) -> str:
    """
    Revenue by customer city.
    前端可用查询：查看客户城市/省份/国家维度收入TopN。
    业务可用解读：用于区域市场贡献分析与渠道投放。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 10
    top_k = max(1, min(top_k, 50))

    cypher = """
    MATCH (c:Customer)-[:PLACED_ORDER]->(o:Order)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        c.city AS city,
        c.province AS province,
        c.country AS country,
        count(DISTINCT o) AS orders,
        sum(con.net_total) AS net_revenue,
        sum(con.profit) AS profit
    ORDER BY net_revenue DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "region_revenue", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No regional revenue data found."
    return _json(result)


@tool
def payment_type_risk(top_k: int = 10) -> str:
    """
    Exposure by payment type.
    前端可用查询：查看支付方式的收入与利润暴露TopN。
    业务可用解读：用于评估支付结构风险与资金回款策略。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 10
    top_k = max(1, min(top_k, 50))

    cypher = """
    MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(:Product)
    RETURN
        o.payment_type AS payment_type,
        count(DISTINCT o) AS orders,
        sum(con.net_total) AS net_revenue,
        sum(con.profit) AS profit
    ORDER BY net_revenue DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "payment_type_risk", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No payment-type risk data found."
    return _json(result)


@tool
def delivery_performance_by_ship_mode(top_k: int = 10) -> str:
    """
    Delivery performance by ship mode.
    前端可用查询：查看按运输方式的交付表现TopN（延误天数/延误订单）。
    业务可用解读：用于优化运输方式选择与交付策略。
    """
    try:
        top_k = int(top_k)
    except Exception:
        top_k = 10
    top_k = max(1, min(top_k, 50))

    cypher = """
    MATCH (o:Order)-[ship:SHIPPED_BY]->(:Carrier)
    RETURN
        ship.ship_mode AS ship_mode,
        ship.trans_mode AS transport_mode,
        count(DISTINCT o) AS orders,
        avg(ship.days_real - ship.days_scheduled) AS avg_delay_days,
        sum(ship.late_risk) AS late_risk_orders
    ORDER BY late_risk_orders DESC
    LIMIT $top_k
    """
    set_last_trace({"tool": "delivery_performance_by_ship_mode", "type": "cypher", "cypher": cypher})
    result = graph.query(cypher, params={"top_k": top_k})
    if not result:
        return "No delivery performance data found."
    return _json(result)
