"""节点 Agent 会话轨迹镜像 Pydantic Schema（ADR-063 M1）。

端点只做**结构校验**：``kind`` 是否在白名单内、``(task_id, seq)`` 是否重复、
单行是否超 8 KB 等都是「数据问题」，一律由
``app.services.task_agent_trace_service`` 丢弃并计入 ``ignored``——不在请求体
校验层报 4xx，避免节点侧因个别坏行整批上报失败。
"""

import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: 单批上报轨迹行数上限（超过由请求体校验直接 422，属于结构性错误）
AGENT_TRACE_MAX_ROWS_PER_APPEND = 200


class AgentTraceRow(BaseModel):
    """单条视图行（节点侧按 ``seq`` 递增上报，序内幂等）。"""

    seq: int = Field(ge=1, description="任务内单调序号，从 1 开始（幂等键的一部分）")
    kind: str = Field(
        description="视图行类型（status/reasoning/text/tool/todo/usage），非法值由服务层丢弃"
    )
    payload: dict = Field(description="视图行内容（JSON 对象，服务层负责截断与落库）")


class AgentTraceAppendRequest(BaseModel):
    """批量上报请求体（同一批次内重复 seq 也算 ignored，不报错）。"""

    node_id: str | None = Field(None, description="执行节点 ID（来源标注，可为空）")
    session_id: str | None = Field(None, description="节点侧会话 ID（可为空）")
    rows: list[AgentTraceRow] = Field(
        ...,
        max_length=AGENT_TRACE_MAX_ROWS_PER_APPEND,
        description=f"轨迹行列表，单批最多 {AGENT_TRACE_MAX_ROWS_PER_APPEND} 条",
    )


class AgentTraceAppendResponse(BaseModel):
    """批量上报结果：``accepted`` 实际落库行数，``ignored`` 被丢弃/幂等跳过行数。"""

    accepted: int = Field(description="实际落库的行数")
    ignored: int = Field(description="被忽略的行数（非法 kind/seq、重复 seq、超上限等）")


class AgentTraceRowOut(BaseModel):
    """读接口返回的单条轨迹行（``from_attributes`` 风格，与服务层 dict 对齐）。"""

    model_config = ConfigDict(from_attributes=True)

    seq: int = Field(description="任务内单调序号")
    kind: str = Field(description="视图行类型")
    payload: dict = Field(description="视图行内容（已反序列化）")
    created_at: datetime.datetime = Field(description="落库时间")

    @field_validator("payload", mode="before")
    @classmethod
    def _coerce_payload(cls, value: Any) -> Any:
        """坏行兜底：payload 非对象时包成 ``{"raw": ...}``。

        服务层对超限/异常内容会退化为字符串（保证仍是合法 JSON），读接口
        不应因此 500（服务层注释「读接口不因坏行 500」），故此处归一为对象。
        """
        return value if isinstance(value, dict) else {"raw": value}


class AgentTraceListResponse(BaseModel):
    """轨迹读取响应：按 ``seq`` 升序的增量分页结果。"""

    rows: list[AgentTraceRowOut] = Field(description="轨迹行列表（seq 升序）")
    has_more: bool = Field(description="本次返回后是否还有 seq 更大的行")
