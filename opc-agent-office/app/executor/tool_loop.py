"""办公任务 LLM 工具循环（ADR-062 执行引擎核心）。

系统提示词 + 任务输入 → LLM（OpenAI 兼容 function calling）→ 工具执行 →
结果回填 → 循环直至最终答复；成本护栏两级（ADR-065 M7）：用量达软阈值
（默认预算 80%）注入「收尾纪律」继续执行，达 100% 硬停并交由桥接层上报明确
原因；轮次达上限时注入收尾指令再给一轮机会（Spec 降级链路）。
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import AgentConfig
from app.executor.llm_client import LLMClient, LLMError
from app.executor.verify import MAX_REPAIR_ROUNDS
from app.tools.base import ToolError, ToolRegistry

logger = logging.getLogger(__name__)

#: 喂给 LLM 的工具结果字符上限（防长网页撑爆上下文）
_TOOL_RESULT_MAX_CHARS = 24_000

#: 轨迹行摘要截断（ADR-065 M5）：工具入参摘要 / 结果摘要的字符上限
_TRACE_ARG_SUMMARY_MAX_CHARS = 200
_TRACE_RESULT_SUMMARY_MAX_CHARS = 300

#: 成本护栏（ADR-065 M7）：软/硬阈值触发时的轨迹行 stage 标记（**非** ADR-055
#: 的封闭 stage 枚举，只出现在轨迹 payload 里用于定位触发点，前端按 kind 渲染）
_BUDGET_TRACE_STAGE_SOFT = "budget_soft_limit"
_BUDGET_TRACE_STAGE_HARD = "budget_hard_stop"

#: 软阈值系统提示（ADR-065 M7）：注入一次即持续生效，要求尽快收敛
SOFT_LIMIT_SYSTEM_NOTE = """系统提示（成本护栏·已进入收尾）：本任务 token 用量已达预算的 {percent}%。
请立即收敛：
1. 只交付当前已能完成的内容，**不要再开启新的检索/抓取/大段改写**；
2. 已生成的交付物视为最终版本，最多做一次最小必要修补；
3. 下一轮直接给出最终交付说明（交付物清单 + 验收结果 + 核心结论），不要再调用工具。"""

#: stage → 进度基准（ADR-055 当前动作展示；随轮次微增，避免长时间停滞）
_STAGE_PROGRESS: dict[str, int] = {"planning": 15, "searching": 30, "coding": 55, "summarizing": 90}

SYSTEM_PROMPT = """你是一名专业的办公任务助手，运行在中枢调度的办公 Agent 节点上，工作目录为 {work_dir}。

你的职责：根据任务要求产出高质量交付物（Word 文档 / PPT / 资料汇总）。

工作方式：
0. 任务附带素材文件（工作目录内的 docx/xlsx/csv/txt/md 等）时，**先用 read_* 工具
   读取素材真实内容再产出**（read_docx / read_xlsx / read_csv / read_text），
   不要凭空编写素材内容；
1. 用户消息中给出具体网址（URL）时，**优先直接用 web_fetch 抓取该网址**，
   不要先去搜索——搜索结果可能完全偏离目标站点；
2. 需要检索补充资料时用 web_search；要限定某网站内搜索，关键词后加
   「 site:域名」（如「隐私政策 site:duckduckgo.com」）；
3. 产出文档用 write_docx，产出 PPT 用 write_pptx，文件保存在工作目录（相对路径）；
4. 资料引用：最终交付物中注明信息来源（URL），无法核实的表述要标注；
5. 工具失败时换方案继续推进，不要反复重试同一操作；
6. 交付前自检：任务附带验收清单（checklist）时，必须逐条核对交付物是否满足，
   最终总结中列出每条验收项的核对结果（通过 / 未满足及原因）；
7. 全部产出完成后，用自然语言给出最终总结（交付物清单 + 验收结果 + 核心结论），
   不要再调用工具。"""

#: 验收回灌修复轮的系统约束（ADR-065 M4：只修未通过条目，已达标内容不得重写）
REPAIR_SYSTEM_NOTE = """本轮是**验收回灌修复轮**（第 {round_index}/{max_rounds} 轮）：用户消息中列出的只是**未通过**的交付质量闸条目。

硬性要求：
1. 只修改未通过条目涉及的内容；**已达标内容必须逐字保留**，不得重写、改写、精简、删除或调整顺序；
2. 用写工具覆盖原交付物文件（保持同一文件名与格式），不要另存新文件替代；
3. 不得改动验收要求本身来「绕过」未通过项；
4. 确实无法达标的条目，在最终说明中写明原因，不要伪造通过。"""


@dataclass
class LoopOutcome:
    """工具循环结果。"""

    summary: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    rounds: int = 0
    stopped_reason: str = "final_answer"
    warnings: list[str] = field(default_factory=list)


class ToolLoopRunner:
    """单任务工具循环执行器。"""

    def __init__(
        self,
        config: AgentConfig,
        llm: LLMClient,
        registry: ToolRegistry,
        *,
        on_progress: Callable[[int, str, str], Awaitable[None]],
        on_sources: Callable[[list[str]], Awaitable[None]] | None = None,
        on_trace: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        on_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Args:
        config: 节点配置（轮次/预算/超时）。
        llm: LLM 客户端。
        registry: 工具注册表（已绑定工作目录）。
        on_progress: 进度回调 (progress, stage, message)。
        on_sources: 联网来源回调（web_search/web_fetch 命中的站点条目，
            供任务摘要「待办明细」下方展示参考来源；可空）。
        on_trace: 轨迹行回调 (kind, payload)（ADR-065 M5 轨迹镜像：每轮 LLM 的
            reasoning/text 与每次工具调用各一条；可空——轨迹是旁路，
            未注入时循环行为与既有完全一致）。
        on_usage: 单次执行的用量回调 ``(payload) -> Awaitable``（ADR-065 M7：
            ``prompt_tokens`` / ``completion_tokens`` / ``rounds`` /
            ``budget_percent``），由桥接层在**任务结束**时按累计值发一条
            ``usage`` 轨迹行；可空（未注入时不收集，行为与既有完全一致）。
        """
        self._config = config
        self._llm = llm
        self._registry = registry
        self._on_progress = on_progress
        self._on_sources = on_sources
        self._on_trace = on_trace
        self._on_usage = on_usage
        # 成本护栏幂等位（ADR-065 M7）：每个 runner 实例 = 一个任务，同一级只触发一次
        self._soft_limit_notified = False
        self._hard_limit_stopped = False
        #: 当前执行的任务 ID（日志用）
        self._task_id = ""
        #: 本次执行的用量快照（结束时的 usage 回调取此处，避免异常路径丢用量）
        self._outcome: LoopOutcome | None = None

    async def run(
        self,
        task_id: str,
        instruction: str,
        cancel_event: asyncio.Event,
        *,
        repair_round: int | None = None,
    ) -> LoopOutcome:
        """执行工具循环直到最终答复或预算终止（结束时回调用量，ADR-065 M7）。

        Args:
            task_id: 任务 ID（日志与进度用）。
            instruction: 任务指令（描述+规格三件套已由桥接层组装；回灌轮为
                只含未通过条目的修复指令）。
            cancel_event: 取消事件（中断时尽快退出）。
            repair_round: 交付质量闸回灌轮序号（ADR-065 M4）；非空时注入
                「只修未通过条目、已达标内容逐字保留」的系统约束。
        """
        try:
            return await self._run_loop(
                task_id, instruction, cancel_event, repair_round=repair_round
            )
        finally:
            # 用量可见（ADR-065 M7）：无论正常收敛、预算硬停还是异常上抛都上报
            # 本次执行的用量（桥接层按任务累计后发一条 usage 轨迹行）
            await self._emit_usage(task_id)

    async def _run_loop(
        self,
        task_id: str,
        instruction: str,
        cancel_event: asyncio.Event,
        *,
        repair_round: int | None = None,
    ) -> LoopOutcome:
        """工具循环主体（护栏与收尾语义见模块 docstring）。"""
        outcome = LoopOutcome()
        self._outcome = outcome
        self._task_id = task_id
        system_prompt = SYSTEM_PROMPT.format(work_dir=self._config.workspace_root)
        if repair_round:
            system_prompt += "\n\n" + REPAIR_SYSTEM_NOTE.format(
                round_index=repair_round, max_rounds=MAX_REPAIR_ROUNDS
            )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": instruction},
        ]
        tools = self._registry.openai_schemas()
        if repair_round:
            await self._on_progress(
                8, "testing", f"正在按验收未通过项修复（第 {repair_round}/{MAX_REPAIR_ROUNDS} 轮）"
            )
        else:
            await self._on_progress(8, "planning", "正在规划任务大纲")

        for round_index in range(1, self._config.office_llm_max_rounds + 1):
            if cancel_event.is_set():
                outcome.stopped_reason = "cancelled"
                return outcome
            outcome.rounds = round_index
            try:
                response = await self._llm.chat(messages, tools)
            except LLMError as exc:
                # 已重试仍失败：已有产出则尽力收尾，否则失败上抛由桥接层处理
                outcome.warnings.append(f"LLM 调用失败：{exc}")
                if outcome.prompt_tokens > 0 and any(
                    isinstance(message, dict) and message.get("role") == "tool" for message in messages
                ):
                    outcome.stopped_reason = "llm_unavailable_partial"
                    outcome.summary = "执行中途 LLM 不可用，请查看已生成的交付物。"
                    return outcome
                raise
            outcome.prompt_tokens += response["prompt_tokens"]
            outcome.completion_tokens += response["completion_tokens"]
            message = response["message"]
            tool_calls = message.get("tool_calls") or []
            # 轨迹镜像（ADR-065 M5）：本轮 LLM 返回的思考与正文片段各一条
            await self._emit_llm_trace(message, round_index)

            if not tool_calls:
                outcome.summary = str(message.get("content") or "").strip() or "任务完成。"
                await self._on_progress(95, "summarizing", "正在整理最终说明")
                return outcome

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                }
            )
            await self._execute_tool_calls(task_id, messages, tool_calls, cancel_event, outcome)
            if await self._check_budget(messages, outcome, stage=self._stage_of_calls(tool_calls)):
                return outcome

        outcome.stopped_reason = "max_rounds"
        outcome.warnings.append(f"达到工具循环轮次上限（{self._config.office_llm_max_rounds}）")
        return outcome

    async def _execute_tool_calls(
        self,
        task_id: str,
        messages: list[dict[str, Any]],
        tool_calls: list[dict[str, Any]],
        cancel_event: asyncio.Event,
        outcome: LoopOutcome,
    ) -> None:
        """执行本轮全部工具调用并回填 tool 消息。"""
        for call in tool_calls:
            if cancel_event.is_set():
                return
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            raw_args = function.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                args = {}
            result = await self._run_one_tool(task_id, name, args, outcome)
            messages.append({"role": "tool", "tool_call_id": call.get("id") or "", "content": result})

    async def _run_one_tool(
        self, task_id: str, name: str, args: dict[str, Any], outcome: LoopOutcome
    ) -> str:
        """执行单个工具（线程池调度 + 进度映射 + 结果摘要上报 + 结果截断）。"""
        started = time.monotonic()
        stage = self._stage_of(name)
        base = _STAGE_PROGRESS.get(stage, 20)
        progress = min(92, base + min(9, outcome.rounds))
        args_hint = self._args_hint(name, args)
        await self._on_progress(progress, stage, f"正在{name}{args_hint}")
        ok = True
        try:
            tool = self._registry.get(name)
            result = await asyncio.to_thread(tool.execute, args)
        except ToolError as exc:
            ok = False
            result = f"工具执行失败：{exc}"
            logger.warning(
                "office_tool_failed",
                extra={"task_id": task_id, "tool": name, "error": str(exc)},
            )
        except Exception as exc:  # noqa: BLE001 — 工具异常不终止循环，转文字告知 LLM
            ok = False
            result = f"工具异常：{type(exc).__name__}: {exc}"
            logger.exception(
                "office_tool_crashed", extra={"task_id": task_id, "tool": name}
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "office_tool_executed",
            extra={
                "task_id": task_id,
                "tool": name,
                "duration_ms": duration_ms,
            },
        )
        # 结果摘要上报（结果可视化）：让用户看到搜到了什么/参考了什么
        summary = self._result_summary(name, result)
        # 轨迹镜像（ADR-065 M5）：每次工具调用一条 tool 行（成功/失败都发，
        # ok 标记结局；args/result 摘要已在桥接层脱敏截断）
        await self._emit_trace(
            "tool",
            {
                "tool": name,
                "args_summary": self._args_summary(name, args),
                "result_summary": summary[:_TRACE_RESULT_SUMMARY_MAX_CHARS],
                "ok": ok,
            },
        )
        if summary:
            await self._on_progress(
                min(92, progress + 1), stage, f"{name} 完成（{duration_ms / 1000:.1f}s）：{summary}"
            )
        # 联网来源回传（待办下方展示参考站点）
        if self._on_sources is not None and name in ("web_search", "web_fetch"):
            entries = self._extract_sources(name, result)
            if entries:
                await self._on_sources(entries)
        return result[:_TOOL_RESULT_MAX_CHARS]

    async def _emit_llm_trace(self, message: dict[str, Any], round_index: int) -> None:
        """上报本轮 LLM 返回的思考与正文片段（ADR-065 M5）。

        思考字段按 OpenAI 兼容生态的两种常见命名取：``reasoning_content``
        （DeepSeek 等推理模型）与 ``reasoning``（部分网关）。**响应不含思考字段时
        只发 text 行**（不伪造空 reasoning 行）；正文为空（纯工具调用轮）时
        同样不发 text 行——空行对镜像没有信息量，只会白占中枢行数配额。
        """
        reasoning = str(
            message.get("reasoning_content") or message.get("reasoning") or ""
        ).strip()
        if reasoning:
            await self._emit_trace(
                "reasoning", {"round": round_index, "text": reasoning}
            )
        content = str(message.get("content") or "").strip()
        if content:
            await self._emit_trace("text", {"round": round_index, "text": content})

    async def _emit_trace(self, kind: str, payload: dict[str, Any]) -> None:
        """交给桥接层的轨迹通道（未注入时静默跳过，轨迹是旁路）。

        回调异常在此吞掉并告警：轨迹上报失败**不得**中断工具循环、影响任务完成
        判定或进度上报（桥接层自身也兜底，这里是双保险）。
        """
        if self._on_trace is None:
            return
        try:
            await self._on_trace(kind, payload)
        except Exception as exc:  # noqa: BLE001 — 旁路：轨迹失败绝不影响工具循环
            logger.warning(
                "office_trace_emit_failed",
                extra={"event": "office_trace_emit_failed", "kind": kind, "error": str(exc)},
            )

    @staticmethod
    def _args_summary(tool_name: str, args: dict[str, Any]) -> str:
        """工具入参摘要（动作提示 + 紧凑 JSON，截断；脱敏在桥接层统一做）。"""
        hint = ToolLoopRunner._args_hint(tool_name, args)
        try:
            raw = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            raw = str(args)
        summary = f"{hint} {raw}".strip() if hint else raw
        return summary[:_TRACE_ARG_SUMMARY_MAX_CHARS]

    @staticmethod
    def _extract_sources(tool_name: str, result: str) -> list[str]:
        """从联网工具结果提取参考来源条目（域名+标题，待办展示用）。"""
        entries: list[str] = []
        if tool_name == "web_search":
            for match in re.finditer(r"\d+\. (.+?)\n\s+链接: (\S+)", result):
                title, url = match.group(1).strip(), match.group(2).strip()
                domain = urlsplit(url).netloc or url[:30]
                entries.append(f"{domain}｜{title[:50]}")
        elif tool_name == "web_fetch" and result.startswith("已抓取"):
            url = next((part for part in result.split() if part.startswith("http")), "")
            domain = urlsplit(url).netloc
            if domain:
                entries.append(f"{domain}｜{url[:70]}")
        return entries[:8]

    @staticmethod
    def _args_hint(tool_name: str, args: dict[str, Any]) -> str:
        """工具调用中的动作提示（带关键入参，用户可见在搜什么）。"""
        if tool_name == "web_search":
            query = str(args.get("query") or "").strip()
            return f"「{query[:40]}」" if query else ""
        if tool_name == "web_fetch":
            url = str(args.get("url") or "").strip()
            return f"网页 {url[:50]}" if url else "网页"
        if tool_name in ("write_docx", "write_pptx"):
            filename = str(args.get("filename") or "").strip()
            return f"文档 {filename[:40]}" if filename else "文档"
        return ""

    @staticmethod
    def _result_summary(tool_name: str, result: str) -> str:
        """从工具结果提取一句话摘要（进度展示：搜到了什么/参考了什么）。"""
        first_line = next((line.strip() for line in result.splitlines() if line.strip()), "")
        if tool_name == "web_search" and first_line.startswith("搜索"):
            # 搜索结果：拼前 3 条标题
            titles = [
                line.split(". ", 1)[1].split("\n")[0].strip()
                for line in result.splitlines()
                if line.strip() and line.strip()[0].isdigit() and ". " in line
            ][:3]
            if titles:
                return f"找到 {len(titles)}+ 条结果：{'；'.join(titles)[:80]}"
        return first_line[:100]

    async def _check_budget(
        self, messages: list[dict[str, Any]], outcome: LoopOutcome, *, stage: str = ""
    ) -> bool:
        """成本护栏两级检查（ADR-065 M7）：软阈值收尾（继续）/ 硬阈值停循环。

        - **软阈值**（``office_llm_budget_soft_ratio``，默认 80%）：注入「收尾纪律」
          系统消息 + 发一条 ``status`` 轨迹行 + 记 ``office_budget_soft_limit``
          warning，**任务继续**；同一任务只注入一次（幂等）。
        - **硬阈值**（100%）：**不再调用 LLM 收尾轮**（Spec D5「100% 硬阻断，停止
          继续调用 LLM」——最后一轮的收尾开销也省掉），直接置
          ``stopped_reason="token_budget"``，由 ``office_bridge`` 上报明确失败
          原因——**不得假装成功**。
        - **轮次上限**：沿用既有语义，注入收尾指令后跑最后一轮（不给工具）。
          注：单轮用量一次性从软阈值下方冲到 100% 时，会先落入上面的硬停分支，
          **不再**发轮次收尾指令（硬停已终止循环）。

        Returns:
            True 表示已终止循环（硬停或轮次收尾完成），调用方应结束循环。
        """
        budget = self._config.office_llm_token_budget
        if budget <= 0:  # 防御：预算非法（0/负数）时护栏不生效，行为与既有一致
            return False
        tokens = outcome.prompt_tokens + outcome.completion_tokens
        soft_threshold = int(budget * self._config.office_llm_budget_soft_ratio)
        if not self._soft_limit_notified and tokens >= soft_threshold:
            # 幂等：每级只触发一次，重复跨越软阈值不重复注入/重复上报
            self._soft_limit_notified = True
            await self._soft_limit(messages, outcome, stage)
        if self._hard_limit_stopped or tokens >= budget:
            self._hard_stop(stage)
            await self._emit_hard_stop_trace(stage)
            return True
        if outcome.rounds < self._config.office_llm_max_rounds:
            return False
        messages.append(
            {
                "role": "user",
                "content": (
                    "系统提示：即将达到工具循环轮次上限。请立即停止调用工具，"
                    "基于已有信息直接输出最终交付说明。"
                ),
            }
        )
        try:
            response = await self._llm.chat(messages, None)  # 收尾轮不给工具
            message = response["message"]
            outcome.summary = str(message.get("content") or "").strip()
            outcome.prompt_tokens += response["prompt_tokens"]
            outcome.completion_tokens += response["completion_tokens"]
            # 收尾轮同样进轨迹（reasoning/text），与普通轮口径一致
            await self._emit_llm_trace(message, outcome.rounds)
        except LLMError as exc:
            outcome.warnings.append(f"收尾轮 LLM 调用失败：{exc}")
        outcome.stopped_reason = "max_rounds"
        return True

    async def _soft_limit(
        self, messages: list[dict[str, Any]], outcome: LoopOutcome, stage: str
    ) -> None:
        """软阈值收尾（ADR-065 M7 80%）：注入收尾纪律 + status 轨迹行 + warning。

        **不终止任务**：只是把「尽快收敛、只交当前可交付内容」这条纪律传达给模型；
        注入用 ``system`` 角色（不占用 user 轮次语义，也不改变既有 tool 消息顺序）。
        """
        percent = self._budget_percent(outcome)
        messages.append(
            {"role": "system", "content": SOFT_LIMIT_SYSTEM_NOTE.format(percent=percent)}
        )
        message = f"token 用量已达预算 {percent}%，已进入收尾：尽快收敛并交付当前内容"
        logger.warning(
            "office_budget_soft_limit",
            extra={
                "event": "office_budget_soft_limit",
                "task_id": self._task_id,
                "stage": stage or "unknown",
                "budget_percent": percent,
                "tokens": outcome.prompt_tokens + outcome.completion_tokens,
                "budget": self._config.office_llm_token_budget,
                "rounds": outcome.rounds,
            },
        )
        await self._emit_trace(
            "status",
            {
                "budget_percent": percent,
                "stage": _BUDGET_TRACE_STAGE_SOFT,
                "message": message,
                "rounds": outcome.rounds,
            },
        )

    def _hard_stop(self, stage: str) -> None:
        """硬阈值停循环（ADR-065 M7 100%）：置停止原因 + warning（同步部分）。

        轨迹行由调用方在返回前 :meth:`_emit_hard_stop_trace` 发出（本方法保持
        同步：它只改状态与记日志，便于在任何上下文里调用）。
        """
        self._hard_limit_stopped = True
        outcome = self._outcome
        assert outcome is not None  # 只由 _check_budget 在循环内调用，必已初始化
        percent = self._budget_percent(outcome)
        outcome.stopped_reason = "token_budget"
        outcome.warnings.append(f"token 预算已耗尽（{percent}%），已停止继续调用 LLM")
        if outcome.summary:
            outcome.summary += (
                "\n\n注意：token 预算已耗尽，任务被硬停，本说明基于已完成的部分产出。"
            )
        logger.warning(
            "office_budget_hard_stop",
            extra={
                "event": "office_budget_hard_stop",
                "task_id": self._task_id,
                "stage": stage or "unknown",
                "budget_percent": percent,
                "tokens": outcome.prompt_tokens + outcome.completion_tokens,
                "budget": self._config.office_llm_token_budget,
                "rounds": outcome.rounds,
            },
        )

    async def _emit_hard_stop_trace(self, stage: str) -> None:
        """发硬停的 ``status`` 轨迹行：前端折叠块能立刻看到「为什么停了」。"""
        outcome = self._outcome
        assert outcome is not None  # 与 _hard_stop 同源，必已初始化
        percent = self._budget_percent(outcome)
        await self._emit_trace(
            "status",
            {
                "budget_percent": percent,
                "stage": _BUDGET_TRACE_STAGE_HARD,
                "message": f"token 预算已耗尽（{percent}%），已停止继续调用 LLM，等待人工决定",
                "rounds": outcome.rounds,
            },
        )

    def _budget_percent(self, outcome: LoopOutcome) -> int:
        """用量百分比（对预算取整，夹取 0..100，供日志/轨迹/上报共用）。"""
        budget = self._config.office_llm_token_budget
        if budget <= 0:
            return 0
        used = outcome.prompt_tokens + outcome.completion_tokens
        return max(0, min(100, round(used * 100 / budget)))

    async def _emit_usage(self, task_id: str) -> None:
        """上报本次执行的用量（ADR-065 M7）：桥接层按任务累计后发 ``usage`` 轨迹行。

        用量是任务的真实成本，无论正常收敛、预算硬停还是异常上抛都要上报（本方法
        只读快照，不上抛异常——回调异常由桥接层兜底，这里再兜一层）。
        """
        outcome = self._outcome
        if outcome is None or self._on_usage is None:
            return
        try:
            await self._on_usage(
                {
                    "prompt_tokens": outcome.prompt_tokens,
                    "completion_tokens": outcome.completion_tokens,
                    "rounds": outcome.rounds,
                    "budget_percent": self._budget_percent(outcome),
                    "stopped_reason": outcome.stopped_reason,
                }
            )
        except Exception as exc:  # noqa: BLE001 — 旁路：用量上报失败绝不影响工具循环
            logger.warning(
                "office_usage_emit_failed",
                extra={"event": "office_usage_emit_failed", "task_id": task_id, "error": str(exc)},
            )

    @staticmethod
    def _stage_of(tool_name: str) -> str:
        """工具名 → ADR-055 stage 枚举映射（不扩展中枢封闭枚举）。"""
        if tool_name in ("web_search", "web_fetch"):
            return "searching"
        if tool_name in ("write_docx", "write_pptx"):
            return "coding"
        return "planning"

    @classmethod
    def _stage_of_calls(cls, tool_calls: list[dict[str, Any]]) -> str:
        """本轮工具调用 → stage（取首个可识别工具；护栏日志/轨迹标注用）。"""
        for call in tool_calls:
            function = call.get("function") or {}
            stage = cls._stage_of(str(function.get("name") or ""))
            if stage != "planning":
                return stage
        return "planning"


def default_registry(work_dir: Path, config: AgentConfig) -> ToolRegistry:
    """构建绑定工作目录的默认工具集（四件套 + read_* 素材读取 + edit_* 另存 + 图表，ADR-062/065）。"""
    from app.tools.chart_tool import RenderChartTool
    from app.tools.docx_tool import DocxTool, EditDocxTool
    from app.tools.fetch_tool import WebFetchTool
    from app.tools.pptx_tool import PptxTool
    from app.tools.read_tool import ReadCsvTool, ReadDocxTool, ReadTextTool, ReadXlsxTool
    from app.tools.search_tool import WebSearchTool
    from app.tools.xlsx_tool import EditXlsxTool, WriteXlsxTool

    registry = ToolRegistry()
    registry.register(
        WebSearchTool(work_dir, config.office_web_search_max_results, proxy=config.office_proxy_url)
    )
    registry.register(
        WebFetchTool(work_dir, config.office_fetch_timeout_seconds, proxy=config.office_proxy_url)
    )
    registry.register(DocxTool(work_dir))
    registry.register(PptxTool(work_dir))
    # 素材读取（只读，ADR-065 M1）：注册顺序即 schema 顺序，读工具排在写工具之后
    registry.register(ReadDocxTool(work_dir))
    registry.register(ReadXlsxTool(work_dir))
    registry.register(ReadCsvTool(work_dir))
    registry.register(ReadTextTool(work_dir))
    # 表格与编辑另存（ADR-065 M2）：编辑类工具一律读入 → 改 → 另存新文件，不动原件
    registry.register(WriteXlsxTool(work_dir))
    registry.register(EditXlsxTool(work_dir))
    registry.register(EditDocxTool(work_dir))
    # 图表渲染（ADR-065 M3）：matplotlib Agg 出 PNG，供文档嵌入或独立交付
    registry.register(RenderChartTool(work_dir))
    return registry
