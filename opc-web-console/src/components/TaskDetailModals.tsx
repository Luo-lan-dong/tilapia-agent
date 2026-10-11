import { useEffect, useState } from 'react';
import { Checkbox, Input, Modal, Select } from 'antd';
import { fetchAgentNodes } from '@/api/agentNodes';
import { fetchTaskEligibility, type TaskEligibilityResponse } from '@/api/tasks';
import { DispatchEligibilityHint } from '@/components/DispatchEligibilityHint';
import { COLORS } from '@/theme/tokens';

interface TaskDetailModalsProps {
  /** 任务 ID（拉取派发准入诊断，ADR-059 D3） */
  taskId?: string | null;
  /** 任务所需 Agent 类型（节点列表按此过滤，ADR-017 增强：手动派发选择具体节点） */
  agentType?: string;
  /** 驳回方案 Modal */
  rejectOpen: boolean;
  onCloseReject: () => void;
  feedback: string;
  onFeedbackChange: (value: string) => void;
  rejecting: boolean;
  onReject: () => void;
  /** 手动派发 Modal */
  manualOpen: boolean;
  onCloseManual: () => void;
  dispatching: boolean;
  onDispatch: (nodeId: string, force?: boolean) => void;
  dispatchTarget: string;
  onDispatchTargetChange: (value: string) => void;
}

/**
 * 任务详情抽屉的操作弹窗（纯展示）。
 *
 * 承载「驳回方案」与「手动派发」两个 Modal，从 TaskDetailDrawer
 * 拆出以控制主文件行数（coding-standards 4.3）。
 *
 * 手动派发 Modal 含 ADR-059 语义：默认**严格排队**（节点未达准入水位时
 * 任务等待该节点而非硬塞），仅当用户勾选「强行派送」才越过门槛。
 */
export function TaskDetailModals({
  taskId,
  agentType,
  rejectOpen,
  onCloseReject,
  feedback,
  onFeedbackChange,
  rejecting,
  onReject,
  manualOpen,
  onCloseManual,
  dispatching,
  onDispatch,
  dispatchTarget,
  onDispatchTargetChange,
}: TaskDetailModalsProps) {
  const [nodeOptions, setNodeOptions] = useState<{ value: string; label: string }[]>([]);
  const [eligibility, setEligibility] = useState<TaskEligibilityResponse | null>(null);
  const [force, setForce] = useState(false);

  // 打开手动派发 Modal 时拉取同类型节点列表（在线优先，ADR-017 增强）
  useEffect(() => {
    if (!manualOpen) {
      return;
    }
    let cancelled = false;
    fetchAgentNodes({ agent_type: agentType, limit: 100 })
      .then((list) => {
        if (cancelled) {
          return;
        }
        setNodeOptions(
          list.items.map((node) => ({
            value: node.id,
            label: `${node.name}（${node.status}${node.load != null ? ` · 负载 ${node.load}%` : ''}）`,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) {
          setNodeOptions([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [manualOpen, agentType]);

  // 打开时拉取准入诊断；每次打开重置「强行派送」勾选，避免上次的越权选择被沿用
  useEffect(() => {
    if (!manualOpen) {
      return;
    }
    setForce(false);
    if (!taskId) {
      return;
    }
    let cancelled = false;
    fetchTaskEligibility(taskId)
      .then((data) => {
        if (!cancelled) {
          setEligibility(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setEligibility(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [manualOpen, taskId]);

  return (
    <>
      <Modal
        title="驳回方案"
        open={rejectOpen}
        okText="提交驳回"
        cancelText="取消"
        okButtonProps={{ disabled: !feedback.trim(), loading: rejecting }}
        onOk={onReject}
        onCancel={onCloseReject}
      >
        <Input.TextArea
          rows={4}
          value={feedback}
          onChange={(e) => onFeedbackChange(e.target.value)}
          placeholder="请填写驳回原因与修改期望（必填），例如：登录超时未处理，请补充重试逻辑"
        />
      </Modal>

      <Modal
        title="手动派发"
        open={manualOpen}
        okText={force ? '强行派送' : '确认派发'}
        cancelText="取消"
        okButtonProps={{ loading: dispatching, disabled: !dispatchTarget, danger: force }}
        onOk={() => onDispatch(dispatchTarget, force)}
        onCancel={onCloseManual}
      >
        <p style={{ color: COLORS.textPrimary }}>
          选择执行节点。节点达到准入水位时立即派送；否则任务**排队等待该节点**
          （每 10 秒复检，达标即派），不会改派到其他节点。
        </p>
        <div style={{ marginTop: 12, color: COLORS.textSecondary }}>目标节点</div>
        <Select
          value={dispatchTarget || undefined}
          onChange={onDispatchTargetChange}
          disabled={dispatching}
          options={nodeOptions}
          placeholder={nodeOptions.length ? '选择节点' : '暂无可用节点'}
          notFoundContent="暂无该类型节点"
          style={{ width: '100%', marginTop: 8 }}
          data-testid="manual-dispatch-node-select"
        />
        <DispatchEligibilityHint eligibility={eligibility} selectedNodeId={dispatchTarget} />
        <Checkbox
          checked={force}
          onChange={(e) => setForce(e.target.checked)}
          disabled={dispatching}
          style={{ marginTop: 12 }}
          data-testid="manual-dispatch-force-checkbox"
        >
          <span style={{ color: COLORS.danger }}>无视资源水位强行派送</span>
          <span style={{ color: COLORS.textSecondary }}>
            （仅在你明确要求时使用；会记入审计 forced_dispatch）
          </span>
        </Checkbox>
      </Modal>
    </>
  );
}
