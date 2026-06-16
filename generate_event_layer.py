import csv
import os
import random
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional


DATA_FILE = Path("数据") / "Supply_Chain_Data_Fake.csv"
OUTPUT_DIR = Path("event_data")
SEED = int(os.getenv("EVENT_LAYER_SEED", "20240616"))

DATE_FMT = "%Y/%m/%d %H:%M"

FIELD_ORDER_ID = "订单ID"
FIELD_ORDER_DATE = "订单日期"
FIELD_SHIP_DATE = "发货日期"
FIELD_SCHEDULED_DATE = "预计送达日期"
FIELD_ACTUAL_DATE = "实际送达日期"
FIELD_ORDER_STATUS = "订单状态"
FIELD_PRODUCT_ID = "产品ID"
FIELD_PRODUCT_SKU = "产品SKU"
FIELD_PRODUCT_NAME = "产品名称"
FIELD_SUPPLIER_NAME = "供应商名称"
FIELD_COMPONENT_NAME = "核心组件名称"
FIELD_DEFECT_RATE = "次品率"
FIELD_CARRIER_NAME = "承运商名称"
FIELD_TRANS_MODE = "运输方式"
FIELD_SHIP_MODE = "发货模式"
FIELD_DAYS_SCHEDULED = "计划物流天数"
FIELD_DAYS_REAL = "实际物流天数"
FIELD_LATE_RISK = "发货延误风险_标签"
FIELD_DELIVERY_STATUS = "物流运送状态"


def parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    return datetime.strptime(value.strip(), DATE_FMT)


def as_int(value: str, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def slugify(text: str) -> str:
    safe = []
    for char in (text or "").strip():
        if char.isalnum():
            safe.append(char.upper())
        else:
            safe.append("_")
    collapsed = "".join(safe).strip("_")
    while "__" in collapsed:
        collapsed = collapsed.replace("__", "_")
    return collapsed or "NA"


def choose_delay_stage(ship_mode: str, trans_mode: str, delay_hours: float) -> str:
    ship_text = ship_mode or ""
    trans_text = trans_mode or ""
    if "次晨" in ship_text or "当日" in ship_text:
        return "delivery"
    if "航空" in trans_text:
        return "transfer" if delay_hours < 48 else "linehaul"
    if "海" in trans_text:
        return "linehaul"
    if "公路" in trans_text:
        return "pickup" if delay_hours < 24 else "linehaul"
    return "delivery"


def choose_delay_reason(row: Dict[str, str], delay_hours: float, severe_defect: bool) -> str:
    trans_text = row.get(FIELD_TRANS_MODE, "")
    ship_text = row.get(FIELD_SHIP_MODE, "")
    if severe_defect:
        return "inspection_hold"
    if "航空" in trans_text:
        return "air_cargo_rollover"
    if "海" in trans_text:
        return "port_transfer_backlog"
    if "次晨" in ship_text or delay_hours > 60:
        return "carrier_capacity"
    return "linehaul_delay"


def choose_notice_type(high_defect: bool, avg_delay_days: float, order_count: int) -> str:
    if high_defect:
        return "quality_alert"
    if avg_delay_days >= 2.5:
        return "delivery_reschedule"
    if order_count >= 3:
        return "capacity_drop"
    return "material_shortage"


def choose_notice_reason(high_defect: bool, avg_delay_days: float) -> str:
    if high_defect:
        return "yield_drop"
    if avg_delay_days >= 2.5:
        return "demand_spike"
    return "upstream_shortage"


def choose_failure_mode(component_name: str, product_name: str) -> str:
    joined = f"{component_name}|{product_name}"
    if "CMOS" in joined or "图像" in joined:
        return "sensor_noise"
    if "OLED" in joined or "屏" in joined:
        return "display_mura"
    if "电池" in joined:
        return "capacity_drift"
    if "连接" in joined or "模组" in joined:
        return "connector_shift"
    return "functional_drift"


def render_source_row_key(row: Dict[str, str]) -> str:
    return "|".join(
        [
            row.get(FIELD_ORDER_ID, ""),
            row.get(FIELD_PRODUCT_ID, ""),
            row.get(FIELD_SUPPLIER_NAME, ""),
            row.get(FIELD_COMPONENT_NAME, ""),
        ]
    )


def load_rows(path: Path) -> List[Dict[str, str]]:
    for encoding in ("utf-8-sig", "gbk"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                return list(reader)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("event_layer", b"", 0, 1, f"Unable to decode {path}")


def fabricate_quality_inspections(rows: Iterable[Dict[str, str]], rng: random.Random):
    inspections = []
    records = []

    for row in rows:
        defect_rate = as_float(row.get(FIELD_DEFECT_RATE))
        days_scheduled = as_int(row.get(FIELD_DAYS_SCHEDULED))
        days_real = as_int(row.get(FIELD_DAYS_REAL))
        late_risk = as_int(row.get(FIELD_LATE_RISK))
        order_date = parse_dt(row.get(FIELD_ORDER_DATE, ""))
        ship_date = parse_dt(row.get(FIELD_SHIP_DATE, ""))
        if not order_date:
            continue

        trigger = defect_rate >= 0.0032 or late_risk == 1 or (days_real - days_scheduled) >= 2
        if not trigger:
            continue

        sample_size = rng.randint(80, 220)
        failed_units = max(1, round(sample_size * defect_rate * rng.uniform(0.8, 1.35)))
        observed_defect_rate = round(failed_units / sample_size, 5)

        if defect_rate >= 0.005 or observed_defect_rate >= 0.006:
            result = "FAIL"
            severity = "high"
        elif defect_rate >= 0.0032 or late_risk == 1:
            result = "WARN"
            severity = "medium"
        else:
            result = "PASS"
            severity = "low"

        inspection_time = (ship_date or order_date) - timedelta(hours=rng.randint(6, 54))
        inspection_id = f"QI-{row[FIELD_ORDER_ID]}-{slugify(row[FIELD_COMPONENT_NAME])}"
        source_ref = f"SRC-QI-{row[FIELD_ORDER_ID]}"
        batch_id = f"BATCH-{slugify(row[FIELD_SUPPLIER_NAME])}-{order_date.strftime('%Y%m')}"

        inspection = {
            "id": inspection_id,
            "batch_id": batch_id,
            "inspection_time": inspection_time.strftime("%Y-%m-%d %H:%M:%S"),
            "result": result,
            "severity": severity,
            "sample_size": str(sample_size),
            "failed_units": str(failed_units),
            "observed_defect_rate": f"{observed_defect_rate:.5f}",
            "source_ref": source_ref,
            "supplier_name": row.get(FIELD_SUPPLIER_NAME, ""),
            "component_name": row.get(FIELD_COMPONENT_NAME, ""),
            "product_id": row.get(FIELD_PRODUCT_ID, ""),
            "order_id": row.get(FIELD_ORDER_ID, ""),
            "inspector": rng.choice(["Line-QA-A", "Line-QA-B", "Factory-QA-7"]),
            "failure_mode": choose_failure_mode(
                row.get(FIELD_COMPONENT_NAME, ""), row.get(FIELD_PRODUCT_NAME, "")
            ),
        }
        inspections.append(inspection)
        records.append(
            {
                "id": source_ref,
                "source_type": "synthetic_order_row",
                "source_system": "event_layer_generator",
                "source_row_key": render_source_row_key(row),
                "created_at": inspection["inspection_time"],
                "summary": (
                    f"订单 {row.get(FIELD_ORDER_ID, '')} 的质量抽检事件，"
                    f"组件 {row.get(FIELD_COMPONENT_NAME, '')}，结果 {result}"
                ),
            }
        )

    return inspections, records


def fabricate_delay_events(rows: Iterable[Dict[str, str]], rng: random.Random):
    delays = []
    records = []

    for row in rows:
        scheduled = parse_dt(row.get(FIELD_SCHEDULED_DATE, ""))
        actual = parse_dt(row.get(FIELD_ACTUAL_DATE, ""))
        ship_date = parse_dt(row.get(FIELD_SHIP_DATE, ""))
        if not scheduled or not actual:
            continue

        explicit_delay_hours = max(0.0, (actual - scheduled).total_seconds() / 3600.0)
        days_gap = max(0, as_int(row.get(FIELD_DAYS_REAL)) - as_int(row.get(FIELD_DAYS_SCHEDULED)))
        late_risk = as_int(row.get(FIELD_LATE_RISK))
        defect_rate = as_float(row.get(FIELD_DEFECT_RATE))

        if explicit_delay_hours <= 0 and late_risk == 0 and days_gap <= 0:
            continue

        delay_hours = max(explicit_delay_hours, days_gap * 24)
        if delay_hours >= 72:
            severity = "high"
        elif delay_hours >= 24:
            severity = "medium"
        else:
            severity = "low"

        event_time = (ship_date or scheduled) + timedelta(hours=max(4, int(delay_hours * 0.45)))
        delay_id = f"DE-{row[FIELD_ORDER_ID]}"
        source_ref = f"SRC-DE-{row[FIELD_ORDER_ID]}"
        stage = choose_delay_stage(row.get(FIELD_SHIP_MODE, ""), row.get(FIELD_TRANS_MODE, ""), delay_hours)
        delay = {
            "id": delay_id,
            "delay_stage": stage,
            "occurred_at": event_time.strftime("%Y-%m-%d %H:%M:%S"),
            "severity": severity,
            "reason_code": choose_delay_reason(row, delay_hours, defect_rate >= 0.005),
            "delay_hours": f"{delay_hours:.1f}",
            "eta_before": scheduled.strftime("%Y-%m-%d %H:%M:%S"),
            "eta_after": actual.strftime("%Y-%m-%d %H:%M:%S"),
            "source_ref": source_ref,
            "order_id": row.get(FIELD_ORDER_ID, ""),
            "carrier_name": row.get(FIELD_CARRIER_NAME, ""),
            "product_id": row.get(FIELD_PRODUCT_ID, ""),
            "supplier_name": row.get(FIELD_SUPPLIER_NAME, ""),
            "trans_mode": row.get(FIELD_TRANS_MODE, ""),
            "ship_mode": row.get(FIELD_SHIP_MODE, ""),
            "location_hint": rng.choice(["武汉转运场", "杭州分拨中心", "深圳航站", "上海港前置仓"]),
        }
        delays.append(delay)
        records.append(
            {
                "id": source_ref,
                "source_type": "synthetic_order_row",
                "source_system": "event_layer_generator",
                "source_row_key": render_source_row_key(row),
                "created_at": delay["occurred_at"],
                "summary": (
                    f"订单 {row.get(FIELD_ORDER_ID, '')} 出现 {stage} 延迟，"
                    f"承运商 {row.get(FIELD_CARRIER_NAME, '')}，延迟 {delay_hours:.1f} 小时"
                ),
            }
        )

    return delays, records


def fabricate_supplier_notices(rows: Iterable[Dict[str, str]], rng: random.Random):
    by_supplier: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_supplier[row.get(FIELD_SUPPLIER_NAME, "")].append(row)

    notices = []
    records = []

    for supplier_name, supplier_rows in by_supplier.items():
        if not supplier_name:
            continue

        avg_defect = sum(as_float(row.get(FIELD_DEFECT_RATE)) for row in supplier_rows) / len(supplier_rows)
        avg_delay_days = sum(
            max(0, as_int(row.get(FIELD_DAYS_REAL)) - as_int(row.get(FIELD_DAYS_SCHEDULED)))
            for row in supplier_rows
        ) / len(supplier_rows)
        risky_rows = [
            row
            for row in supplier_rows
            if as_int(row.get(FIELD_LATE_RISK)) == 1 or as_float(row.get(FIELD_DEFECT_RATE)) >= 0.0035
        ]
        if not risky_rows:
            continue

        high_defect = avg_defect >= 0.0038 or any(as_float(row.get(FIELD_DEFECT_RATE)) >= 0.005 for row in risky_rows)
        if not high_defect and avg_delay_days < 1.5 and len(risky_rows) < 2:
            continue

        anchor = max(
            risky_rows,
            key=lambda row: (
                as_int(row.get(FIELD_LATE_RISK)),
                as_float(row.get(FIELD_DEFECT_RATE)),
                max(0, as_int(row.get(FIELD_DAYS_REAL)) - as_int(row.get(FIELD_DAYS_SCHEDULED))),
            ),
        )

        order_date = parse_dt(anchor.get(FIELD_ORDER_DATE, ""))
        ship_date = parse_dt(anchor.get(FIELD_SHIP_DATE, ""))
        created_at = (ship_date or order_date or datetime(2024, 1, 1)) - timedelta(hours=rng.randint(12, 72))

        notice_type = choose_notice_type(high_defect, avg_delay_days, len(risky_rows))
        reason_code = choose_notice_reason(high_defect, avg_delay_days)
        severity = "high" if high_defect or avg_delay_days >= 2.5 else "medium"
        confidence = 0.82 if severity == "high" else 0.68
        notice_id = f"SN-{slugify(supplier_name)}"
        source_ref = f"SRC-SN-{slugify(supplier_name)}"

        notice = {
            "id": notice_id,
            "notice_type": notice_type,
            "severity": severity,
            "reason_code": reason_code,
            "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "effective_from": (created_at + timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
            "summary": (
                f"{supplier_name} 对组件 {anchor.get(FIELD_COMPONENT_NAME, '')} 发出 {notice_type} 预警，"
                f"关联订单 {anchor.get(FIELD_ORDER_ID, '')}"
            ),
            "confidence": f"{confidence:.2f}",
            "supplier_name": supplier_name,
            "component_name": anchor.get(FIELD_COMPONENT_NAME, ""),
            "product_id": anchor.get(FIELD_PRODUCT_ID, ""),
            "order_id": anchor.get(FIELD_ORDER_ID, ""),
            "expected_impact": (
                f"平均次品率 {avg_defect:.4f}，平均额外物流天数 {avg_delay_days:.1f} 天，"
                f"风险订单 {len(risky_rows)} 个"
            ),
            "source_ref": source_ref,
        }
        notices.append(notice)
        records.append(
            {
                "id": source_ref,
                "source_type": "synthetic_supplier_alert",
                "source_system": "event_layer_generator",
                "source_row_key": f"{supplier_name}|{anchor.get(FIELD_COMPONENT_NAME, '')}|{anchor.get(FIELD_PRODUCT_ID, '')}",
                "created_at": notice["created_at"],
                "summary": (
                    f"供应商 {supplier_name} 生成 {notice_type} 预警，"
                    f"锚定组件 {anchor.get(FIELD_COMPONENT_NAME, '')}"
                ),
            }
        )

    return notices, records


def write_csv(path: Path, fieldnames: List[str], rows: Iterable[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    rng = random.Random(SEED)
    rows = load_rows(DATA_FILE)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    quality_inspections, qi_records = fabricate_quality_inspections(rows, rng)
    delay_events, de_records = fabricate_delay_events(rows, rng)
    supplier_notices, sn_records = fabricate_supplier_notices(rows, rng)

    source_record_map = {row["id"]: row for row in [*qi_records, *de_records, *sn_records]}
    source_records = sorted(source_record_map.values(), key=lambda row: row["id"])

    write_csv(
        OUTPUT_DIR / "quality_inspections.csv",
        [
            "id",
            "batch_id",
            "inspection_time",
            "result",
            "severity",
            "sample_size",
            "failed_units",
            "observed_defect_rate",
            "source_ref",
            "supplier_name",
            "component_name",
            "product_id",
            "order_id",
            "inspector",
            "failure_mode",
        ],
        quality_inspections,
    )
    write_csv(
        OUTPUT_DIR / "delay_events.csv",
        [
            "id",
            "delay_stage",
            "occurred_at",
            "severity",
            "reason_code",
            "delay_hours",
            "eta_before",
            "eta_after",
            "source_ref",
            "order_id",
            "carrier_name",
            "product_id",
            "supplier_name",
            "trans_mode",
            "ship_mode",
            "location_hint",
        ],
        delay_events,
    )
    write_csv(
        OUTPUT_DIR / "supplier_notices.csv",
        [
            "id",
            "notice_type",
            "severity",
            "reason_code",
            "created_at",
            "effective_from",
            "summary",
            "confidence",
            "supplier_name",
            "component_name",
            "product_id",
            "order_id",
            "expected_impact",
            "source_ref",
        ],
        supplier_notices,
    )
    write_csv(
        OUTPUT_DIR / "source_records.csv",
        ["id", "source_type", "source_system", "source_row_key", "created_at", "summary"],
        source_records,
    )

    print(f"Loaded rows: {len(rows)}")
    print(f"Generated supplier notices: {len(supplier_notices)}")
    print(f"Generated quality inspections: {len(quality_inspections)}")
    print(f"Generated delay events: {len(delay_events)}")
    print(f"Generated source records: {len(source_records)}")
    print(f"Output directory: {OUTPUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
