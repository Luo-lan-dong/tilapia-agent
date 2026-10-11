"""任务规划 Agent：将自然语言需求解析为结构化任务。"""

import json
import logging
import re

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.deepseek import DeepSeekAPIError, DeepSeekClient
from app.core.errors import TaskPlanningError
from app.services.llm_provider_service import LLMProviderService

logger = logging.getLogger(__name__)

VALID_AGENT_TYPES: frozenset[str] = frozenset(
    {"code", "design", "copywriting", "video", "office"}
)

# 旧类型归一化（ADR-016：agent_type 以 code 替换 programming）：
# LLM 可能仍按旧习惯返回 programming，统一映射为 code 而非报错。
_AGENT_TYPE_ALIASES: dict[str, str] = {"programming": "code"}

SYSTEM_PROMPT_TEMPLATE: str = """你是一个任务规划助手，负责将用户的自然语言需求解析为结构化任务。

请严格遵循以下规则：
1. 分析用户需求，提取任务名称、任务描述和最适合的 Agent 类型。
2. 必须返回合法 JSON 格式，不要包含任何 Markdown 代码块标记或解释性文字。
3. JSON 必须包含以下字段：
   - task_name: 任务名称，简短明确（不超过 50 个字符）
   - description: 任务描述，说明任务目标、范围和交付物（不超过 200 个字符）
   - agent_type: Agent 类型，必须是以下值之一：{valid_types}
4. Agent 类型判定依据：
   - code：编程开发类（写代码、改 Bug、搭项目、部署、接口调试等）；
   - office：办公类（写 Word 文档、做 PPT、联网查资料并汇总、写报告/方案/调研等
     以文档交付为主的任务）；无法明确归类为编程时，优先考虑 office；
   - design/copywriting/video：对应设计、文案、视频类任务（当前暂无节点，谨慎选择）。
5. 如果需求不够明确，请基于已有信息给出最合理的推测，不要反问用户。
6. 返回的 JSON 必须可以直接被 Python json.loads 解析。"""


class TaskPlanResult(BaseModel):
    """任务规划结果模型。"""

    task_name: str = Field(..., description="任务名称")
    description: str = Field(..., description="任务描述")
    agent_type: str = Field(..., description="Agent 类型")


class TaskPlanningAgent:
    """基于 DeepSeek 的任务规划 Agent。"""

    def __init__(
        self,
        session: AsyncSession,
        deepseek_client: DeepSeekClient | None = None,
    ) -> None:
        """初始化任务规划 Agent。

        Args:
            session: 异步数据库会话，用于读取当前 LLM 配置。
            deepseek_client: DeepSeek 客户端实例，未提供时从数据库配置创建。
        """
        self._session = session
        self._client = deepseek_client
        self._system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            valid_types=", ".join(sorted(VALID_AGENT_TYPES)),
        )

    async def _get_client(self) -> DeepSeekClient:
        """获取或创建 DeepSeek 客户端。"""
        if self._client is None:
            config = await LLMProviderService().get_active_provider(self._session)
            self._client = DeepSeekClient(config, scene="planning")
        return self._client

    async def plan(self, requirement: str) -> TaskPlanResult:
        """将用户需求解析为结构化任务计划。

        Args:
            requirement: 用户输入的自然语言需求。

        Returns:
            解析后的任务规划结果。

        Raises:
            TaskPlanningError: 当返回字段缺失、agent_type 无效或 JSON 解析失败时抛出。
            DeepSeekAPIError: 当 DeepSeek API 调用失败时原样抛出。
        """
        if not requirement or not requirement.strip():
            raise TaskPlanningError("需求不能为空")

        logger.info(
            "task_planning_started",
            extra={"requirement_length": len(requirement)},
        )

        client = await self._get_client()
        try:
            raw_content = await client.chat_completion(
                system_prompt=self._system_prompt,
                user_prompt=requirement,
            )
        except DeepSeekAPIError as exc:
            logger.warning(
                "deepseek_api_error",
                extra={
                    "error_code": type(exc).__name__,
                    "reason": exc.message,
                    "status_code": exc.status_code,
                },
                exc_info=True,
            )
            raise

        try:
            parsed_data = self._extract_json(raw_content)
        except json.JSONDecodeError as exc:
            logger.warning(
                "task_planning_json_parse_failed",
                extra={
                    "error_code": type(exc).__name__,
                    "reason": str(exc),
                    "content_snippet": raw_content[:200],
                },
                exc_info=True,
            )
            raise TaskPlanningError(f"返回内容不是合法 JSON: {exc}") from exc

        return self._build_result(parsed_data, requirement)

    def _extract_json(self, content: str) -> dict:
        """从 DeepSeek 返回内容中提取并解析 JSON。

        Args:
            content: DeepSeek 返回的原始文本。

        Returns:
            解析后的字典。

        Raises:
            json.JSONDecodeError: 当内容无法解析为 JSON 时抛出。
        """
        content = content.strip()

        code_block_match = re.search(
            r"```(?:json)?\s*([\s\S]*?)\s*```",
            content,
        )
        if code_block_match:
            content = code_block_match.group(1).strip()

        return json.loads(content)

    def _build_result(self, data: dict, requirement: str) -> TaskPlanResult:
        """根据解析后的数据构建任务规划结果。

        Args:
            data: 解析后的 JSON 字典。
            requirement: 原始用户需求，用于补充默认描述。

        Returns:
            校验通过的任务规划结果。

        Raises:
            TaskPlanningError: 当必要字段缺失或 agent_type 无效时抛出。
        """
        task_name = data.get("task_name")
        if not task_name or not str(task_name).strip():
            raise TaskPlanningError("返回结果缺少 task_name 字段")

        agent_type = data.get("agent_type")
        if not agent_type:
            raise TaskPlanningError("返回结果缺少 agent_type 字段")
        agent_type = str(agent_type).strip().lower()
        agent_type = _AGENT_TYPE_ALIASES.get(agent_type, agent_type)
        if agent_type not in VALID_AGENT_TYPES:
            raise TaskPlanningError(
                f"未知的 agent_type: {agent_type}，有效值为: {', '.join(sorted(VALID_AGENT_TYPES))}"
            )

        description = data.get("description")
        if not description or not str(description).strip():
            description = requirement
        else:
            description = str(description).strip()

        result = TaskPlanResult(
            task_name=str(task_name).strip(),
            description=description,
            agent_type=agent_type,
        )
        logger.info(
            "task_planning_completed",
            extra={
                "task_name": result.task_name,
                "agent_type": result.agent_type,
            },
        )
        return result
