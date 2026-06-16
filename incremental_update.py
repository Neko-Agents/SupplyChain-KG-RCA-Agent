"""
Incremental update script for Supply Chain KG (Neo4j).

Usage:
  python incremental_update.py --csv .\\test\\test_increment.csv \
      --neo4j-uri neo4j://127.0.0.1:7687 --neo4j-user neo4j --neo4j-password 88888888

Notes:
- Supports both Chinese and English wide-table CSV headers.
- Reuses the same normalization and upsert logic as the main ingest pipeline.
- Uses UNWIND with batch submission (default 2000).
"""

import argparse
import sys
from typing import Dict, List

from neo4j import GraphDatabase

from ingest_service import _chunked, _df_to_records, _read_csv, _upsert_batch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incremental CSV upsert to Neo4j")
    parser.add_argument("--csv", required=True, help="Path to increment CSV file")
    parser.add_argument("--neo4j-uri", default="neo4j://127.0.0.1:7687")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="88888888")
    parser.add_argument("--batch-size", type=int, default=2000)
    parser.add_argument(
        "--update-mode",
        choices=["safe", "overwrite"],
        default="safe",
        help="Whether to only fill missing values or overwrite existing ones.",
    )
    return parser.parse_args()


def _filter_valid_records(records: List[Dict]) -> List[Dict]:
    valid: List[Dict] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        if not row.get("order_id"):
            continue
        if not row.get("customer_id"):
            continue
        if not row.get("supplier_name"):
            continue
        valid.append(row)
    return valid


def main() -> int:
    args = parse_args()

    try:
        df = _read_csv(args.csv)
        records = _df_to_records(df)
        records = _filter_valid_records(records)

        if not records:
            print("No valid rows to upsert.")
            return 0

        batches = list(_chunked(records, max(1, args.batch_size)))

        driver = GraphDatabase.driver(
            args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password)
        )

        with driver.session() as session:
            for batch in batches:
                session.execute_write(_upsert_batch, batch, args.update_mode)

        driver.close()
        print(
            f"Upsert complete. Rows: {len(records)}, Batches: {len(batches)}, "
            f"UpdateMode: {args.update_mode}"
        )
        return 0

    except Exception as exc:
        print(f"Incremental update failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
