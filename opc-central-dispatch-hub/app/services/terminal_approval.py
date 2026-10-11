"""终端命令审批注册表（ADR-053）。

manual / ai_auto(high|unknown) 模式下，run_command 技能创建审批项并在
asyncio.Event 上阻塞等待（技能事件流先发出 command_approval_request），
用户在前端确认卡点批准/拒绝 → POST /terminal/approvals/{id} 置位事件 →
技能继续执行。内存态即可（审批与技能执行同生命周期）。
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: 审批等待上限（秒）：须小于技能执行预算（ADR-043 300s），避免撞技能超时
APPROVAL_WAIT_SECONDS = 240


@dataclass
class PendingApproval:
    """一条待审批的终端命令。"""

    approval_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    command: str = ""
    mode: str = "manual"
    risk_level: str = "unknown"
    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool = False
    resolved: bool = False


class TerminalApprovalRegistry:
    """审批注册表（进程内单例）。"""

    def __init__(self) -> None:
        self._pending: dict[str, PendingApproval] = {}

    def create(self, session_id: str, command: str, mode: str, risk_level: str) -> PendingApproval:
        approval = PendingApproval(
            session_id=session_id,
            command=command,
            mode=mode,
            risk_level=risk_level,
        )
        self._pending[approval.approval_id] = approval
        return approval

    def resolve(self, approval_id: str, approved: bool) -> PendingApproval | None:
        """用户批准/拒绝：置位事件唤醒等待中的技能。"""
        approval = self._pending.get(approval_id)
        if approval is None or approval.resolved:
            return None
        approval.approved = approved
        approval.resolved = True
        approval.event.set()
        return approval

    async def wait(self, approval: PendingApproval) -> bool:
        """等待审批结果；超时按拒绝处理并清理。"""
        try:
            await asyncio.wait_for(approval.event.wait(), timeout=APPROVAL_WAIT_SECONDS)
        except TimeoutError:
            approval.resolved = True
            self._pending.pop(approval.approval_id, None)
            logger.warning(
                "terminal_approval_timeout",
                extra={"event": "terminal_approval_timeout", "approval_id": approval.approval_id},
            )
            return False
        self._pending.pop(approval.approval_id, None)
        return approval.approved


terminal_approval_registry = TerminalApprovalRegistry()
