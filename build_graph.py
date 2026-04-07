import os
import pandas as pd
import numpy as np
from neo4j import GraphDatabase

# ================= Configuration =================
URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "88888888")
CSV_FILE = os.getenv("CSV_FILE", "Supply_Chain_Data_Fake.csv")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "1000"))
# ================================================


class SupplyChainKGBuilder:
    def __init__(self, uri: str, user: str, password: str):
        print("Connecting to Neo4j...")
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def build_graph(self, csv_path: str) -> None:
        print(f"Loading dataset: {csv_path}")
        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(csv_path, encoding="gbk")

        df = df.replace({np.nan: None})
        records = df.to_dict("records")
        total_rows = len(records)

        print(f"Found {total_rows} rows. Importing in batches...")

        for i in range(0, total_rows, BATCH_SIZE):
            batch = records[i : i + BATCH_SIZE]
            with self.driver.session() as session:
                session.execute_write(self._import_batch, batch)
            print(f"Imported {min(i + BATCH_SIZE, total_rows)} / {total_rows}")

        print("Graph build completed.")

    @staticmethod
    def _import_batch(tx, batch):
        query = """
        UNWIND $batch AS row

        // 1. Nodes
        MERGE (c:Customer {id: row.`客户ID`})
        SET c.name = row.`客户姓名`,
            c.email = row.`客户邮箱`,
            c.segment = row.`客户群体`,
            c.country = row.`客户国家`,
            c.province = row.`客户省份`,
            c.city = row.`客户城市`,
            c.street = row.`客户街道`,
            c.lat = toFloat(row.`客户纬度`),
            c.lon = toFloat(row.`客户经度`)

        MERGE (o:Order {id: row.`订单ID`})
        SET o.order_date = row.`订单日期`,
            o.shipping_date = row.`发货日期`,
            o.scheduled_date = row.`预计送达日期`,
            o.actual_date = row.`实际送达日期`,
            o.payment_type = row.`支付类型`,
            o.status = row.`订单状态`

        MERGE (p:Product {id: row.`产品ID`})
        SET p.sku = row.`产品SKU`,
            p.name = row.`产品名称`,
            p.desc = row.`产品描述`,
            p.base_price = toFloat(row.`产品基础价格`)

        MERGE (cat:Category {name: row.`类别名称`})
        MERGE (dept:Department {name: row.`部门名称`})

        MERGE (s:Supplier {name: row.`供应商名称`})
        SET s.city = row.`供应商城市`
        MERGE (comp:Component {name: row.`核心组件名称`})

        MERGE (car:Carrier {name: row.`承运商名称`})

        // 2. Relationships
        MERGE (c)-[:PLACED_ORDER]->(o)

        MERGE (o)-[con:CONTAINS_PRODUCT]->(p)
        SET con.quantity = toInteger(row.`购买数量`),
            con.gross_total = toFloat(row.`销售总额_应付`),
            con.discount_rate = toFloat(row.`折扣率`),
            con.discount_amount = toFloat(row.`折扣金额`),
            con.net_total = toFloat(row.`实付总金额`),
            con.profit = toFloat(row.`订单单笔利润`),
            con.profit_ratio = toFloat(row.`利润率`)

        MERGE (p)-[:BELONGS_TO_CATEGORY]->(cat)
        MERGE (cat)-[:BELONGS_TO_DEPARTMENT]->(dept)

        MERGE (s)-[sup:SUPPLIES_COMPONENT]->(comp)
        SET sup.mfg_cost = toFloat(row.`单件制造成本`),
            sup.defect_rate = toFloat(row.`次品率`)

        MERGE (comp)-[:USED_IN]->(p)

        MERGE (o)-[ship:SHIPPED_BY]->(car)
        SET ship.trans_mode = row.`运输方式`,
            ship.ship_mode = row.`发货模式`,
            ship.days_scheduled = toInteger(row.`计划物流天数`),
            ship.days_real = toInteger(row.`实际物流天数`),
            ship.late_risk = toInteger(row.`发货延误风险_标签`),
            ship.delivery_status = row.`物流运送状态`
        """
        tx.run(query, batch=batch)


if __name__ == "__main__":
    builder = SupplyChainKGBuilder(URI, USER, PASSWORD)
    builder.build_graph(CSV_FILE)
    builder.close()
