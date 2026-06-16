// 1. 查看某个订单的事件证据链
MATCH (o:Order {id: "ORD-2024-100001"})
OPTIONAL MATCH (o)-[:HAS_DELAY_EVENT]->(d:DelayEvent)
OPTIONAL MATCH (q:QualityInspection)-[:IMPACTS_ORDER]->(o)
OPTIONAL MATCH (n:SupplierNotice)-[:AFFECTS_ORDER]->(o)
RETURN o, d, q, n;

// 2. 查看某个订单的完整 RCA 路径
MATCH p =
    (s:Supplier)-[:ISSUED_NOTICE]->(:SupplierNotice)-[:AFFECTS_ORDER]->(o:Order {id: "ORD-2024-100001"})
OPTIONAL MATCH p2 =
    (s)-[:UNDERWENT_INSPECTION]->(:QualityInspection)-[:IMPACTS_ORDER]->(o)
OPTIONAL MATCH p3 =
    (o)-[:HAS_DELAY_EVENT]->(:DelayEvent)
RETURN p, p2, p3;

// 3. 找出同时出现供应商预警、质量异常、延迟事件的订单
MATCH (o:Order)-[:HAS_DELAY_EVENT]->(d:DelayEvent)
MATCH (q:QualityInspection)-[:IMPACTS_ORDER]->(o)
MATCH (n:SupplierNotice)-[:AFFECTS_ORDER]->(o)
RETURN
    o.id AS order_id,
    d.reason_code AS delay_reason,
    q.result AS inspection_result,
    n.notice_type AS notice_type
ORDER BY order_id
LIMIT 50;

// 4. 看某个供应商触发的事件传播范围
MATCH (s:Supplier {name: "长江存储 (YMTC)"})-[:ISSUED_NOTICE]->(n:SupplierNotice)
OPTIONAL MATCH (n)-[:AFFECTS_COMPONENT]->(c:Component)
OPTIONAL MATCH (n)-[:AFFECTS_PRODUCT]->(p:Product)
OPTIONAL MATCH (n)-[:AFFECTS_ORDER]->(o:Order)
RETURN s, n, c, p, o;
