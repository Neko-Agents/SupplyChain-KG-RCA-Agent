# SupplyChainGraph

## API Quick Links

- External API reference: [API_REFERENCE.md](API_REFERENCE.md)
- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI schema: `http://127.0.0.1:8000/openapi.json`

面向供应链场景的知识图谱与 GraphRAG 后端服务。项目使用 Neo4j 存储供应链实体与关系，使用 FastAPI 对外提供统一接口，支持自然语言分析、图谱查询、CSV/PDF/文本导入和多类供应链风险分析。

## 项目定位

这个仓库的核心是一个可独立运行的后端服务，不依赖前端也可以直接使用。对外能力主要包括：

- 供应商断供影响分析
- 订单根因追踪
- 瓶颈供应商识别
- 产品、客户、物流、区域等经营分析
- 图谱子图查询
- CSV / PDF / 文本知识导入

前端页面 `index.html` 和 `admin.html` 只是演示界面，不是项目的核心交付物。

## 技术栈

- Python
- FastAPI
- Uvicorn
- Neo4j
- Neo4j Graph Data Science
- Pandas
- NumPy
- LangChain
- LangGraph
- langchain-neo4j
- pypdf

## 目录结构

```text
SupplyChainGraph/
├─ graphrag_api.py            # 后端服务入口
├─ supplychain_agent.py       # GraphRAG Agent 编排
├─ kg_tools.py                # 固定分析工具
├─ ingest_service.py          # CSV / PDF / 文本导入服务
├─ build_graph.py             # 初始建图脚本
├─ incremental_update.py      # 命令行增量更新脚本
├─ generate_ultimate_data.py  # 样例数据生成脚本
├─ demo_queries.cypher        # 示例 Cypher
├─ CSV_IMPORT_SPEC.md         # CSV 导入规范
├─ index.html                 # 问答演示页面
├─ admin.html                 # 导入演示页面
├─ 数据/                       # 示例数据
├─ uploads/                   # 上传文件暂存目录
└─ test/                      # 测试文件
```

## 图谱模型

### 节点

- `Customer`
- `Order`
- `Product`
- `Category`
- `Department`
- `Supplier`
- `Component`
- `Carrier`

### 关系

- `PLACED_ORDER`
- `CONTAINS_PRODUCT`
- `BELONGS_TO_CATEGORY`
- `BELONGS_TO_DEPARTMENT`
- `SUPPLIES_COMPONENT`
- `USED_IN`
- `SHIPPED_BY`

## 环境要求

- Python 3.10 及以上
- Neo4j 5.x
- 已启用 Neo4j Bolt
- 建议安装 Neo4j GDS 插件

说明：

- 没有 GDS 时，项目仍可运行，但瓶颈分析等图算法能力会失败。
- 没有 LLM 配置时，Agent 问答和部分 PDF/文本抽取能力无法使用。

## 快速开始

以下步骤按 Windows PowerShell 编写，可直接复制执行。

### 1. 创建虚拟环境并安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install fastapi uvicorn neo4j pandas numpy langchain-core langchain-openai langgraph langchain-neo4j pypdf python-multipart
```

### 2. 配置 Neo4j 连接

如果你本地 Neo4j 使用默认配置，可以直接用下面这组环境变量：

```powershell
$env:NEO4J_URI="neo4j://127.0.0.1:7687"
$env:NEO4J_USER="neo4j"
$env:NEO4J_PASSWORD="88888888"
```

如果你已经修改过 Neo4j 用户名或密码，请替换成你自己的值。

### 3. 配置 LLM

在项目根目录创建 `.env` 文件：

```powershell
@'
OPENAI_API_KEY=your_api_key
OPENAI_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
'@ | Set-Content .env -Encoding UTF8
```

说明：

- `OPENAI_API_KEY` 必填，否则智能问答服务无法启动
- `OPENAI_API_BASE` 和 `LLM_MODEL` 可以按你实际接入的平台修改

### 4. 导入初始数据

```powershell
$env:CSV_FILE="数据/Supply_Chain_Data_Fake.csv"
python .\build_graph.py
```

如果你想指定批大小：

```powershell
$env:BATCH_SIZE="1000"
python .\build_graph.py
```

### 5. 启动后端服务

```powershell
python .\graphrag_api.py
```

启动后默认监听：

- `http://127.0.0.1:8000`

## 配置说明

### 必要配置

后端运行至少依赖以下配置：

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `OPENAI_API_KEY`

### 当前代码中的读取方式

有一个实现细节需要注意：

- `graphrag_api.py` 启动时直接从进程环境变量读取 Neo4j 配置
- `supplychain_agent.py` 会读取 `.env` 中的 LLM 配置

因此最稳妥的做法是：

- Neo4j 配置通过 PowerShell `env` 设置
- LLM 配置写入 `.env`

## 数据初始化与更新

### 初始建图

使用结构化 CSV 初始化知识图谱：

```powershell
$env:CSV_FILE="数据/Supply_Chain_Data_Fake.csv"
python .\build_graph.py
```

### 命令行增量更新

使用 `incremental_update.py` 对图谱进行增量 Upsert：

```powershell
python .\incremental_update.py `
  --csv .\test\test_increment.csv `
  --neo4j-uri neo4j://127.0.0.1:7687 `
  --neo4j-user neo4j `
  --neo4j-password 88888888 `
  --batch-size 2000
```

### CSV 导入建议

建议优先复用项目已有示例数据表头：

- [数据/Supply_Chain_Data_Fake.csv](/e:/Projects/长江存储/SupplyChainGraph/数据/Supply_Chain_Data_Fake.csv)

CSV 字段规范可参考：

- [CSV_IMPORT_SPEC.md](/e:/Projects/长江存储/SupplyChainGraph/CSV_IMPORT_SPEC.md)

## 后端对外接口

下面列出的都是后端服务直接对外提供的能力接口，不依赖前端页面也可独立调用。

### 1. 智能问答分析接口

路径：

`POST /api/chat`

作用：

- 统一接收自然语言问题
- 自动选择固定分析工具或动态 Cypher 查询
- 返回最终分析结果和最后一次查询轨迹

请求示例：

```json
{
  "message": "请分析长江存储的断供风险",
  "conversation_id": "demo-001"
}
```

响应示例：

```json
{
  "reply": "......",
  "conversation_id": "demo-001",
  "trace": {
    "tool": "supplier_ripple_effect",
    "type": "cypher",
    "cypher": "MATCH ..."
  }
}
```

适合承载的业务问题示例：

- 某供应商断供会影响多少订单和利润
- 某订单的上游根因供应商是谁
- 当前最关键的瓶颈供应商有哪些
- 哪些承运商延迟风险最高

调用示例：

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/chat" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"message":"请分析长江存储的断供风险","conversation_id":"demo-001"}'
```

### 2. 流式智能问答接口

路径：

`POST /api/chat/stream`

作用：

- 以 SSE 方式返回分析过程
- 适合接入聊天系统、控制台、前端实时输出

请求体：

```json
{
  "message": "找出最关键的瓶颈供应商",
  "conversation_id": "demo-001"
}
```

事件类型：

- `status`
- `delta`
- `done`
- `error`

说明：

- 本质上仍然是后端分析接口
- 只是返回格式从普通 JSON 变成了流式事件

### 3. 图谱查询接口

路径：

`POST /api/graph_view`

作用：

- 返回图谱可视化所需的节点和边
- 可作为子图查询接口使用
- 支持局部聚焦查询和全图裁剪查询

请求示例：

```json
{
  "question": "长江存储相关订单和产品",
  "mode": "focus",
  "max_nodes": 80,
  "max_edges": 160
}
```

字段说明：

- `question`：查询描述，用于抽取关键词并定位局部图
- `mode`：`focus` 或 `full`
- `max_nodes`：最大节点数
- `max_edges`：最大边数

响应示例：

```json
{
  "ok": true,
  "graph": {
    "nodes": [],
    "edges": [],
    "meta": {
      "node_count": 0,
      "edge_count": 0,
      "mode": "focus"
    }
  }
}
```

调用示例：

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/graph_view" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"question":"长江存储相关订单和产品","mode":"focus","max_nodes":80,"max_edges":160}'
```

### 4. CSV 导入接口

路径：

`POST /api/ingest/csv`

作用：

- 接收结构化 CSV
- 执行批量增量写入
- 适合 ERP、WMS、订单明细、供应商数据导入

表单参数：

- `file`
- `batch_size`
- `update_mode`

实际行为说明：

- 当前接口内部固定采用 `safe` 更新策略
- 更偏向补充缺失字段，不会强制覆盖已有值

调用示例：

```powershell
$form = @{
  file = Get-Item ".\test\test_increment.csv"
  batch_size = 2000
  update_mode = "safe"
}
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/ingest/csv" `
  -Method Post `
  -Form $form
```

响应示例：

```json
{
  "ok": true,
  "strategy": {
    "source_type": "structured_csv",
    "update_mode": "safe"
  },
  "rows": 100,
  "batches": 1
}
```

### 5. PDF 导入接口

路径：

`POST /api/ingest/pdf`

作用：

- 读取 PDF 文本
- 通过模板抽取或 LLM 抽取生成结构化记录
- 将补充信息写入图谱

表单参数：

- `file`
- `mode`
- `batch_size`
- `update_mode`

实际行为说明：

- 当前接口内部固定采用 `hybrid + safe`
- 需要安装 `pypdf`
- 使用 LLM 时需要配置 `OPENAI_API_KEY`

响应示例：

```json
{
  "ok": true,
  "strategy": {
    "source_type": "document_supplement",
    "extract_mode": "hybrid",
    "update_mode": "safe"
  },
  "rows": 20,
  "batches": 1
}
```

### 6. 文本导入接口

路径：

`POST /api/ingest/text`

作用：

- 接收自然语言描述
- 从文本中提取实体、关系或更新意图
- 将补充信息写入图谱

请求示例：

```json
{
  "text": "订单ORD-2024-100001由长江存储供应核心存储组件，物流承运商为顺丰速运。",
  "mode": "hybrid",
  "batch_size": 2000,
  "update_mode": "safe"
}
```

响应示例：

```json
{
  "ok": true,
  "strategy": {
    "source_type": "text_supplement",
    "extract_mode": "hybrid",
    "update_mode": "safe"
  },
  "rows": 3,
  "batches": 1
}
```

## 当前支持的分析能力

这些能力目前主要通过 `POST /api/chat` 和 `POST /api/chat/stream` 统一对外暴露：

- 供应商断供涟漪影响分析
- 供应商受影响订单查询
- 供应商受影响产品查询
- 多供应商风险对比
- 订单根因追踪
- 瓶颈供应商识别
- 延迟交付损失估算
- 供应商中断损失估算
- 高利润产品排行
- 高营收客户排行
- 高缺陷率供应商排行
- 承运商延迟表现分析
- 客户分群财务暴露分析
- 品类利润分析
- 供应商风险画像
- 订单状态汇总
- 高延误风险产品分析
- 高延误风险承运模式分析
- 区域营收分析
- 支付方式风险分析
- 发货模式履约表现分析
- 动态 Cypher 查询

## 演示页面

这部分不是核心接口说明，只是项目自带的演示入口：

- 管理页：`http://127.0.0.1:8000/admin`
- 问答页：直接打开 `index.html`

如果你是把项目作为后端服务交付，这两部分可以不作为主要介绍内容。

## 常见问题

### Neo4j 无法连接

请检查：

- Neo4j 是否已经启动
- Bolt 地址是否正确
- 用户名和密码是否正确

### 启动时报缺少 `OPENAI_API_KEY`

请确认项目根目录下的 `.env` 已正确写入：

```env
OPENAI_API_KEY=your_api_key
OPENAI_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

### PDF 导入时报 `pypdf not installed`

执行：

```powershell
pip install pypdf
```

### 文件上传时报表单解析错误

执行：

```powershell
pip install python-multipart
```

### 瓶颈分析时报 `gds.*` 错误

说明 Neo4j 没有安装或启用 GDS 插件。

## 发布到 GitHub 前的注意事项

- 不要提交 `.env`
- 不要提交真实 API Key
- 不要提交 `uploads/`
- 不要提交 `__pycache__/`

如果你当前本地 `.env` 已经放入真实密钥，建议先更换为占位值，并立即轮换真实密钥。
