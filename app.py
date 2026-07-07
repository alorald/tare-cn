"""
app.py — FinFlow AaaS FastAPI 主应用

跨境财税智能体服务，真实调用 GMI Cloud API。

启动：
    uvicorn app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Set

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gmi_client import gmi_client, PRICING
from agents import orchestrator

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("finflow")

# ---------------------------------------------------------------------------
# 内存存储
# ---------------------------------------------------------------------------

# task_id -> 任务详情
tasks_db: Dict[str, Dict[str, Any]] = {}

# task_id -> 财税报表
reports_db: Dict[str, Dict[str, Any]] = {}

# task_id -> 进度事件列表
progress_db: Dict[str, List[Dict[str, Any]]] = {}

# task_id -> 订阅该任务进度的 WebSocket 集合
ws_subscribers: Dict[str, Set[WebSocket]] = {}

# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class TaskCreateRequest(BaseModel):
    task_type: str = Field(..., description="任务类型，如 receipt_parse / compliance_check / full_workflow")
    platform: str = Field(default="amazon", description="交易平台，如 amazon/ebay/shopify")
    target_country: str = Field(default="US", description="目标国家/地区")
    receipt_text: Optional[str] = Field(default=None, description="票据文本（无图片时使用）")
    receipt_image_base64: Optional[str] = Field(default=None, description="票据图片 base64")


class ReceiptParseRequest(BaseModel):
    image_base64: str = Field(..., description="票据图片 base64 字符串")
    platform: str = Field(default="amazon", description="交易平台")
    target_country: str = Field(default="US", description="目标国家")


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FinFlow AaaS 服务启动")
    yield
    logger.info("FinFlow AaaS 服务关闭，释放 GMI 客户端")
    await gmi_client.close()


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="FinFlow AaaS API",
    description="跨境财税智能体服务 — 基于 GMI Cloud 多模型编排",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 允许所有源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（前端放在 static/ 目录）
app.mount("/static", StaticFiles(directory="static", html=True), name="static")


# ---------------------------------------------------------------------------
# WebSocket 进度推送
# ---------------------------------------------------------------------------

async def broadcast_progress(task_id: str, event: Dict[str, Any]) -> None:
    """向订阅了某任务的 WebSocket 推送进度事件"""
    subs = ws_subscribers.get(task_id, set())
    if not subs:
        return
    message = json.dumps(event, ensure_ascii=False, default=str)
    dead: List[WebSocket] = []
    for ws in list(subs):
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        subs.discard(ws)


async def make_progress_callback(task_id: str):
    """创建绑定到 task_id 的进度回调"""

    async def _cb(step: str, status: str, message: str, data: Optional[Dict[str, Any]] = None):
        event = {
            "task_id": task_id,
            "step": step,
            "status": status,
            "message": message,
            "data": data,
            "timestamp": time.time(),
        }
        progress_db.setdefault(task_id, []).append(event)
        logger.info("[task=%s] %s/%s — %s", task_id, step, status, message)
        await broadcast_progress(task_id, event)

    return _cb


# ---------------------------------------------------------------------------
# 异步任务执行
# ---------------------------------------------------------------------------

async def execute_task(task_id: str, request: TaskCreateRequest) -> None:
    """异步执行业财自动化任务

    流程：
      1. 调度 Agent 分解任务
      2. 票据解析 Agent 解析票据
      3. 合规决策 Agent 进行税法匹配和风险检测
      4. 聚合结果生成报表
      5. 每个步骤通过 WebSocket 推送进度
    """
    task = tasks_db[task_id]
    task["status"] = "running"
    task["started_at"] = time.time()
    progress_cb = await make_progress_callback(task_id)

    try:
        await progress_cb("task_start", "running", f"任务开始：{request.task_type} / {request.platform} / {request.target_country}", None)

        # 委托给 OrchestratorAgent 完整编排（内部已含规划/解析/合规/报表四步）
        result = await orchestrator.execute_task(
            task_type=request.task_type,
            platform=request.platform,
            target_country=request.target_country,
            receipt_text=request.receipt_text,
            receipt_image_base64=request.receipt_image_base64,
            progress=progress_cb,
        )

        task["result"] = result
        task["status"] = "completed" if not result.get("errors") else "completed_with_errors"
        task["completed_at"] = time.time()

        # 保存报表
        if result.get("report"):
            reports_db[task_id] = {
                "task_id": task_id,
                "report": result["report"],
                "generated_at": time.time(),
            }

        await progress_cb(
            "task_complete",
            "done",
            "任务执行完成",
            {
                "status": task["status"],
                "has_report": bool(result.get("report")),
                "errors": result.get("errors", []),
            },
        )

    except Exception as exc:
        logger.exception("任务执行异常 task=%s", task_id)
        task["status"] = "failed"
        task["error"] = str(exc)
        task["completed_at"] = time.time()
        await progress_cb("task_error", "error", f"任务执行失败: {exc}", {"error": str(exc)})


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/api")
@app.get("/api/docs")
async def api_docs():
    """API 文档信息（接口列表）"""
    return {
        "name": "FinFlow AaaS API",
        "version": "1.0.0",
        "description": "跨境财税智能体服务 — 基于 GMI Cloud 多模型编排",
        "endpoints": [
            {"method": "GET", "path": "/api/dashboard", "desc": "仪表盘数据（任务统计、Agent 状态、Token 预算消耗）"},
            {"method": "GET", "path": "/api/gmi/models", "desc": "获取 GMI 可用模型列表"},
            {"method": "GET", "path": "/api/gmi/status", "desc": "GMI 推理实例状态与 Token 消耗"},
            {"method": "POST", "path": "/api/tasks", "desc": "创建财税处理任务"},
            {"method": "GET", "path": "/api/tasks", "desc": "任务列表"},
            {"method": "GET", "path": "/api/tasks/{task_id}", "desc": "查询任务状态"},
            {"method": "POST", "path": "/api/receipts/parse", "desc": "直接上传票据图片进行 OCR 解析"},
            {"method": "GET", "path": "/api/reports/{task_id}", "desc": "获取生成的财税报表"},
            {"method": "GET", "path": "/api/docs", "desc": "API 文档信息（本接口）"},
            {"method": "WS", "path": "/ws", "desc": "WebSocket 实时推送任务进度（订阅 task_id）"},
        ],
        "models": list(PRICING.keys()),
    }


@app.get("/api/dashboard")
async def dashboard():
    """仪表盘数据：任务统计、Agent 状态、Token 预算消耗"""
    total = len(tasks_db)
    status_counts: Dict[str, int] = {}
    for t in tasks_db.values():
        s = t.get("status", "pending")
        status_counts[s] = status_counts.get(s, 0) + 1

    gmi_status = gmi_client.get_status()

    return {
        "task_stats": {
            "total": total,
            "by_status": status_counts,
        },
        "gmi": {
            "instances": gmi_status["instances"],
            "token_budget": gmi_status["token_budget"],
            "usage": gmi_status["usage"],
        },
        "agents": [
            {"name": "ReceiptParserAgent", "model": "openai/gpt-4o + deepseek-ai/DeepSeek-V3.2", "status": "ready"},
            {"name": "ComplianceAgent", "model": "openai/gpt-5 + zai-org/GLM-5-FP8", "status": "ready"},
            {"name": "OrchestratorAgent", "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8", "status": "ready"},
        ],
        "recent_tasks": [
            {
                "task_id": tid,
                "task_type": t.get("request", {}).get("task_type"),
                "status": t.get("status"),
                "created_at": t.get("created_at"),
            }
            for tid, t in list(tasks_db.items())[-10:]
        ],
        "timestamp": time.time(),
    }


@app.get("/api/gmi/models")
async def gmi_models():
    """获取 GMI 可用模型列表"""
    models_data = await gmi_client.list_models()
    # 附加价格信息
    enriched: List[Dict[str, Any]] = []
    data = models_data.get("data", []) if isinstance(models_data, dict) else []
    for m in data:
        mid = m.get("id", "") if isinstance(m, dict) else str(m)
        price = PRICING.get(mid, {})
        enriched.append({
            "id": mid,
            "object": m.get("object", "model") if isinstance(m, dict) else "model",
            "owned_by": m.get("owned_by", "gmi") if isinstance(m, dict) else "gmi",
            "pricing": price,
        })
    return {
        "object": "list",
        "data": enriched,
        "source": models_data.get("source", "gmi_api") if isinstance(models_data, dict) else "gmi_api",
    }


@app.get("/api/gmi/status")
async def gmi_status():
    """GMI 推理实例状态（5个实例的运行状态、Token 消耗）"""
    return gmi_client.get_status()


@app.post("/api/tasks")
async def create_task(request: TaskCreateRequest):
    """创建财税处理任务

    接受 task_type, platform, target_country, receipt_text 或 receipt_image_base64。
    创建后异步执行，通过 WebSocket 推送进度。
    """
    # 至少要有一种输入
    if not request.receipt_text and not request.receipt_image_base64:
        # 允许无票据的任务（仅做合规咨询），但记录提示
        pass

    task_id = f"task-{uuid.uuid4().hex[:12]}"
    now = time.time()
    tasks_db[task_id] = {
        "task_id": task_id,
        "request": request.model_dump(),
        "status": "pending",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "result": None,
        "error": None,
    }
    progress_db[task_id] = []

    # 异步执行任务（不阻塞响应）
    asyncio.create_task(execute_task(task_id, request))

    return {
        "task_id": task_id,
        "status": "pending",
        "message": "任务已创建，正在异步执行。请通过 WebSocket /ws 订阅进度，或轮询 GET /api/tasks/{task_id}。",
        "ws_url": f"/ws?task_id={task_id}",
        "created_at": now,
    }


@app.get("/api/tasks")
async def list_tasks(status: Optional[str] = None, limit: int = 50):
    """任务列表"""
    items = list(tasks_db.values())
    if status:
        items = [t for t in items if t.get("status") == status]
    items = sorted(items, key=lambda t: t.get("created_at", 0), reverse=True)[:limit]
    return {
        "total": len(items),
        "tasks": [
            {
                "task_id": t["task_id"],
                "task_type": t["request"].get("task_type"),
                "platform": t["request"].get("platform"),
                "target_country": t["request"].get("target_country"),
                "status": t.get("status"),
                "created_at": t.get("created_at"),
                "completed_at": t.get("completed_at"),
            }
            for t in items
        ],
    }


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """查询任务状态"""
    task = tasks_db.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return {
        "task_id": task_id,
        "request": task.get("request"),
        "status": task.get("status"),
        "created_at": task.get("created_at"),
        "started_at": task.get("started_at"),
        "completed_at": task.get("completed_at"),
        "result": task.get("result"),
        "error": task.get("error"),
        "progress": progress_db.get(task_id, []),
    }


@app.post("/api/receipts/parse")
async def parse_receipt(request: ReceiptParseRequest):
    """直接上传票据图片进行 OCR 解析

    同步调用 ReceiptParserAgent，返回解析结果。
    """
    from agents import ReceiptParserAgent

    agent = ReceiptParserAgent()
    try:
        result = await agent.parse_image(image_base64=request.image_base64)
        return {
            "status": "success",
            "parsed_data": result.get("parsed_data"),
            "verification": result.get("verification"),
            "final_data": result.get("final_data"),
            "steps": result.get("steps"),
            "errors": result.get("errors"),
        }
    except Exception as exc:
        logger.exception("票据解析失败")
        raise HTTPException(status_code=500, detail=f"票据解析失败: {exc}")


@app.get("/api/reports/{task_id}")
async def get_report(task_id: str):
    """获取生成的财税报表"""
    if task_id not in tasks_db:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    report = reports_db.get(task_id)
    if report is None:
        # 任务可能尚未完成，返回任务状态
        task = tasks_db[task_id]
        raise HTTPException(
            status_code=404,
            detail=f"报表尚未生成，当前任务状态: {task.get('status')}",
        )
    return report


# ---------------------------------------------------------------------------
# 根路由 -> 静态前端
# ---------------------------------------------------------------------------

from fastapi.responses import FileResponse

@app.get("/")
async def root():
    """根路由，返回前端控制台"""
    return FileResponse("static/index.html", media_type="text/html")


# ---------------------------------------------------------------------------
# WebSocket /ws
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 实时推送任务进度

    连接时可带 ?task_id=xxx 订阅指定任务；
    也可连接后发送 {"action": "subscribe", "task_id": "xxx"} 动态订阅。
    """
    await websocket.accept()

    # 从查询参数订阅
    query_params = websocket.query_params
    task_id = query_params.get("task_id")

    subscribed: Set[str] = set()
    if task_id:
        ws_subscribers.setdefault(task_id, set()).add(websocket)
        subscribed.add(task_id)
        # 发送已有进度
        for evt in progress_db.get(task_id, []):
            await websocket.send_text(json.dumps(evt, ensure_ascii=False, default=str))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "invalid json"}))
                continue

            action = msg.get("action")
            tid = msg.get("task_id")

            if action == "subscribe" and tid:
                ws_subscribers.setdefault(tid, set()).add(websocket)
                subscribed.add(tid)
                # 推送历史进度
                for evt in progress_db.get(tid, []):
                    await websocket.send_text(json.dumps(evt, ensure_ascii=False, default=str))
                await websocket.send_text(json.dumps({
                    "type": "subscribed",
                    "task_id": tid,
                    "history_count": len(progress_db.get(tid, [])),
                }))

            elif action == "unsubscribe" and tid:
                ws_subscribers.get(tid, set()).discard(websocket)
                subscribed.discard(tid)
                await websocket.send_text(json.dumps({"type": "unsubscribed", "task_id": tid}))

            elif action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            else:
                await websocket.send_text(json.dumps({"error": "unknown action"}))

    except WebSocketDisconnect:
        logger.info("WebSocket 断开")
    finally:
        for tid in subscribed:
            ws_subscribers.get(tid, set()).discard(websocket)


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info",
    )
