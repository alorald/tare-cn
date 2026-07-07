"""
agents.py — FinFlow AaaS 三大 Agent 编排逻辑

  - ReceiptParserAgent   票据多模态解析（gpt-4o 识别 + DeepSeek-V3.2 校验）
  - ComplianceAgent      财税合规决策（gpt-5 风险检测 + GLM-5 多语种建议）
  - OrchestratorAgent    业财自动化调度（Qwen3-Coder 任务分解 + 编排）

每个 Agent 方法均为 async，接受输入返回结构化 dict。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional

from gmi_client import gmi_client

logger = logging.getLogger("agents")

# 进度回调类型： (step, status, message, data) -> Awaitable[None]
ProgressCallback = Callable[[str, str, str, Optional[Dict[str, Any]]], Awaitable[None]]


def _safe_json_parse(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中尽力解析出 JSON 对象"""
    if not text:
        return None
    # 1. 直接解析
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # 2. 提取 ```json ... ``` 代码块
    match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    # 3. 提取第一个 {...} 块
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


# ===========================================================================
# Agent 1: ReceiptParserAgent — 票据多模态解析
# ===========================================================================

class ReceiptParserAgent:
    """票据解析 Agent

    工作流：
      1. 调用 openai/gpt-4o 多模态识别票据图片，提取关键字段
      2. 调用 deepseek-ai/DeepSeek-V3.2 校验提取的文本字段
      3. 输出结构化 JSON
    """

    name = "ReceiptParserAgent"
    description = "票据多模态解析 Agent（gpt-4o + DeepSeek-V3.2）"

    VISION_PROMPT = """你是一名资深跨境财税领域的票据解析专家，精通亚马逊、eBay、Shopify 等跨境电商平台的结算单、发票、回执。

请仔细识别图片中的票据信息，提取以下字段并以严格的 JSON 格式返回（不要输出 JSON 以外的内容）：

{
  "merchant_name": "商户/平台名称",
  "settlement_period": "结算周期（如 2025-06-01 至 2025-06-30）",
  "currency": "币种（如 USD/EUR/GBP/JPY/CNY）",
  "total_sales": "总销售额（数字字符串）",
  "platform_fee": "平台手续费（数字字符串）",
  "fba_storage_fee": "FBA 仓储费（数字字符串，无则填 0）",
  "other_fees": "其他费用（数字字符串，无则填 0）",
  "net_amount": "净到账金额（数字字符串）",
  "tax_id": "税号/VAT 号/EIN 等",
  "transaction_date": "交易日期或结算日期",
  "payment_method": "支付方式",
  "country": "交易涉及的国家/地区",
  "notes": "其他备注信息"
}

若某字段无法识别，对应值填 null。请确保金额字段为纯数字字符串，不含货币符号。"""

    VERIFY_PROMPT_TEMPLATE = """你是一名严谨的财税数据校验专家。以下是从票据中提取的 JSON 数据，请逐字段校验：

1. 金额字段是否为有效数字
2. 币种代码是否为标准 ISO 4217
3. 净到账金额是否约等于 总销售额 - 平台手续费 - FBA仓储费 - 其他费用
4. 日期格式是否合理
5. 税号格式是否符合该国家规范

待校验数据：
{data}

请返回校验结果 JSON：
{{
  "valid": true/false,
  "issues": ["问题1", "问题2"],
  "corrected_data": {{ 修正后的完整数据，若无需修正则原样返回 }},
  "confidence": 0.0-1.0
}}
只返回 JSON，不要其他内容。"""

    async def parse_image(
        self,
        image_base64: str,
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """解析票据图片

        Args:
            image_base64: 票据图片的 base64 字符串
            progress: 进度回调

        Returns:
            结构化解析结果
        """
        if progress:
            await progress("receipt_vision", "running", "调用 gpt-4o 识别票据图片字段", None)

        result: Dict[str, Any] = {
            "agent": self.name,
            "steps": [],
            "parsed_data": None,
            "verification": None,
            "final_data": None,
            "errors": [],
        }

        # 步骤 1：gpt-4o 多模态识别
        try:
            vision_resp = await gmi_client.call_agent(
                agent_type="receipt_vision",
                input_data={
                    "image_base64": image_base64,
                    "prompt": self.VISION_PROMPT,
                    "temperature": 0.2,
                },
            )
            vision_content = vision_resp.get("content", "")
            result["steps"].append({
                "step": "vision_ocr",
                "model": "openai/gpt-4o",
                "status": "done",
                "usage": vision_resp.get("usage"),
            })

            parsed = _safe_json_parse(vision_content)
            if parsed is None:
                # 无法解析为 JSON，保留原始文本
                parsed = {
                    "raw_text": vision_content,
                    "_parse_warning": "模型未返回标准 JSON，已保留原始文本",
                }
            result["parsed_data"] = parsed
            result["raw_vision_text"] = vision_content

            if progress:
                await progress(
                    "receipt_vision",
                    "done",
                    "票据字段识别完成",
                    {"fields_found": list(parsed.keys()) if isinstance(parsed, dict) else []},
                )
        except Exception as exc:
            logger.exception("ReceiptParser vision 步骤失败")
            result["errors"].append(f"vision_ocr: {exc}")
            result["steps"].append({
                "step": "vision_ocr",
                "model": "openai/gpt-4o",
                "status": "error",
                "error": str(exc),
            })
            if progress:
                await progress("receipt_vision", "error", f"识别失败: {exc}", None)
            return result

        # 步骤 2：DeepSeek-V3.2 校验
        if progress:
            await progress(
                "receipt_verify",
                "running",
                "调用 DeepSeek-V3.2 校验提取字段",
                None,
            )

        try:
            verify_prompt = self.VERIFY_PROMPT_TEMPLATE.format(
                data=json.dumps(parsed, ensure_ascii=False, indent=2)
            )
            verify_resp = await gmi_client.call_agent(
                agent_type="receipt_verify",
                input_data={
                    "prompt": verify_prompt,
                    "system": "你是严谨的财税数据校验专家，只返回 JSON。",
                    "temperature": 0.1,
                },
            )
            verify_content = verify_resp.get("content", "")
            verification = _safe_json_parse(verify_content)
            if verification is None:
                verification = {
                    "valid": False,
                    "issues": ["校验模型未返回标准 JSON"],
                    "raw_text": verify_content,
                    "confidence": 0.0,
                }
            result["verification"] = verification
            result["steps"].append({
                "step": "field_verification",
                "model": "deepseek-ai/DeepSeek-V3.2",
                "status": "done",
                "usage": verify_resp.get("usage"),
            })

            # 采用校验后的数据作为最终结果
            corrected = verification.get("corrected_data") if isinstance(verification, dict) else None
            result["final_data"] = corrected if corrected else parsed

            if progress:
                confidence = verification.get("confidence", 0) if isinstance(verification, dict) else 0
                await progress(
                    "receipt_verify",
                    "done",
                    f"字段校验完成（置信度 {confidence}）",
                    {"valid": verification.get("valid") if isinstance(verification, dict) else False},
                )
        except Exception as exc:
            logger.exception("ReceiptParser verify 步骤失败")
            result["errors"].append(f"field_verification: {exc}")
            result["steps"].append({
                "step": "field_verification",
                "model": "deepseek-ai/DeepSeek-V3.2",
                "status": "error",
                "error": str(exc),
            })
            # 校验失败不影响已有解析结果
            result["final_data"] = parsed
            if progress:
                await progress("receipt_verify", "error", f"校验失败: {exc}", None)

        return result

    async def parse_text(
        self,
        receipt_text: str,
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """解析票据文本（无图片，直接文本提取）"""
        if progress:
            await progress("receipt_verify", "running", "调用 DeepSeek-V3.2 解析票据文本", None)

        try:
            verify_resp = await gmi_client.call_agent(
                agent_type="receipt_verify",
                input_data={
                    "system": "你是跨境财税票据解析专家，只返回 JSON。",
                    "prompt": f"{self.VISION_PROMPT}\n\n请从以下票据文本中提取字段：\n\n{receipt_text}",
                    "temperature": 0.2,
                },
            )
            content = verify_resp.get("content", "")
            parsed = _safe_json_parse(content)
            if parsed is None:
                parsed = {"raw_text": content}

            if progress:
                await progress("receipt_verify", "done", "票据文本解析完成", None)

            return {
                "agent": self.name,
                "steps": [{
                    "step": "text_parse",
                    "model": "deepseek-ai/DeepSeek-V3.2",
                    "status": "done",
                    "usage": verify_resp.get("usage"),
                }],
                "parsed_data": parsed,
                "verification": None,
                "final_data": parsed,
                "errors": [],
            }
        except Exception as exc:
            logger.exception("ReceiptParser parse_text 失败")
            return {
                "agent": self.name,
                "steps": [],
                "parsed_data": None,
                "verification": None,
                "final_data": None,
                "errors": [str(exc)],
            }


# ===========================================================================
# Agent 2: ComplianceAgent — 财税合规决策
# ===========================================================================

class ComplianceAgent:
    """合规决策 Agent

    工作流：
      1. 调用 openai/gpt-5 进行税法匹配和风险检测
      2. 调用 zai-org/GLM-5-FP8 生成多语种合规建议
    """

    name = "ComplianceAgent"
    description = "财税合规决策 Agent（gpt-5 + GLM-5）"

    DECISION_PROMPT_TEMPLATE = """你是一名精通多国税法的合规决策专家，熟悉欧盟 VAT、美国 Sales Tax、英国 VAT、日本消费税、中国跨境税务等法规。

请基于以下业务数据，进行合规分析：

【业务数据】
{data}

【目标国家/地区】{country}
【交易平台】{platform}

请检测以下合规要点并返回 JSON：
{{
  "vat_applicability": {{
    "applicable": true/false,
    "rate": "适用 VAT 税率",
    "reason": "适用理由"
  }},
  "transfer_pricing": {{
    "risk_level": "low/medium/high",
    "issues": ["转移定价风险点"],
    "suggestion": "建议"
  }},
  "filing_deadline": {{
    "next_deadline": "下次申报截止日期",
    "frequency": "申报频率",
    "penalty": "逾期罚款说明"
  }},
  "sales_tax_registration": {{
    "required": true/false,
    "threshold": "登记阈值",
    "states": ["需登记的州/地区"]
  }},
  "input_tax_credit": {{
    "deductible": true/false,
    "deductible_items": ["可抵扣进项税项目"],
    "non_deductible_items": ["不可抵扣项目"]
  }},
  "overall_risk": "low/medium/high",
  "risk_score": 0-100,
  "key_findings": ["关键发现1", "关键发现2"]
}}

只返回 JSON，不要其他内容。"""

    ADVICE_PROMPT_TEMPLATE = """你是一名跨境财税顾问，擅长用多语种（中文、英文、目标国家语言）输出合规建议。

基于以下合规分析结果，请生成结构化的合规建议报告：

【合规分析】
{analysis}

【目标国家】{country}

请返回 JSON：
{{
  "summary_cn": "中文合规建议摘要",
  "summary_en": "English compliance advice summary",
  "summary_local": "目标国家语言的建议摘要",
  "action_items": [
    {{"priority": "high/medium/low", "action": "行动项", "deadline": "建议完成时间"}}
  ],
  "warnings": ["警告事项"],
  "recommended_documents": ["建议准备的文档"]
}}

只返回 JSON。"""

    async def analyze(
        self,
        receipt_data: Dict[str, Any],
        target_country: str,
        platform: str,
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """执行合规分析

        Args:
            receipt_data: 票据解析结果（final_data）
            target_country: 目标国家/地区
            platform: 交易平台
            progress: 进度回调
        """
        result: Dict[str, Any] = {
            "agent": self.name,
            "steps": [],
            "decision": None,
            "advice": None,
            "errors": [],
        }

        # 步骤 1：gpt-5 合规决策
        if progress:
            await progress(
                "compliance_decision",
                "running",
                "调用 gpt-5 进行税法匹配与风险检测",
                None,
            )

        try:
            decision_prompt = self.DECISION_PROMPT_TEMPLATE.format(
                data=json.dumps(receipt_data, ensure_ascii=False, indent=2),
                country=target_country,
                platform=platform,
            )
            decision_resp = await gmi_client.call_agent(
                agent_type="compliance_decision",
                input_data={
                    "system": "你是多国税法合规决策专家，只返回 JSON。",
                    "prompt": decision_prompt,
                    "temperature": 0.3,
                },
            )
            decision_content = decision_resp.get("content", "")
            decision = _safe_json_parse(decision_content)
            if decision is None:
                decision = {
                    "raw_text": decision_content,
                    "_parse_warning": "gpt-5 未返回标准 JSON",
                    "overall_risk": "unknown",
                }
            result["decision"] = decision
            result["steps"].append({
                "step": "compliance_decision",
                "model": "openai/gpt-5",
                "status": "done",
                "usage": decision_resp.get("usage"),
            })

            if progress:
                risk = decision.get("overall_risk", "unknown") if isinstance(decision, dict) else "unknown"
                await progress(
                    "compliance_decision",
                    "done",
                    f"合规决策完成（整体风险: {risk}）",
                    {"overall_risk": risk},
                )
        except Exception as exc:
            logger.exception("ComplianceAgent decision 步骤失败")
            result["errors"].append(f"compliance_decision: {exc}")
            result["steps"].append({
                "step": "compliance_decision",
                "model": "openai/gpt-5",
                "status": "error",
                "error": str(exc),
            })
            if progress:
                await progress("compliance_decision", "error", f"合规决策失败: {exc}", None)
            return result

        # 步骤 2：GLM-5 多语种建议
        if progress:
            await progress(
                "compliance_advice",
                "running",
                "调用 GLM-5 生成多语种合规建议",
                None,
            )

        try:
            advice_prompt = self.ADVICE_PROMPT_TEMPLATE.format(
                analysis=json.dumps(decision, ensure_ascii=False, indent=2),
                country=target_country,
            )
            advice_resp = await gmi_client.call_agent(
                agent_type="compliance_advice",
                input_data={
                    "system": "你是跨境财税顾问，擅长多语种输出，只返回 JSON。",
                    "prompt": advice_prompt,
                    "temperature": 0.5,
                },
            )
            advice_content = advice_resp.get("content", "")
            advice = _safe_json_parse(advice_content)
            if advice is None:
                advice = {
                    "raw_text": advice_content,
                    "_parse_warning": "GLM-5 未返回标准 JSON",
                }
            result["advice"] = advice
            result["steps"].append({
                "step": "compliance_advice",
                "model": "zai-org/GLM-5-FP8",
                "status": "done",
                "usage": advice_resp.get("usage"),
            })

            if progress:
                await progress(
                    "compliance_advice",
                    "done",
                    "多语种合规建议生成完成",
                    None,
                )
        except Exception as exc:
            logger.exception("ComplianceAgent advice 步骤失败")
            result["errors"].append(f"compliance_advice: {exc}")
            result["steps"].append({
                "step": "compliance_advice",
                "model": "zai-org/GLM-5-FP8",
                "status": "error",
                "error": str(exc),
            })
            if progress:
                await progress("compliance_advice", "error", f"建议生成失败: {exc}", None)

        return result


# ===========================================================================
# Agent 3: OrchestratorAgent — 业财自动化调度
# ===========================================================================

class OrchestratorAgent:
    """业财自动化调度 Agent

    工作流：
      1. 调用 Qwen3-Coder 分解任务为子步骤
      2. 编排 ReceiptParserAgent 与 ComplianceAgent
      3. 聚合结果生成报表
    """

    name = "OrchestratorAgent"
    description = "业财自动化调度 Agent（Qwen3-Coder + 编排）"

    PLAN_PROMPT_TEMPLATE = """你是 FinFlow 跨境财税智能体的任务规划引擎。请将以下财税处理任务分解为可执行的子步骤。

【任务信息】
- 任务类型: {task_type}
- 交易平台: {platform}
- 目标国家: {country}
- 输入: {input_summary}

请返回 JSON 任务计划：
{{
  "steps": [
    {{
      "id": 1,
      "name": "步骤名称",
      "agent": "ReceiptParserAgent/ComplianceAgent/OrchestratorAgent",
      "description": "步骤描述",
      "depends_on": []
    }}
  ],
  "estimated_duration": "预计耗时（秒）",
  "summary": "整体执行计划摘要"
}}

可选步骤包括：票据识别、字段校验、税法匹配、风险检测、合规建议、报表生成。只返回 JSON。"""

    REPORT_PROMPT_TEMPLATE = """你是 FinFlow 财税报表生成引擎。请基于以下任务执行结果，生成一份结构化的跨境财税处理报表。

【任务信息】
- 任务类型: {task_type}
- 交易平台: {platform}
- 目标国家: {country}

【票据解析结果】
{receipt}

【合规分析结果】
{compliance}

请返回 JSON 报表：
{{
  "report_title": "报表标题",
  "executive_summary": "执行摘要",
  "financial_summary": {{
    "total_sales": "总销售额",
    "platform_fee": "平台手续费",
    "fba_fee": "FBA费用",
    "net_amount": "净到账",
    "currency": "币种"
  }},
  "compliance_summary": {{
    "overall_risk": "整体风险等级",
    "vat_rate": "适用VAT税率",
    "filing_deadline": "申报截止日",
    "key_findings": ["关键发现"]
  }},
  "recommendations": ["建议1", "建议2"],
  "next_steps": ["后续步骤1", "后续步骤2"]
}}

只返回 JSON。"""

    def __init__(self) -> None:
        self.receipt_agent = ReceiptParserAgent()
        self.compliance_agent = ComplianceAgent()

    async def plan_task(
        self,
        task_type: str,
        platform: str,
        target_country: str,
        input_summary: str,
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """调用 Qwen3-Coder 分解任务"""
        if progress:
            await progress("orchestration", "running", "调用 Qwen3-Coder 分解任务", None)

        try:
            plan_prompt = self.PLAN_PROMPT_TEMPLATE.format(
                task_type=task_type,
                platform=platform,
                country=target_country,
                input_summary=input_summary,
            )
            resp = await gmi_client.call_agent(
                agent_type="orchestration",
                input_data={
                    "system": "你是 FinFlow 任务规划引擎，只返回 JSON。",
                    "prompt": plan_prompt,
                    "temperature": 0.4,
                },
            )
            content = resp.get("content", "")
            plan = _safe_json_parse(content)
            if plan is None:
                # 兜底默认计划
                plan = {
                    "steps": [
                        {"id": 1, "name": "票据识别", "agent": "ReceiptParserAgent", "description": "解析票据图片/文本", "depends_on": []},
                        {"id": 2, "name": "字段校验", "agent": "ReceiptParserAgent", "description": "校验提取字段", "depends_on": [1]},
                        {"id": 3, "name": "税法匹配", "agent": "ComplianceAgent", "description": "VAT/销售税匹配", "depends_on": [2]},
                        {"id": 4, "name": "风险检测", "agent": "ComplianceAgent", "description": "合规风险检测", "depends_on": [3]},
                        {"id": 5, "name": "报表生成", "agent": "OrchestratorAgent", "description": "聚合生成报表", "depends_on": [4]},
                    ],
                    "estimated_duration": "60",
                    "summary": "默认执行计划",
                }
            if progress:
                await progress("orchestration", "done", "任务分解完成", {"steps": len(plan.get("steps", []))})
            return {
                "plan": plan,
                "usage": resp.get("usage"),
                "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
            }
        except Exception as exc:
            logger.exception("OrchestratorAgent plan_task 失败")
            if progress:
                await progress("orchestration", "error", f"任务分解失败: {exc}", None)
            return {
                "plan": {
                    "steps": [],
                    "estimated_duration": "0",
                    "summary": f"规划失败: {exc}",
                },
                "usage": None,
                "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
                "error": str(exc),
            }

    async def generate_report(
        self,
        task_type: str,
        platform: str,
        target_country: str,
        receipt_result: Dict[str, Any],
        compliance_result: Dict[str, Any],
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """聚合结果生成报表"""
        if progress:
            await progress("report_generation", "running", "聚合结果生成财税报表", None)

        try:
            report_prompt = self.REPORT_PROMPT_TEMPLATE.format(
                task_type=task_type,
                platform=platform,
                country=target_country,
                receipt=json.dumps(receipt_result, ensure_ascii=False, indent=2),
                compliance=json.dumps(compliance_result, ensure_ascii=False, indent=2),
            )
            resp = await gmi_client.call_agent(
                agent_type="orchestration",
                input_data={
                    "system": "你是 FinFlow 财税报表生成引擎，只返回 JSON。",
                    "prompt": report_prompt,
                    "temperature": 0.4,
                },
            )
            content = resp.get("content", "")
            report = _safe_json_parse(content)
            if report is None:
                report = {
                    "report_title": "FinFlow 跨境财税处理报表",
                    "raw_text": content,
                    "_parse_warning": "报表模型未返回标准 JSON",
                }
            if progress:
                await progress("report_generation", "done", "财税报表生成完成", None)
            return {
                "report": report,
                "usage": resp.get("usage"),
                "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
            }
        except Exception as exc:
            logger.exception("OrchestratorAgent generate_report 失败")
            if progress:
                await progress("report_generation", "error", f"报表生成失败: {exc}", None)
            return {
                "report": None,
                "usage": None,
                "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
                "error": str(exc),
            }

    async def execute_task(
        self,
        task_type: str,
        platform: str,
        target_country: str,
        receipt_text: Optional[str] = None,
        receipt_image_base64: Optional[str] = None,
        progress: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """完整执行业财自动化任务

        流程：
          1. 任务规划（Qwen3-Coder）
          2. 票据解析（ReceiptParserAgent）
          3. 合规决策（ComplianceAgent）
          4. 报表生成（Qwen3-Coder 聚合）
        """
        result: Dict[str, Any] = {
            "agent": self.name,
            "task_type": task_type,
            "platform": platform,
            "target_country": target_country,
            "plan": None,
            "receipt_parsing": None,
            "compliance": None,
            "report": None,
            "errors": [],
        }

        # 输入摘要
        if receipt_image_base64:
            input_summary = "票据图片输入"
        elif receipt_text:
            input_summary = receipt_text[:200]
        else:
            input_summary = "无输入数据"

        # 1. 任务规划
        plan_result = await self.plan_task(
            task_type=task_type,
            platform=platform,
            target_country=target_country,
            input_summary=input_summary,
            progress=progress,
        )
        result["plan"] = plan_result.get("plan")
        if plan_result.get("error"):
            result["errors"].append(f"plan: {plan_result['error']}")

        # 2. 票据解析
        if receipt_image_base64:
            receipt_result = await self.receipt_agent.parse_image(
                image_base64=receipt_image_base64, progress=progress
            )
        else:
            receipt_result = await self.receipt_agent.parse_text(
                receipt_text=receipt_text or "无票据数据", progress=progress
            )
        result["receipt_parsing"] = receipt_result
        result["errors"].extend(receipt_result.get("errors", []))

        final_receipt_data = receipt_result.get("final_data") or receipt_result.get("parsed_data") or {}

        # 3. 合规决策
        compliance_result = await self.compliance_agent.analyze(
            receipt_data=final_receipt_data,
            target_country=target_country,
            platform=platform,
            progress=progress,
        )
        result["compliance"] = compliance_result
        result["errors"].extend(compliance_result.get("errors", []))

        # 4. 报表生成
        report_result = await self.generate_report(
            task_type=task_type,
            platform=platform,
            target_country=target_country,
            receipt_result=final_receipt_data,
            compliance_result=compliance_result.get("decision") or {},
            progress=progress,
        )
        result["report"] = report_result.get("report")
        if report_result.get("error"):
            result["errors"].append(f"report: {report_result['error']}")

        return result


# ===========================================================================
# 全局实例
# ===========================================================================

orchestrator = OrchestratorAgent()
