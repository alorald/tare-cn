# FinFlow Global AaaS — 产品方向与技术路线

## 产品定位

FinFlow Global AaaS 是面向全球跨境小微外贸商家的财税智能体云服务。产品以 AaaS（Agent as a Service）模式交付，商户无需安装软件、无需按席位付费，只需通过 Web 控制台或 API 触发任务，智能体即在 GMI Cloud Inference Engine 上自主完成从票据解析到财税报表输出的全链路工作。

## 核心产品形态

### 交付物

1. **AaaS Web 管理控制台** — 交互式仪表盘，展示任务统计、Agent 集群状态、Token 消耗预算
2. **票据多模态解析 Agent** — 上传票据图片，调用 GMI Cloud VLM 完成 OCR + 字段提取
3. **多国财税合规决策 Agent** — 基于提取数据调用 GMI LLM 完成税法匹配与风险检测
4. **业财自动化调度 Agent** — 任务分解、分发、结果聚合，编排全链路工作流
5. **GMI 引擎监控面板** — 实时展示推理实例状态、Token 消耗、GPU 利用率
6. **标准化 AaaS 开放 API** — RESTful 接口文档，支持第三方集成

### 技术架构

```
用户浏览器 (前端 SPA)
    ↓ HTTP / WebSocket
FastAPI 后端服务
    ├── REST API 层 — 任务创建、票据上传、状态查询
    ├── Agent 编排层 — 任务分解、分发、聚合
    ├── GMI Cloud 适配层 — OpenAI 兼容 API 调用
    └── WebSocket 推送层 — 实时状态更新
        ↓
GMI Cloud Inference Engine
    ├── 多模态推理实例 — 票据图像识别
    ├── DeepSeek V3.1 — 多语种财税文本推理
    ├── GPT OSS 120B — 合规决策推理
    ├── Qwen3 Coder — 任务规划
    └── GLM-4.5 — 对话咨询
```

## GMI Cloud 使用方案

### API 接入

- **Base URL**: `https://inference.gmicloud.ai/v1`
- **协议**: OpenAI 兼容（`/chat/completions`）
- **认证**: Bearer Token
- **团队额度**: $50 赛事 Token

### 模型分配

| 模型 | 用途 | 调用场景 |
|------|------|----------|
| GMI Cloud VLM | 票据图像识别 | OCR + 版面分析 + 字段提取 |
| DeepSeek V3.1 | 多语种文本推理 | 金额/币种/税率字段校验 |
| GPT OSS 120B | 合规决策 | 税法匹配 + 风险检测 |
| Qwen3 Coder | 任务规划 | 自然语言指令分解 |
| GLM-4.5 | 对话咨询 | 多语种合规建议 |

### Token 预算

- 多模态推理: ~30%（高频调用）
- DeepSeek V3.1: ~25%（文本校验）
- GPT OSS 120B: ~25%（合规推理）
- Qwen3 Coder: ~12%（任务规划）
- GLM-4.5: ~8%（对话咨询）

## 产品功能清单

### P0 — 核心功能（本次交付）

- [x] 仪表盘总览（任务统计、Agent 状态、Token 预算）
- [x] 票据上传与多模态解析（真实调用 GMI VLM）
- [x] 财税合规决策（真实调用 GMI LLM）
- [x] Agent 工作流编排与实时进度
- [x] GMI 引擎监控面板（实例状态、Token 消耗）
- [x] 开放 API 文档页面

### P1 — 增强功能（后续迭代）

- [ ] 多票据批量上传
- [ ] 历史任务记录与报表导出
- [ ] 多租户管理
- [ ] Webhook 事件推送
- [ ] 多语言界面切换

## 开发路线

### 阶段一：可交互原型（当前）

构建 FastAPI + 前端 SPA 的完整应用，真实调用 GMI Cloud API，实现票据上传→解析→合规决策→报表输出的完整链路。

### 阶段二：产品化

- 用户认证与多租户
- 持久化存储（PostgreSQL）
- 任务队列（Celery + Redis）
- 容器化部署（Docker）

### 阶段三：商业化

- 开放 API 注册与密钥管理
- 按任务计费系统
- 多区域部署
- 合作伙伴集成

## 技术选型

| 层级 | 技术 | 理由 |
|------|------|------|
| 后端 | Python FastAPI | 异步高性能、自动生成 OpenAPI 文档 |
| 前端 | 原生 HTML + JS + CSS | 无构建步骤、即开即用 |
| 推理引擎 | GMI Cloud Inference Engine | 赛事指定、OpenAI 兼容 |
| 实时通信 | WebSocket | 任务进度实时推送 |

## 运行方式

```bash
cd finflow-aaas-app
pip install -r requirements.txt
python app.py
# 访问 http://localhost:8000
```
