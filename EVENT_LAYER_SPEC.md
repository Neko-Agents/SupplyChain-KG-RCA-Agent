# Event Layer Spec

This project currently models mostly structural dependency. The event layer adds time-ordered operational evidence so RCA can explain how an anomaly propagated, not only who is connected to whom.

## Goals

- Capture direct evidence of abnormal behavior
- Preserve lightweight provenance without changing the base order CSV schema
- Support RCA chains such as `SupplierNotice -> QualityInspection -> DelayEvent -> Order`

## Event Nodes

### `SupplierNotice`

Represents a supplier-side warning or behavior change.

Required properties:

- `id`
- `notice_type`
- `severity`
- `reason_code`
- `created_at`
- `effective_from`
- `summary`
- `confidence`

Optional properties:

- `supplier_name`
- `component_name`
- `product_id`
- `order_id`
- `expected_impact`
- `source_ref`

Recommended `notice_type` values:

- `capacity_drop`
- `quality_alert`
- `delivery_reschedule`
- `material_shortage`

Recommended `reason_code` values:

- `upstream_shortage`
- `yield_drop`
- `maintenance_window`
- `demand_spike`

### `QualityInspection`

Represents a quality check on a supplier/component/product combination.

Required properties:

- `id`
- `batch_id`
- `inspection_time`
- `result`
- `severity`
- `sample_size`
- `failed_units`
- `observed_defect_rate`
- `source_ref`

Optional properties:

- `supplier_name`
- `component_name`
- `product_id`
- `order_id`
- `inspector`
- `failure_mode`

Recommended `result` values:

- `PASS`
- `WARN`
- `FAIL`

### `DelayEvent`

Represents a concrete order-delay event in the logistics chain.

Required properties:

- `id`
- `delay_stage`
- `occurred_at`
- `severity`
- `reason_code`
- `delay_hours`
- `eta_before`
- `eta_after`
- `source_ref`

Optional properties:

- `order_id`
- `carrier_name`
- `product_id`
- `supplier_name`
- `trans_mode`
- `ship_mode`
- `location_hint`

Recommended `delay_stage` values:

- `pickup`
- `linehaul`
- `transfer`
- `delivery`

### `SourceRecord`

Lightweight provenance node pointing back to the synthetic source row used to fabricate the event.

Required properties:

- `id`
- `source_type`
- `source_system`
- `source_row_key`
- `created_at`
- `summary`

Recommended `source_type` values:

- `synthetic_order_row`
- `synthetic_supplier_alert`

## Relationships

- `(Supplier)-[:ISSUED_NOTICE]->(SupplierNotice)`
- `(SupplierNotice)-[:AFFECTS_COMPONENT]->(Component)`
- `(SupplierNotice)-[:AFFECTS_PRODUCT]->(Product)`
- `(SupplierNotice)-[:AFFECTS_ORDER]->(Order)`

- `(Supplier)-[:UNDERWENT_INSPECTION]->(QualityInspection)`
- `(QualityInspection)-[:INSPECTED_COMPONENT]->(Component)`
- `(QualityInspection)-[:INSPECTED_PRODUCT]->(Product)`
- `(QualityInspection)-[:IMPACTS_ORDER]->(Order)`

- `(Order)-[:HAS_DELAY_EVENT]->(DelayEvent)`
- `(Carrier)-[:TRIGGERED_DELAY_EVENT]->(DelayEvent)`
- `(DelayEvent)-[:IMPACTS_PRODUCT]->(Product)`
- `(DelayEvent)-[:RELATED_TO_SUPPLIER]->(Supplier)`

- `(SupplierNotice)-[:SUPPORTED_BY]->(SourceRecord)`
- `(QualityInspection)-[:SUPPORTED_BY]->(SourceRecord)`
- `(DelayEvent)-[:SUPPORTED_BY]->(SourceRecord)`

## Generated CSV Files

The synthetic generator writes the following files under `event_data/`:

- `supplier_notices.csv`
- `quality_inspections.csv`
- `delay_events.csv`
- `source_records.csv`

## How RCA Should Use It

Direct evidence should be preferred over aggregate evidence:

- `SupplierNotice`, `QualityInspection.FAIL`, `DelayEvent`

Structural amplification should be treated as secondary evidence:

- single-source dependency
- component reuse
- supplier span
- profit exposure

This means RCA can tell the user:

- which supplier warning was first observed
- which inspection failed
- which order delay event materialized afterward
- which source record supports each step
