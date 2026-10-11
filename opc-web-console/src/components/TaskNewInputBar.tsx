/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：输入区、构建模式选择
   与附件上传共享同一份输入草稿状态，拆分需跨文件同步草稿状态易引入不一致，故整体
   豁免。 */
import { useCallback, useEffect, useState } from 'react';
import { App, Button, Dropdown, Progress, Tag, Tooltip } from 'antd';
import {
  CheckOutlined,
  DownOutlined,
  SendOutlined,
} from '@ant-design/icons';
import { activateLLMModel, fetchLLMModels } from '@/api/llmProvider';
import type { LLMModelItem } from '@/types/llmProvider';
import type { TaskBuildMode, TaskBuildStreamContextUsage } from '@/types/taskBuilding';
import type { ExecutorModelSelection, TaskExecutionMode } from '@/types/task';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';
import { TaskExecutorModelSelect } from '@/components/TaskExecutorModelSelect';

/** 输入长度上限（与 TextArea maxLength 一致，自绘计数用） */
export const INPUT_MAX_LENGTH = 2000;

const CHAT_MODE_LABEL: Record<TaskBuildMode, string> = {
  fast: '快速',
  thinking: '思考',
};

const EXECUTION_MODE_LABEL: Record<TaskExecutionMode, string> = {
  build: '直接开发',
  plan: '先规划',
};

interface TaskNewInputBarProps {
  /** 当前对话模型（中枢使用中模型，ADR-030 热切换） */
  model: string;
  mode: TaskBuildMode;
  onModeChange: (mode: TaskBuildMode) => void;
  executionMode: TaskExecutionMode;
  onExecutionModeChange: (mode: TaskExecutionMode) => void;
  /** 子 Agent 执行模型选择（ADR-029/030） */
  executorModel?: ExecutorModelSelection;
  onExecutorModelChange?: (selection: ExecutorModelSelection) => void;
  /** 对话模型热切换成功后的回调（更新页面模型展示） */
  onChatModelActivated?: (model: string) => void;
  /** 上下文占用（ADR-041），null 表示本轮尚未收到计量 */
  contextUsage?: TaskBuildStreamContextUsage | null;
  /** 历史对话压缩进行中（ADR-041 增强）：圆环切换为压缩动画 */
  compacting?: boolean;
  /** 当前输入字数（自绘计数） */
  inputLength: number;
  sending: boolean;
  canSend: boolean;
  onSend: () => void;
  /** 中断当前 AI 回答（问题修复）：回答中按钮变停止 */
  onStop?: () => void;
}

/**
 * 输入卡片底栏（2026-09-13 布局改版，原顶部工具栏迁入）。
 *
 * 左侧依次：历史会话（时钟图标）、对话模型下拉（热切换）、模式下拉
 * （回答模式 快速/思考 + 执行模式 直接开发/先规划 合并）、子 Agent
 * 执行模型选择；右侧：字数计数 + 发送/「回答中」按钮。
 */
export function TaskNewInputBar({
  model,
  mode,
  onModeChange,
  executionMode,
  onExecutionModeChange,
  executorModel,
  onExecutorModelChange,
  onChatModelActivated,
  contextUsage,
  compacting,
  inputLength,
  sending,
  canSend,
  onSend,
  onStop,
}: TaskNewInputBarProps) {
  const { message } = App.useApp();
  // 对话模型下拉：已添加模型列表（ADR-030 热切换）
  const [chatModels, setChatModels] = useState<LLMModelItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchLLMModels()
      .then((items) => {
        if (!cancelled) {
          setChatModels(items);
        }
      })
      .catch(() => {
        // 加载失败静默：下拉仅展示当前模型不可切换
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** 切换对话模型（即切换中枢使用中模型，下一轮对话即生效）。 */
  const handleSwitchChatModel = useCallback(
    async (item: LLMModelItem) => {
      if (item.is_active) {
        return;
      }
      try {
        await activateLLMModel(item.id);
        message.success(`对话模型已切换为 ${item.model}，下一轮对话生效`);
        setChatModels((prev) =>
          prev.map((m) => ({ ...m, is_active: m.id === item.id })),
        );
        onChatModelActivated?.(item.model);
      } catch (err) {
        message.error(err instanceof Error ? err.message : '切换失败');
      }
    },
    [message, onChatModelActivated],
  );

  const modelMenuItems = chatModels.map((item) => ({
    key: item.id,
    label: (
      <span>
        {item.model}
        {item.is_active && (
          <Tag color="green" style={{ marginInlineStart: 6 }}>
            使用中
          </Tag>
        )}
      </span>
    ),
  }));

  const modeMenuItems = [
    {
      key: 'group-chat-mode',
      type: 'group' as const,
      label: '回答模式',
      children: (['fast', 'thinking'] as TaskBuildMode[]).map((value) => ({
        key: `mode:${value}`,
        label: (
          <span>
            {CHAT_MODE_LABEL[value] === mode && (
              <CheckOutlined style={{ color: COLORS.primary, marginInlineEnd: 6 }} />
            )}
            {CHAT_MODE_LABEL[value]}
          </span>
        ),
      })),
    },
    {
      key: 'group-execution-mode',
      type: 'group' as const,
      label: '执行模式',
      children: (['build', 'plan'] as TaskExecutionMode[]).map((value) => ({
        key: `exec:${value}`,
        label: (
          <span>
            {EXECUTION_MODE_LABEL[value] === EXECUTION_MODE_LABEL[executionMode] && (
              <CheckOutlined style={{ color: COLORS.primary, marginInlineEnd: 6 }} />
            )}
            {EXECUTION_MODE_LABEL[value]}
          </span>
        ),
      })),
    },
  ];

  return (
    <div className="task-new__input-bar">
      <div className="task-new__input-bar-left">
        <Dropdown
          menu={{
            items: modelMenuItems,
            onClick: ({ key }) => {
              const target = chatModels.find((m) => m.id === key);
              if (target) {
                void handleSwitchChatModel(target);
              }
            },
          }}
          trigger={['click']}
          disabled={chatModels.length <= 1}
        >
          <Button
            type="text"
            size="small"
            data-testid="current-model"
            icon={<DownOutlined style={{ fontSize: 10 }} />}
            iconPosition="end"
            style={{ paddingInline: 6, color: COLORS.textSecondary }}
          >
            <span className="task-new__model-name" data-testid="current-model-name">
              {model || '未连接'}
            </span>
          </Button>
        </Dropdown>
        <Dropdown
          menu={{
            items: modeMenuItems,
            onClick: ({ key }) => {
              if (key.startsWith('mode:')) {
                onModeChange(key.slice(5) as TaskBuildMode);
              } else if (key.startsWith('exec:')) {
                onExecutionModeChange(key.slice(5) as TaskExecutionMode);
              }
            },
          }}
          trigger={['click']}
        >
          <Button
            type="text"
            size="small"
            data-testid="mode-switch"
            icon={<DownOutlined style={{ fontSize: 10 }} />}
            iconPosition="end"
            style={{ paddingInline: 6, color: COLORS.textSecondary }}
          >
            <span style={{ color: COLORS.textPrimary }} data-testid="execution-mode-switch">
              {CHAT_MODE_LABEL[mode]} · {EXECUTION_MODE_LABEL[executionMode]}
            </span>
          </Button>
        </Dropdown>
        {executorModel && onExecutorModelChange && (
          <TaskExecutorModelSelect
            value={executorModel}
            onChange={onExecutorModelChange}
            hubModelName={model}
          />
        )}
      </div>
      <div className="task-new__input-bar-right">
        {contextUsage && contextUsage.percent !== null && (
          <Tooltip
            title={
              compacting
                ? '正在压缩历史对话…'
                : `上下文占用 ${contextUsage.used_tokens.toLocaleString()} / ${contextUsage.window_tokens.toLocaleString()} tokens`
            }
          >
            <span
              data-testid="context-usage-ring"
              style={{ display: 'inline-flex', alignItems: 'center', cursor: 'default' }}
            >
              {compacting && (
                <span
                  style={{
                    fontSize: 12,
                    color: COLORS.textSecondary,
                    marginInlineEnd: 6,
                  }}
                >
                  压缩中
                </span>
              )}
              <Progress
                type="circle"
                size={22}
                percent={Math.min(100, contextUsage.percent)}
                status={compacting ? 'active' : 'normal'}
                strokeColor={
                  contextUsage.percent >= 80
                    ? COLORS.danger
                    : contextUsage.percent >= 60
                      ? COLORS.warning
                      : COLORS.primary
                }
                format={() => `${Math.round(Math.min(100, contextUsage.percent!))}`}
              />
            </span>
          </Tooltip>
        )}
        <span className="task-new__char-count" data-testid="input-char-count">
          {inputLength}/{INPUT_MAX_LENGTH}
        </span>
        {sending ? (
          canSend ? (
            // Steering（ADR-045）：回复中发送 = 插话投递，按钮可点
            <Button
              type="primary"
              icon={<SendOutlined />}
              onClick={onSend}
              data-testid="send-button-steer"
              style={primaryButtonStyle}
              title="AI 回复中将插入这条消息（下个工具轮边界生效）"
            >
              插话
            </Button>
          ) : (
            // 中断（问题修复）：点击即断开 SSE，已生成内容保留
            <Button
              danger
              icon={<span className="task-new__stop-square" aria-hidden />}
              onClick={() => onStop?.()}
              data-testid="send-button-stop"
              style={primaryButtonStyle}
              title="停止生成（已生成内容保留）"
            >
              停止
            </Button>
          )
        ) : (
          <Button
            type="primary"
            icon={<SendOutlined />}
            disabled={!canSend}
            onClick={onSend}
            data-testid="send-button"
            style={primaryButtonStyle}
          >
            发送
          </Button>
        )}
      </div>
    </div>
  );
}
