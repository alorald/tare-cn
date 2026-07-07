"""
gmi_client.py — GMI Cloud API 适配层

负责与 GMI Cloud（OpenAI 兼容协议）进行异步通信，提供：
  - 模型列表查询
  - 文本推理（chat）
  - 多模态视觉识别（vision，gpt-4o 支持图片）
  - 业务场景 Agent 路由（call_agent）
  - Token 用量与费用统计
  - 5 个推理实例状态模拟
  - 429 过载重试逻辑
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("gmi_client")

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------

API_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpZCI6IjhhYmNmZGRjLTI0NzgtNGEyYy1hNWIxLWVjOTg1OWNhNzhkOCIsInNjb3BlIjoiaWVfbW9kZWwiLCJwcm9kdWN0IjoiIiwib3duZXJJZCI6ImMzMzVhNmUzLWRlYzYtNDdlOC1iOGEwLWQxOTJjZWVmMjhmZCJ9."
    "yR4_h4TeftkALeRd3ZrQKkvIgC-lq5TFk4k2eBj6jro"
)

BASE_URL = "https://api.gmi-serving.com/v1"

# 请求超时（秒）。推理模型可能较慢，给足时间。
REQUEST_TIMEOUT = 120.0

# 429 过载重试等待时间（秒）
RETRY_WAIT_SECONDS = 2.0

# 最大重试次数
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# 价格表（每 1M tokens，单位：美元）
# ---------------------------------------------------------------------------

PRICING: Dict[str, Dict[str, float]] = {
    "openai/gpt-4o": {"input": 2.5, "output": 10.0},
    "openai/gpt-5": {"input": 5.0, "output": 15.0},
    "deepseek-ai/DeepSeek-V4-Pro": {"input": 0.55, "output": 1.65},
    "deepseek-ai/DeepSeek-V3.2": {"input": 0.27, "output": 1.10},
    "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8": {"input": 1.2, "output": 3.0},
    "zai-org/GLM-5-FP8": {"input": 0.5, "output": 1.5},
    "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8": {"input": 1.2, "output": 3.0},
}

# 推理模型默认 max_tokens（V4-Pro 这类模型需要至少 2000，否则只输出 reasoning）
REASONING_MODEL_DEFAULT_MAX_TOKENS = 4096

# 被视为推理模型（需要更大 max_tokens）的模型集合
REASONING_MODELS = {
    "deepseek-ai/DeepSeek-V4-Pro",
    "openai/gpt-5",
}

# ---------------------------------------------------------------------------
# 业务场景 -> 模型映射
# ---------------------------------------------------------------------------

AGENT_MODEL_MAP: Dict[str, str] = {
    # 票据解析
    "receipt_vision": "openai/gpt-4o",                       # 多模态识别
    "receipt_verify": "deepseek-ai/DeepSeek-V3.2",            # 文本校验
    # 合规决策
    "compliance_decision": "openai/gpt-5",                    # 合规决策推理
    "compliance_advice": "zai-org/GLM-5-FP8",                 # 多语种合规建议
    # 业财调度
    "orchestration": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",  # 任务规划
    # 对话咨询
    "consultation": "zai-org/GLM-5-FP8",
    # 可替代模型
    "general_text": "Qwen/Qwen3-235B-A22B-Instruct-2507-FP8",
    "deep_reasoning": "deepseek-ai/DeepSeek-V4-Pro",
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    """单次调用的 Token 用量记录"""

    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    timestamp: float = field(default_factory=time.time)
    agent_type: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": round(self.cost, 6),
            "timestamp": self.timestamp,
            "agent_type": self.agent_type,
        }


@dataclass
class InstanceState:
    """推理实例状态模拟"""

    id: int
    name: str
    model: str
    status: str = "idle"  # idle / running
    current_task: str = ""
    tokens_consumed: int = 0
    last_active: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "status": self.status,
            "current_task": self.current_task,
            "tokens_consumed": self.tokens_consumed,
            "last_active": self.last_active,
        }


# ---------------------------------------------------------------------------
# GMI 客户端
# ---------------------------------------------------------------------------

class GMIClient:
    """GMI Cloud API 异步客户端

    封装 OpenAI 兼容协议的 /chat/completions 与 /models 接口，
    并提供 Token 用量统计、费用扣减、推理实例状态模拟、429 重试。
    """

    def __init__(self, initial_budget: float = 50.0) -> None:
        self.api_key = API_KEY
        self.base_url = BASE_URL
        self.initial_budget = initial_budget
        self.token_budget = initial_budget
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
        self.usage_log: List[TokenUsage] = []

        # 5 个推理实例模拟，分别绑定到不同模型
        self.instances: List[InstanceState] = [
            InstanceState(id=1, name="vision-node-01", model="openai/gpt-4o"),
            InstanceState(id=2, name="reasoning-node-02", model="openai/gpt-5"),
            InstanceState(id=3, name="verify-node-03", model="deepseek-ai/DeepSeek-V3.2"),
            InstanceState(id=4, name="planner-node-04", model="Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8"),
            InstanceState(id=5, name="dialogue-node-05", model="zai-org/GLM-5-FP8"),
        ]

        # httpx 客户端在首次使用时懒加载创建，便于事件循环绑定
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # 内部：httpx 客户端管理
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=15.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # 内部：实例状态管理
    # ------------------------------------------------------------------

    def _find_instance_by_model(self, model: str) -> Optional[InstanceState]:
        for inst in self.instances:
            if inst.model == model:
                return inst
        return None

    def _acquire_instance(self, model: str, task_label: str) -> Optional[InstanceState]:
        inst = self._find_instance_by_model(model)
        if inst is None:
            return None
        inst.status = "running"
        inst.current_task = task_label
        return inst

    def _release_instance(self, model: str, tokens: int) -> None:
        inst = self._find_instance_by_model(model)
        if inst is None:
            return
        inst.status = "idle"
        inst.current_task = ""
        inst.tokens_consumed += tokens
        inst.last_active = time.time()

    # ------------------------------------------------------------------
    # 内部：用量与费用统计
    # ------------------------------------------------------------------

    def _estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = PRICING.get(model, {"input": 1.0, "output": 2.0})
        cost = (input_tokens / 1_000_000.0) * price["input"] + (
            output_tokens / 1_000_000.0
        ) * price["output"]
        return cost

    def _record_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        agent_type: str = "",
    ) -> TokenUsage:
        cost = self._estimate_cost(model, input_tokens, output_tokens)
        usage = TokenUsage(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            agent_type=agent_type,
        )
        self.usage_log.append(usage)
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost
        self.token_budget = max(0.0, self.token_budget - cost)
        self.call_count += 1
        logger.info(
            "GMI 调用统计 model=%s in=%d out=%d cost=$%.6f 预算余=$%.4f",
            model,
            input_tokens,
            output_tokens,
            cost,
            self.token_budget,
        )
        return usage

    # ------------------------------------------------------------------
    # 核心：带重试的请求
    # ------------------------------------------------------------------

    async def _request_with_retry(
        self,
        payload: Dict[str, Any],
        task_label: str = "",
    ) -> Dict[str, Any]:
        """发送 chat/completions 请求，遇 429 等待重试"""
        client = await self._get_client()
        model = payload.get("model", "")
        last_error: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await client.post("/chat/completions", json=payload)

                if response.status_code == 429:
                    logger.warning(
                        "GMI 返回 429 过载（model=%s, attempt=%d/%d），等待 %.1fs 重试",
                        model,
                        attempt,
                        MAX_RETRIES,
                        RETRY_WAIT_SECONDS,
                    )
                    last_error = RuntimeError(
                        f"GMI 服务过载（429 Too Many Requests），model={model}"
                    )
                    await asyncio.sleep(RETRY_WAIT_SECONDS)
                    continue

                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                if status == 429:
                    await asyncio.sleep(RETRY_WAIT_SECONDS)
                    continue
                if status >= 500:
                    logger.warning(
                        "GMI 服务端错误 status=%d（attempt=%d/%d），重试",
                        status,
                        attempt,
                        MAX_RETRIES,
                    )
                    await asyncio.sleep(RETRY_WAIT_SECONDS)
                    continue
                # 4xx（非 429）直接抛出
                raise
            except httpx.RequestError as exc:
                last_error = exc
                logger.warning(
                    "GMI 网络错误：%s（attempt=%d/%d），重试",
                    exc,
                    attempt,
                    MAX_RETRIES,
                )
                await asyncio.sleep(RETRY_WAIT_SECONDS)
                continue

        raise RuntimeError(
            f"GMI 请求失败，已达最大重试次数 {MAX_RETRIES}（task={task_label}）: {last_error}"
        )

    # ------------------------------------------------------------------
    # 公开 API：list_models
    # ------------------------------------------------------------------

    async def list_models(self) -> Dict[str, Any]:
        """获取 GMI 可用模型列表"""
        client = await self._get_client()
        try:
            response = await client.get("/models")
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning("获取模型列表失败，返回本地配置: %s", exc)
            return {
                "object": "list",
                "data": [
                    {"id": m, "object": "model", "owned_by": "gmi"}
                    for m in PRICING.keys()
                ],
                "source": "local_fallback",
            }

    # ------------------------------------------------------------------
    # 公开 API：chat（文本推理）
    # ------------------------------------------------------------------

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        agent_type: str = "",
    ) -> Dict[str, Any]:
        """文本推理接口

        对推理模型（DeepSeek-V4-Pro / gpt-5）自动放大 max_tokens，
        避免只输出 reasoning 而无 content。
        """
        if max_tokens is None:
            if model in REASONING_MODELS:
                max_tokens = REASONING_MODEL_DEFAULT_MAX_TOKENS
            else:
                max_tokens = 2048

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        task_label = agent_type or "chat"
        inst = self._acquire_instance(model, task_label)
        try:
            result = await self._request_with_retry(payload, task_label)
        finally:
            if inst is not None:
                self._release_instance(model, 0)

        # 解析用量
        usage = result.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        if input_tokens == 0 and output_tokens == 0:
            # 兜底估算
            input_tokens = sum(
                len(str(m.get("content", ""))) // 4 for m in messages
            )
            content_text = ""
            choices = result.get("choices", [])
            if choices:
                content_text = choices[0].get("message", {}).get("content", "") or ""
            output_tokens = max(1, len(content_text) // 4)

        usage_record = self._record_usage(model, input_tokens, output_tokens, agent_type)
        if inst is not None:
            inst.tokens_consumed += input_tokens + output_tokens

        return {
            "model": model,
            "content": (
                result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if result.get("choices")
                else ""
            ),
            "raw": result,
            "usage": usage_record.to_dict(),
        }

    # ------------------------------------------------------------------
    # 公开 API：vision（多模态识别）
    # ------------------------------------------------------------------

    async def vision(
        self,
        model: str,
        image_base64: str,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.3,
        agent_type: str = "receipt_vision",
    ) -> Dict[str, Any]:
        """多模态视觉识别（gpt-4o 支持图片输入）

        image_base64 为纯 base64 字符串（不含 data: 前缀）。
        """
        # 规范化 base64：去掉可能的 data URL 前缀
        if image_base64.startswith("data:"):
            # 形如 data:image/jpeg;base64,xxxx
            image_base64 = image_base64.split(",", 1)[-1]

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        },
                    },
                ],
            }
        ]

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        inst = self._acquire_instance(model, agent_type)
        try:
            result = await self._request_with_retry(payload, agent_type)
        finally:
            if inst is not None:
                self._release_instance(model, 0)

        usage = result.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        if input_tokens == 0 and output_tokens == 0:
            input_tokens = max(1, len(prompt) // 4 + 1500)  # 图片 token 估算
            content_text = ""
            choices = result.get("choices", [])
            if choices:
                content_text = choices[0].get("message", {}).get("content", "") or ""
            output_tokens = max(1, len(content_text) // 4)

        usage_record = self._record_usage(model, input_tokens, output_tokens, agent_type)
        if inst is not None:
            inst.tokens_consumed += input_tokens + output_tokens

        return {
            "model": model,
            "content": (
                result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if result.get("choices")
                else ""
            ),
            "raw": result,
            "usage": usage_record.to_dict(),
        }

    # ------------------------------------------------------------------
    # 公开 API：call_agent（业务场景路由）
    # ------------------------------------------------------------------

    async def call_agent(
        self,
        agent_type: str,
        input_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """按业务场景调用不同模型

        input_data 支持字段：
          - messages: 直接传入消息列表
          - prompt: 单条用户提示（会包装成 messages）
          - image_base64: 图片 base64（仅 vision 场景）
          - max_tokens / temperature: 可选覆盖
        """
        model = AGENT_MODEL_MAP.get(agent_type)
        if model is None:
            raise ValueError(f"未知 agent_type: {agent_type}")

        max_tokens = input_data.get("max_tokens")
        temperature = input_data.get("temperature", 0.7)

        # 视觉场景
        if agent_type == "receipt_vision":
            image_b64 = input_data.get("image_base64", "")
            prompt = input_data.get("prompt", "请识别这张票据图片中的关键财税字段。")
            if not image_b64:
                raise ValueError("receipt_vision 场景需要 image_base64")
            return await self.vision(
                model=model,
                image_base64=image_b64,
                prompt=prompt,
                max_tokens=max_tokens or 2048,
                temperature=temperature,
                agent_type=agent_type,
            )

        # 文本场景
        messages = input_data.get("messages")
        if messages is None:
            prompt = input_data.get("prompt", "")
            system = input_data.get("system", "")
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

        try:
            return await self.chat(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                agent_type=agent_type,
            )
        except RuntimeError as e:
            # Fallback: 主模型 429 限流时，自动切换到 DeepSeek-V3.2
            fallback = "deepseek-ai/DeepSeek-V3.2"
            if model == fallback:
                raise  # 已经是 fallback，不再重试
            logger.warning(
                "主模型 %s 调用失败，切换 fallback 模型 %s（task=%s）",
                model, fallback, agent_type,
            )
            return await self.chat(
                model=fallback,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                agent_type=agent_type,
            )

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """返回 GMI 客户端整体状态（实例、预算、用量）"""
        return {
            "base_url": self.base_url,
            "token_budget": {
                "initial": round(self.initial_budget, 4),
                "remaining": round(self.token_budget, 4),
                "consumed": round(self.total_cost, 6),
                "consumed_percent": round(
                    (self.total_cost / self.initial_budget) * 100, 2
                ) if self.initial_budget else 0,
            },
            "usage": {
                "total_calls": self.call_count,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_cost": round(self.total_cost, 6),
            },
            "instances": [inst.to_dict() for inst in self.instances],
            "recent_usage": [u.to_dict() for u in self.usage_log[-20:]],
        }


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

gmi_client = GMIClient(initial_budget=50.0)
