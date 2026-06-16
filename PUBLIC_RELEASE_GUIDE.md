# Public Release Guide

## Recommended Repository Name

`SupplyChain-KG-RCA-Agent`

## Recommended Repository Description

Event-driven root cause analysis for supply chain knowledge graphs, combining GraphRAG, Neo4j, guided investigation probes, and explainable evidence chains.

---

## 1. Goal

This guide is for creating a **clean public GitHub repository** from the current local project.

The purpose is:

- keep the project runnable
- avoid publishing unrelated local reference code
- avoid breaking import paths or startup commands
- keep the repository understandable for external users

This project should be published using a **light cleanup**, not a deep refactor.

---

## 2. Keep vs Remove

### Keep in the Public Repository

Core runtime files:

- `.gitignore`
- `.env.example`
- `README.md`
- `requirements.txt`
- `graphrag_api.py`
- `supplychain_agent.py`
- `kg_tools.py`
- `ingest_service.py`
- `build_graph.py`
- `incremental_update.py`
- `generate_ultimate_data.py`
- `generate_event_layer.py`
- `import_event_layer.py`
- `index.html`
- `admin.html`

RCA files:

- `rca/`
- `RCA_ARCHITECTURE.md`
- `EVENT_LAYER_SPEC.md`

Demo / query files:

- `demo_queries.cypher`
- `event_layer_demo.cypher`

Demo data:

- `数据/Supply_Chain_Data_Fake.csv`
- `event_data/`

Optional demo/test assets:

- `test/`

### Do Not Put in the Public Repository

These are unrelated or local-only:

- `onyx-main/`
- `trustgraph-master/`
- `.venv/`
- `__pycache__/`
- `uploads/`
- `.vscode/`
- local temp files
- local credentials

### Never Commit

- `.env`
- private API keys
- personal tokens
- private Neo4j credentials

---

## 3. Recommended Public Repository Structure

```text
SupplyChain-KG-RCA-Agent/
├─ .env.example
├─ .gitignore
├─ README.md
├─ requirements.txt
├─ graphrag_api.py
├─ supplychain_agent.py
├─ kg_tools.py
├─ ingest_service.py
├─ build_graph.py
├─ incremental_update.py
├─ generate_ultimate_data.py
├─ generate_event_layer.py
├─ import_event_layer.py
├─ index.html
├─ admin.html
├─ demo_queries.cypher
├─ event_layer_demo.cypher
├─ RCA_ARCHITECTURE.md
├─ EVENT_LAYER_SPEC.md
├─ rca/
├─ event_data/
├─ 数据/
└─ test/
```

---

## 4. Why No Large Refactor Right Now

The current project already runs with these entrypoints:

- `python build_graph.py`
- `python generate_event_layer.py`
- `python import_event_layer.py`
- `python graphrag_api.py`

If you move files into new packages right now, you will also need to change:

- import paths
- startup docs
- environment setup docs
- deployment scripts
- local testing paths

That is possible later, but it is not necessary for a usable first public release.

For now, the correct approach is:

- clean repository boundaries
- preserve runtime layout
- improve documentation

---

## 5. How to Create a New Clean Repository

### Option A: Create a New GitHub Repository First

Create a new empty repository on GitHub:

- Name: `SupplyChain-KG-RCA-Agent`
- Visibility: your choice
- Do not initialize with README if you plan to push from local

Then locally:

```powershell
git remote remove origin
git remote add origin https://github.com/<your-account>/SupplyChain-KG-RCA-Agent.git
git push -u origin codex-full-rca-update:main
```

### Option B: Copy Only Public Files into a Fresh Folder

If the current repository is too noisy, create a new folder and copy only the public files:

```text
D:\Codes\SupplyChain-KG-RCA-Agent
```

Then copy only the files listed in section 2.

After that:

```powershell
cd D:\Codes\SupplyChain-KG-RCA-Agent
git init
git add .
git commit -m "Initial public release"
git branch -M main
git remote add origin https://github.com/<your-account>/SupplyChain-KG-RCA-Agent.git
git push -u origin main
```

Option B is cleaner if you want a truly minimal public repo.

---

## 6. Recommended First Commit Scope

For the first public release, include:

- GraphRAG backend
- RCA engine
- event layer generator/importer
- frontend demo
- sample graph data
- event demo data
- docs

You do not need to include:

- unrelated experimental reference repositories
- local sandbox directories

---

## 7. Pre-Publish Checklist

Before pushing, verify:

- `.env` is not tracked
- `onyx-main/` is not tracked
- `trustgraph-master/` is not tracked
- README startup steps are correct
- `requirements.txt` installs all runtime dependencies
- `.env.example` contains placeholders, not real secrets
- Neo4j import commands are documented
- event-layer generation/import steps are documented

---

## 8. Suggested Release Notes

You can use this as the initial release summary:

### Highlights

- Added a dedicated RCA pipeline for supply-chain anomalies
- Added event-layer RCA evidence with `SupplierNotice`, `QualityInspection`, `DelayEvent`, and `SourceRecord`
- Added investigation probes and structured RCA outputs
- Added `Why This Cause` UI for explainable root-cause exploration
- Added event-layer generator and Neo4j importer

---

## 9. Suggested README Positioning

When presenting the project publicly, describe it as:

> A Neo4j-based supply-chain GraphRAG and root-cause analysis system with event-driven investigation and explainable evidence chains.

This is clearer than describing it only as a chatbot or only as a knowledge graph demo.

---

## 10. Best Recommendation

If you want the cleanest public result:

- create a **new GitHub repository**
- publish only the files listed in the keep section
- avoid publishing the current noisy workspace as-is

That gives you:

- cleaner star/fork experience
- easier onboarding for others
- fewer accidental files
- lower maintenance cost

