# SupplyChain-KG-RCA-Agent

Event-driven root cause analysis for supply chain knowledge graphs, combining GraphRAG, Neo4j, guided investigation probes, and explainable evidence chains.

## What This Project Does

This project provides a runnable supply-chain analysis system built around:

- `Neo4j` as the knowledge graph store
- `FastAPI` as the backend service
- `LangChain / LangGraph` for GraphRAG-style orchestration
- a dedicated `RCA` pipeline for anomaly investigation
- an event layer (`SupplierNotice`, `QualityInspection`, `DelayEvent`, `SourceRecord`) for explainable root-cause analysis

It supports:

- supply-chain Q&A
- focused graph visualization
- structured root-cause analysis
- CSV bootstrap import
- incremental CSV / PDF / text ingestion
- event-layer generation and import for RCA demos

## Scope and Reuse

This repository should be understood as a **domain-specific RCA prototype for the current supply-chain knowledge graph**, not as a fully generic RCA platform.

At the moment, several parts of the system are intentionally coupled to the current schema and demo dataset, including:

- entity types such as `Supplier`, `Component`, `Product`, `Order`, `Carrier`
- supply-chain relationship paths used by the collectors and investigation probes
- RCA intent types such as `order_delay`, `supplier_risk`, `carrier_delay`, and `product_impact`
- the current event-layer design around `SupplierNotice`, `QualityInspection`, and `DelayEvent`

This means the project is highly reusable for **this supply-chain graph model**, but it is **not plug-and-play for arbitrary knowledge graphs or arbitrary RCA datasets** without adaptation.

The parts with stronger reuse potential are:

- the FastAPI service layer
- the GraphRAG orchestration flow
- the RCA investigation loop
- the safe probe pattern
- the structured RCA response format
- the explainable frontend workflow such as `Investigation Trail` and `Why This Cause`

If you want to adapt this project to a different domain, the recommended approach is to keep the framework and replace the domain-specific schema, probes, collectors, and RCA policies.

## Core Features

- GraphRAG question answering over a supply-chain knowledge graph
- RCA-specific routing for:
  - `order_delay`
  - `supplier_risk`
  - `carrier_delay`
  - `product_impact`
- Event-driven RCA investigation with safe probes
- Explainable RCA output with:
  - `candidate_causes`
  - `supporting_nodes`
  - `supporting_edges`
  - `evidence_chain`
  - `source_records`
- Web demo with:
  - `Investigation Trail`
  - `RCA Evidence` graph view
  - `Why This Cause` panel

## Repository Layout

```text
SupplyChain-KG-RCA-Agent/
├─ graphrag_api.py              # FastAPI entrypoint
├─ supplychain_agent.py         # GraphRAG + RCA routing executor
├─ kg_tools.py                  # graph query tools
├─ ingest_service.py            # CSV / PDF / text ingestion
├─ build_graph.py               # bootstrap CSV import into Neo4j
├─ incremental_update.py        # incremental update CLI
├─ generate_ultimate_data.py    # synthetic base data generator
├─ generate_event_layer.py      # synthetic event-layer generator
├─ import_event_layer.py        # import generated event layer into Neo4j
├─ event_layer_demo.cypher      # event-layer Cypher demo queries
├─ EVENT_LAYER_SPEC.md          # event-layer schema
├─ RCA_ARCHITECTURE.md          # RCA architecture notes
├─ demo_queries.cypher          # general Cypher demo queries
├─ index.html                   # main frontend demo
├─ admin.html                   # ingestion demo page
├─ rca/                         # RCA engine, collectors, investigator, scorers
├─ event_data/                  # generated event-layer demo CSVs
├─ 数据/                         # base synthetic supply-chain CSV
└─ test/                        # auxiliary test/demo files
```

## Runtime Requirements

- Python `3.10+`
- Neo4j `5.x`
- Neo4j Bolt enabled
- recommended: Neo4j Graph Data Science plugin for graph algorithms

Without GDS, most of the system still runs, but GDS-dependent analyses may be unavailable.

## Installation

### 1. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in your values.

```powershell
Copy-Item .env.example .env
```

Required configuration:

- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `LLM_MODEL`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`

Example:

```env
OPENAI_API_KEY=your_api_key
OPENAI_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

NEO4J_URI=neo4j://127.0.0.1:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
```

## Quick Start

### Step 1. Import base graph data

```powershell
$env:CSV_FILE="数据/Supply_Chain_Data_Fake.csv"
python .\build_graph.py
```

### Step 2. Import event-layer demo data

If you want to regenerate the event layer:

```powershell
python .\generate_event_layer.py
```

Then import it:

```powershell
python .\import_event_layer.py
```

### Step 3. Start the backend

```powershell
python .\graphrag_api.py
```

Backend endpoints:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/openapi.json`

### Step 4. Open the frontend

Open:

- `index.html`

The page calls:

- `POST /api/chat/stream`
- `POST /api/graph_view`

## Main API Endpoints

- `GET /api/health`
- `GET /api/catalog`
- `POST /api/chat`
- `POST /api/chat/stream`
- `POST /api/rca`
- `POST /api/graph_view`
- `POST /api/ingest/csv`
- `POST /api/ingest/pdf`
- `POST /api/ingest/text`

## Recommended RCA Demo Questions

- `ORD-2024-100001 延误根因是什么？`
- `为什么长江存储 (YMTC) 风险高？`
- `4K超清 无线VR一体机 受影响的根因是什么？`
- `为什么跨越速运会导致延误？`

## RCA Output Shape

The RCA pipeline returns structured results including:

- `validation`
- `graph_metrics`
- `incident_summary`
- `risk_signals`
- `candidate_causes`
- `recommended_actions`
- `investigation`
- `investigation_steps`
- `evidence_graph`

Each `candidate_cause` may include:

- `evidence`
- `supporting_nodes`
- `supporting_edges`
- `evidence_chain`
- `source_records`
- `evidence_mode`

## Notes for Open-Source Publishing

This repository intentionally keeps the current runnable layout instead of doing a large refactor.

Reason:

- preserve existing import paths
- avoid breaking startup commands
- keep frontend/backend integration stable

If you want to publish a cleaner public version, the recommended next step is a **light repository cleanup**, not a deep code move:

- keep only this project's files
- exclude unrelated reference repos
- keep runtime scripts and docs stable
- refactor module layout later if needed

## Files That Should Not Be Published Together

These directories are local references and should not be included in a clean public repo:

- `onyx-main/`
- `trustgraph-master/`

## Related Docs

- [RCA_ARCHITECTURE.md](RCA_ARCHITECTURE.md)
- [EVENT_LAYER_SPEC.md](EVENT_LAYER_SPEC.md)
- [event_layer_demo.cypher](event_layer_demo.cypher)
