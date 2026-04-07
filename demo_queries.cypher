// 供应链知识图谱演示查询脚本
// 使用方式：在 Neo4j Browser 中逐段执行，必要时替换 ID

// 1) 验证客户节点
MATCH (c:Customer {id: "CUST-90002"})
RETURN c;

// 2) 验证订单节点
MATCH (o:Order {id: "ORD-2024-100002"})
RETURN o;

// 3) 验证产品节点
MATCH (p:Product {id: "SKU-ST-M01"})
RETURN p;

// 4) 验证供应商节点
MATCH (s:Supplier {name: "中芯国际 (SMIC)"})
RETURN s;

// 5) 客户 → 订单 关系验证
MATCH (c:Customer {id: "CUST-90002"})-[:PLACED_ORDER]->(o:Order {id: "ORD-2024-100002"})
RETURN c, o;

// 6) 订单 → 产品 关系及属性验证
MATCH (o:Order {id: "ORD-2024-100002"})-[r:CONTAINS_PRODUCT]->(p:Product {id: "SKU-ST-M01"})
RETURN o.id AS order_id, p.id AS product_id, r.quantity, r.net_total, r.profit, r.profit_ratio;

// 7) 供应商 → 组件（如存在）
MATCH (s:Supplier {name: "中芯国际 (SMIC)"})-[r:SUPPLIES_COMPONENT]->(comp:Component)
RETURN s.name AS supplier, comp.name AS component, r.mfg_cost, r.defect_rate;

// 8) 全链路展示：客户 → 订单 → 产品 → 品类 → 部门
MATCH (c:Customer {id: "CUST-90002"})-[:PLACED_ORDER]->(o:Order)-[:CONTAINS_PRODUCT]->(p:Product)
OPTIONAL MATCH (p)-[:BELONGS_TO_CATEGORY]->(cat:Category)
OPTIONAL MATCH (cat)-[:BELONGS_TO_DEPARTMENT]->(dept:Department)
RETURN c.name AS customer, o.id AS order_id, p.name AS product, cat.name AS category, dept.name AS department;

// 9) 物流信息（如存在）
MATCH (o:Order {id: "ORD-2024-100002"})-[r:SHIPPED_BY]->(car:Carrier)
RETURN o.id AS order_id, car.name AS carrier, r.trans_mode, r.ship_mode, r.days_real, r.late_risk;

// 10) 最近订单（按订单日期 Top 10）
MATCH (o:Order)
RETURN o.id AS order_id, o.order_date AS order_date, o.status AS status
ORDER BY o.order_date DESC
LIMIT 10;

// 11) 利润贡献 Top 10 产品
MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p:Product)
RETURN p.name AS product, sum(con.profit) AS total_profit
ORDER BY total_profit DESC
LIMIT 10;

// 12) 供应商收入暴露（按净额 Top 10）
MATCH (s:Supplier)-[:SUPPLIES_COMPONENT]->(:Component)-[:USED_IN]->(p:Product)
MATCH (o:Order)-[con:CONTAINS_PRODUCT]->(p)
RETURN s.name AS supplier, sum(con.net_total) AS net_exposure
ORDER BY net_exposure DESC
LIMIT 10;
