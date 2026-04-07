"""
Incremental update script for Supply Chain KG (Neo4j).

Usage:
  python incremental_update.py --csv Supply_Chain_Data_Fake_Increment.csv \
      --neo4j-uri neo4j://127.0.0.1:7687 --neo4j-user neo4j --neo4j-password 88888888

Notes:
- Reads CSV with pandas.
- Uses Neo4j official driver.
- Uses UNWIND with batch submission (default 2000).
- Uses MERGE + ON CREATE/ON MATCH for upsert.
"""

import argparse
import sys
from datetime import datetime, timezone
from typing import Dict, List

import pandas as pd
from neo4j import GraphDatabase


REQUIRED_COLUMNS = [
    "customer_id",
    "customer_name",
    "order_id",
    "order_status",
    "supplier_name",
    "supplier_city",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental CSV upsert to Neo4j")
    parser.add_argument("--csv", required=True, help="Path to increment CSV file")
    parser.add_argument("--neo4j-uri", default="neo4j://127.0.0.1:7687")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="88888888")
    parser.add_argument("--batch-size", type=int, default=2000)
    return parser.parse_args()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_columns(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}")


def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    # Trim string columns and drop rows with required fields missing.
    for col in REQUIRED_COLUMNS:
        df[col] = df[col].astype(str).str.strip()
    df = df.dropna(subset=REQUIRED_COLUMNS)
    df = df[df["customer_id"] != ""]
    df = df[df["order_id"] != ""]
    df = df[df["supplier_name"] != ""]
    return df


def _chunked(data: List[Dict], size: int) -> List[List[Dict]]:
    return [data[i : i + size] for i in range(0, len(data), size)]


def _upsert_batch(tx, rows: List[Dict]) -> None:
    cypher = """
    UNWIND $rows AS row

    MERGE (c:Customer {customer_id: row.customer_id})
    ON CREATE SET
        c.name = row.customer_name,
        c.last_updated_time = row.last_updated_time
    ON MATCH SET
        c.name = row.customer_name,
        c.last_updated_time = row.last_updated_time

    MERGE (o:Order {order_id: row.order_id})
    ON CREATE SET
        o.status = row.order_status,
        o.last_updated_time = row.last_updated_time
    ON MATCH SET
        o.status = row.order_status,
        o.last_updated_time = row.last_updated_time

    MERGE (s:Supplier {supplier_name: row.supplier_name})
    ON CREATE SET
        s.city = row.supplier_city
    ON MATCH SET
        s.city = row.supplier_city

    MERGE (c)-[:PLACED]->(o)
    MERGE (s)-[:SUPPLIES]->(o)
    """
    tx.run(cypher, rows=rows)


def main() -> int:
    args = parse_args()

    try:
        df = pd.read_csv(args.csv)
        _validate_columns(df)
        df = _normalize_df(df)

        if df.empty:
            print("No valid rows to upsert.")
            return 0

        # Add a uniform timestamp for this batch run.
        df["last_updated_time"] = _now_iso()

        # Convert to list-of-dict for UNWIND.
        records = df.to_dict(orient="records")
        batches = _chunked(records, max(1, args.batch_size))

        driver = GraphDatabase.driver(
            args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password)
        )

        with driver.session() as session:
            for batch in batches:
                session.execute_write(_upsert_batch, batch)

        driver.close()
        print(f"Upsert complete. Rows: {len(records)}, Batches: {len(batches)}")
        return 0

    except Exception as exc:
        print(f"Incremental update failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
