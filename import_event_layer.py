import csv
import os
from pathlib import Path

from neo4j import GraphDatabase


URI = os.getenv("NEO4J_URI", "neo4j://127.0.0.1:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "88888888")
EVENT_DIR = Path(os.getenv("EVENT_DIR", "event_data"))


def load_csv(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def normalize_int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


class EventLayerImporter:
    def __init__(self, uri: str, user: str, password: str):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self) -> None:
        self.driver.close()

    def import_all(self, event_dir: Path) -> None:
        source_records = load_csv(event_dir / "source_records.csv")
        supplier_notices = load_csv(event_dir / "supplier_notices.csv")
        quality_inspections = load_csv(event_dir / "quality_inspections.csv")
        delay_events = load_csv(event_dir / "delay_events.csv")

        with self.driver.session() as session:
            session.execute_write(self._merge_source_records, source_records)
            session.execute_write(self._merge_supplier_notices, supplier_notices)
            session.execute_write(self._merge_quality_inspections, quality_inspections)
            session.execute_write(self._merge_delay_events, delay_events)

    @staticmethod
    def _merge_source_records(tx, rows):
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (src:SourceRecord {id: row.id})
            SET src.source_type = row.source_type,
                src.source_system = row.source_system,
                src.source_row_key = row.source_row_key,
                src.created_at = row.created_at,
                src.summary = row.summary
            """,
            rows=rows,
        )

    @staticmethod
    def _merge_supplier_notices(tx, rows):
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (n:SupplierNotice {id: row.id})
            SET n.notice_type = row.notice_type,
                n.severity = row.severity,
                n.reason_code = row.reason_code,
                n.created_at = row.created_at,
                n.effective_from = row.effective_from,
                n.summary = row.summary,
                n.confidence = toFloat(row.confidence),
                n.supplier_name = row.supplier_name,
                n.component_name = row.component_name,
                n.product_id = row.product_id,
                n.order_id = row.order_id,
                n.expected_impact = row.expected_impact,
                n.source_ref = row.source_ref
            WITH row, n
            OPTIONAL MATCH (s:Supplier {name: row.supplier_name})
            FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END |
                MERGE (s)-[:ISSUED_NOTICE]->(n)
            )
            WITH row, n
            OPTIONAL MATCH (c:Component {name: row.component_name})
            FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
                MERGE (n)-[:AFFECTS_COMPONENT]->(c)
            )
            WITH row, n
            OPTIONAL MATCH (p:Product {id: row.product_id})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                MERGE (n)-[:AFFECTS_PRODUCT]->(p)
            )
            WITH row, n
            OPTIONAL MATCH (o:Order {id: row.order_id})
            FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END |
                MERGE (n)-[:AFFECTS_ORDER]->(o)
            )
            WITH row, n
            OPTIONAL MATCH (src:SourceRecord {id: row.source_ref})
            FOREACH (_ IN CASE WHEN src IS NULL THEN [] ELSE [1] END |
                MERGE (n)-[:SUPPORTED_BY]->(src)
            )
            """,
            rows=rows,
        )

    @staticmethod
    def _merge_quality_inspections(tx, rows):
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (q:QualityInspection {id: row.id})
            SET q.batch_id = row.batch_id,
                q.inspection_time = row.inspection_time,
                q.result = row.result,
                q.severity = row.severity,
                q.sample_size = toInteger(row.sample_size),
                q.failed_units = toInteger(row.failed_units),
                q.observed_defect_rate = toFloat(row.observed_defect_rate),
                q.source_ref = row.source_ref,
                q.supplier_name = row.supplier_name,
                q.component_name = row.component_name,
                q.product_id = row.product_id,
                q.order_id = row.order_id,
                q.inspector = row.inspector,
                q.failure_mode = row.failure_mode
            WITH row, q
            OPTIONAL MATCH (s:Supplier {name: row.supplier_name})
            FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END |
                MERGE (s)-[:UNDERWENT_INSPECTION]->(q)
            )
            WITH row, q
            OPTIONAL MATCH (c:Component {name: row.component_name})
            FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END |
                MERGE (q)-[:INSPECTED_COMPONENT]->(c)
            )
            WITH row, q
            OPTIONAL MATCH (p:Product {id: row.product_id})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                MERGE (q)-[:INSPECTED_PRODUCT]->(p)
            )
            WITH row, q
            OPTIONAL MATCH (o:Order {id: row.order_id})
            FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END |
                MERGE (q)-[:IMPACTS_ORDER]->(o)
            )
            WITH row, q
            OPTIONAL MATCH (src:SourceRecord {id: row.source_ref})
            FOREACH (_ IN CASE WHEN src IS NULL THEN [] ELSE [1] END |
                MERGE (q)-[:SUPPORTED_BY]->(src)
            )
            """,
            rows=rows,
        )

    @staticmethod
    def _merge_delay_events(tx, rows):
        tx.run(
            """
            UNWIND $rows AS row
            MERGE (d:DelayEvent {id: row.id})
            SET d.delay_stage = row.delay_stage,
                d.occurred_at = row.occurred_at,
                d.severity = row.severity,
                d.reason_code = row.reason_code,
                d.delay_hours = toFloat(row.delay_hours),
                d.eta_before = row.eta_before,
                d.eta_after = row.eta_after,
                d.source_ref = row.source_ref,
                d.order_id = row.order_id,
                d.carrier_name = row.carrier_name,
                d.product_id = row.product_id,
                d.supplier_name = row.supplier_name,
                d.trans_mode = row.trans_mode,
                d.ship_mode = row.ship_mode,
                d.location_hint = row.location_hint
            WITH row, d
            OPTIONAL MATCH (o:Order {id: row.order_id})
            FOREACH (_ IN CASE WHEN o IS NULL THEN [] ELSE [1] END |
                MERGE (o)-[:HAS_DELAY_EVENT]->(d)
            )
            WITH row, d
            OPTIONAL MATCH (car:Carrier {name: row.carrier_name})
            FOREACH (_ IN CASE WHEN car IS NULL THEN [] ELSE [1] END |
                MERGE (car)-[:TRIGGERED_DELAY_EVENT]->(d)
            )
            WITH row, d
            OPTIONAL MATCH (p:Product {id: row.product_id})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                MERGE (d)-[:IMPACTS_PRODUCT]->(p)
            )
            WITH row, d
            OPTIONAL MATCH (s:Supplier {name: row.supplier_name})
            FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END |
                MERGE (d)-[:RELATED_TO_SUPPLIER]->(s)
            )
            WITH row, d
            OPTIONAL MATCH (src:SourceRecord {id: row.source_ref})
            FOREACH (_ IN CASE WHEN src IS NULL THEN [] ELSE [1] END |
                MERGE (d)-[:SUPPORTED_BY]->(src)
            )
            """,
            rows=rows,
        )


if __name__ == "__main__":
    importer = EventLayerImporter(URI, USER, PASSWORD)
    importer.import_all(EVENT_DIR)
    importer.close()
    print(f"Imported event layer from {EVENT_DIR.resolve()}")
