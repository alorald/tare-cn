# FinFlow Global AaaS — 跨境财税自主智能体即服务

> 基于 GMI Cloud Inference Engine 的跨境财税智能体平台，提供票据多模态解析、多国财税合规决策、业财自动化调度三大 Agent 集群服务。

## 核心功能

- **票据多模态解析 Agent** — 调用 GMI Cloud VLM 完成 OCR、版面分析与结构化字段提取
- **多国财税合规决策 Agent** — 基于 GPT-OSS 进行多国税法匹配、税率计算与税务风险检测
- **业财自动化调度 Agent** — 负责任务分解、子任务分发与结果聚合，编排全链路工作流
- **GMI 引擎可视化监控面板** — 实时展示推理实例负载、Token 消耗、推理时延
- **标准化财税智能体调用 Open API** — RESTful API + WebSocket 实时进度推送

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python FastAPI + uvicorn + httpx |
| 前端 | 原生 HTML/CSS/JS SPA（暗色主题） |
| 推理引擎 | GMI Cloud Inference Engine（OpenAI 兼容协议） |
| 通信 | REST API + WebSocket 实时进度推送 |
| 模型编排 | Qwen3-Coder / DeepSeek-V3.2 / GPT-5 / GLM-5 多模型 fallback |

## 快速开始

### 环境要求

- Python 3.10+
- GMI Cloud API Key

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置 API Key

在 `gmi_client.py` 中替换 `API_KEY` 为你的 GMI Cloud API Key：

```python
API_KEY = "your-gmi-api-key-here"
BASE_URL = "https://api.gmi-serving.com/v1"
```

### 启动服务

```bash
python app.py
```

服务启动后访问 http://localhost:8080 即可打开控制台。

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/dashboard` | 仪表盘数据（任务统计、Agent 状态、Token 预算） |
| GET | `/api/gmi/models` | 获取 GMI 可用模型列表 |
| GET | `/api/gmi/status` | GMI 推理实例状态与 Token 消耗 |
| POST | `/api/tasks` | 创建财税处理任务 |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{task_id}` | 查询任务状态 |
| POST | `/api/receipts/parse` | 直接上传票据图片进行 OCR 解析 |
| GET | `/api/reports/{task_id}` | 获取生成的财税报表 |
| GET | `/api/docs` | API 文档信息 |
| WS | `/ws?task_id={task_id}` | WebSocket 实时推送任务进度 |

## Agent 工作流

```
任务创建 → 任务规划(Qwen3-Coder) → 票据解析(DeepSeek-V3.2)
    → 合规决策(GPT-5) → 合规建议(GLM-5) → 报表生成(Qwen3-Coder)
```

每个步骤均通过 WebSocket 实时推送进度，支持模型 429 限流时自动 fallback 到 DeepSeek-V3.2。

## GMI 模型分配

| 业务场景 | 主模型 | Fallback 模型 |
|----------|--------|---------------|
| 票据多模态识别 | openai/gpt-4o | deepseek-ai/DeepSeek-V3.2 |
| 票据文本校验 | deepseek-ai/DeepSeek-V3.2 | — |
| 合规决策推理 | openai/gpt-5 | deepseek-ai/DeepSeek-V3.2 |
| 多语种合规建议 | zai-org/GLM-5-FP8 | deepseek-ai/DeepSeek-V3.2 |
| 任务规划/报表 | Qwen/Qwen3-Coder-480B | deepseek-ai/DeepSeek-V3.2 |

## 项目结构

```
finflow-aaas-app/
├── app.py                 # FastAPI 主应用（REST API + WebSocket）
├── gmi_client.py          # GMI Cloud API 适配层（多模型编排 + fallback）
├── agents.py              # 三大 Agent 编排逻辑
├── requirements.txt       # Python 依赖
├── PRODUCT_DIRECTION.md   # 产品方向文档
└── static/                # 前端 SPA
    ├── index.html         # 入口页面
    ├── style.css          # 暗色主题样式
    └── app.js             # SPA 路由 + API 封装 + WebSocket 进度
```

## License

MIT
