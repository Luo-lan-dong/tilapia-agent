import { Descriptions, Tag, Typography } from 'antd';
import { MarkdownView } from '@/components/MarkdownView';
import { formatDateTime } from '@/utils/format';
import type { Task, TaskExecutionMode } from '@/types/task';
import { COLORS } from '@/theme/tokens';

const { Text } = Typography;

/** 执行模式展示文案映射 */
const EXECUTION_MODE_LABEL: Record<TaskExecutionMode, string> = {
  build: '直接开发',
  plan: '先规划',
};

const STATUS_LABEL_MAP: Record<string, string> = {
  pending: '待分配',
  assigned: '已分配',
  running: '执行中',
  completed: '已完成',
  failed: '失败',
  review: '待确认',
  // ADR-059 D5：节点离线且 AI 智能调度关闭 → 等用户确认改派
  requeue_pending: '待确认改派',
};

/** 执行阶段中文标签（ADR-055，与 TaskSummary 保持一致） */
const STAGE_LABEL_MAP: Record<string, string> = {
  preparing: '准备中',
  planning: '规划中',
  coding: '编写中',
  editing: '修改文件',
  running_command: '执行命令',
  searching: '检索代码',
  testing: '验证中',
  compacting: '压缩上下文',
  summarizing: '生成说明',
  idle: '空闲',
};

/**
 * 组合「当前动作」展示文本：阶段标签 + 动作描述（ADR-055）。
 *
 * 均未上报时展示占位符，避免出现空白单元格。
 */
function currentActionText(task: Task): string {
  const stage = task.progress_stage ? (STAGE_LABEL_MAP[task.progress_stage] ?? task.progress_stage) : '';
  const message = task.progress_message ?? '';
  if (stage && message) {
    return `${stage} · ${message}`;
  }
  return stage || message || '—';
}

function dispatchTag(type: 'auto' | 'manual' | undefined) {
  if (!type) {
    return <Text style={{ color: COLORS.textMuted }}>—</Text>;
  }
  return <Tag color={type === 'manual' ? 'green' : 'blue'}>{type === 'manual' ? '手动' : '自动'}</Tag>;
}

interface TaskDetailInfoProps {
  detail: Task;
}

/**
 * 任务详情信息区（纯展示）。
 *
 * 承载基础信息 Descriptions 与 plan 方案全文，从 TaskDetailDrawer
 * 拆出以控制主文件行数（coding-standards 4.3）。
 */
export function TaskDetailInfo({ detail }: TaskDetailInfoProps) {
  return (
    <>
      <Descriptions column={1} size="small" bordered
        items={[
          { key: 'title', label: '任务名称', children: detail.title },
          {
            key: 'status',
            label: '状态',
            children: (
              <Tag>{STATUS_LABEL_MAP[detail.status] ?? detail.status}</Tag>
            ),
          },
          { key: 'dispatch', label: '派发模式', children: dispatchTag(detail.dispatch_type) },
          {
            key: 'mode',
            label: '执行模式',
            children: detail.execution_mode
              ? EXECUTION_MODE_LABEL[detail.execution_mode]
              : '—',
          },
          { key: 'progress', label: '进度', children: `${detail.progress}%` },
          {
            key: 'current-action',
            label: '当前动作',
            children: currentActionText(detail),
          },
          {
            key: 'node',
            label: '分配节点',
            children: detail.assigned_node_name ?? '—',
          },
          // 排队可见（ADR-059 D3）：展示指定节点与排队原因（含缺口数值），
          // 把「排队中 0%」变成可解释的等待
          ...(detail.target_node_name || detail.target_node_id
            ? [
                {
                  key: 'target-node',
                  label: '指定节点',
                  children: (
                    <Text>
                      {detail.target_node_name ?? detail.target_node_id}
                      {detail.forced_dispatch ? (
                        <Tag color="red" style={{ marginInlineStart: 8 }}>
                          强行派送
                        </Tag>
                      ) : null}
                    </Text>
                  ),
                },
              ]
            : []),
          ...(detail.dispatch_hold_reason
            ? [
                {
                  key: 'hold-reason',
                  label: '排队原因',
                  children: (
                    <Text style={{ color: COLORS.textSecondary }}>
                      {detail.dispatch_hold_reason}
                    </Text>
                  ),
                },
              ]
            : []),
          ...(detail.last_reject_reason
            ? [
                {
                  key: 'reject-reason',
                  label: '最近拒单',
                  children: <Tag color="orange">{detail.last_reject_reason}</Tag>,
                },
              ]
            : []),
          { key: 'session', label: '会话 ID', children: detail.session_id ?? '—' },
          ...(detail.executor_model_actual
            ? [
                {
                  key: 'executor-model-actual',
                  label: '实际执行模型',
                  children: detail.executor_model_actual,
                },
              ]
            : []),
          { key: 'created', label: '创建时间', children: formatDateTime(detail.created_at) },
          ...(detail.change_brief
            ? [{ key: 'brief', label: '本轮修改说明', children: detail.change_brief }]
            : []),
        ]}
      />
      {detail.plan_content && (
        <div style={{ marginTop: 16 }}>
          <div style={{ color: COLORS.textSecondary, marginBottom: 8 }}>方案全文</div>
          {/* 方案全文渲染 Markdown（可切源码），替代原纯文本 pre */}
          <MarkdownView
            content={detail.plan_content}
            resetKey={detail.id}
            style={{
              maxHeight: 320,
              overflow: 'auto',
              padding: 12,
              borderRadius: 8,
              background: COLORS.bgBase,
              color: COLORS.textPrimary,
              fontSize: 13,
            }}
            testId="plan-content"
          />
        </div>
      )}
    </>
  );
}
