/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：本组件为任务创建页
   右侧面板的布局编排层，聚合交付物 / 文档 / 摘要 / 工具空态等子面板的切换逻辑，
   自身仅做编排不含业务，故整体豁免。 */
import { useState } from 'react';
import {
  CloseOutlined,
  FolderOpenOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import { App, Button, Dropdown, Tooltip, Typography } from 'antd';
import { TaskDetailArtifacts } from '@/components/TaskDetailArtifacts';
import { TaskNewDocPanel } from '@/components/TaskNewDocPanel';
import { TaskSummary } from '@/components/TaskSummary';
import { OrchestratorTodoSection } from '@/components/TaskSummaryTodoSections';
import { TaskToolEmpty } from '@/components/TaskToolEmpty';
import { TOOLS, toolMeta } from '@/components/TaskNewToolDefs';
import type { ToolKey } from '@/components/TaskNewToolDefs';
import type { OrchestratorTodo, Task, TaskInsight } from '@/types/task';
import type { TaskSpecification } from '@/types/taskBuilding';
import { COLORS } from '@/theme/tokens';
import { isDesktopShell, pickWorkspaceFolder } from '@/utils/desktopShell';

const { Text } = Typography;

interface TaskNewRightPanelProps {
  /** 面板是否处于折叠态（折叠时仅渲染展开按钮） */
  collapsed: boolean;
  /** 面板宽度（像素），折叠时不生效 */
  width: number;
  onExpand: () => void;
  onCollapse: () => void;
  /** 已打开的工具（按打开顺序，标签栏展示） */
  openTools: ToolKey[];
  /** 当前激活的工具（null 表示显示空态入口列表） */
  activeTool: ToolKey | null;
  /** 打开工具（已在列表中则仅激活） */
  onOpenTool: (key: ToolKey) => void;
  /** 激活工具 */
  onActivateTool: (key: ToolKey) => void;
  /** 关闭工具 */
  onCloseTool: (key: ToolKey) => void;
  /** 任务工作目录（ADR-028）：仅通过系统目录选择，不再手动输入 */
  workspaceDir?: string;
  onWorkspaceDirChange?: (dir: string) => void;
  /** 迭代模式下禁用目录切换 */
  workspaceDirDisabled?: boolean;
  /** 跟踪中的任务详情与加载态（任务摘要 + 方案工具） */
  trackedTask: Task | undefined;
  taskLoading: boolean;
  /** 中枢编排器自己的待办（ADR-061 M5d，摘要下段；缺省为空） */
  orchestratorTodos?: OrchestratorTodo[];
  /** 验收阻断决策回调（ADR-061 M4）：accept/retry */
  onDecideVerify?: (decision: 'accept' | 'retry') => void | Promise<void>;
  /** 向节点会话发言（ADR-061 M5a-steer） */
  onSteer?: (text: string) => void | Promise<void>;
  /** 任务只读洞察（ADR-061 M5c） */
  insight?: TaskInsight;

  /** 三件套编辑与动作（透传文档工具视图） */
  specification: TaskSpecification;
  isReady: boolean;
  creating: boolean;
  reviewing: boolean;
  onDocChange: (key: keyof TaskSpecification, value: string) => void;
  onCreate: () => void;
  onApprovePlan: () => void;
  onRejectPlan: (feedback: string) => void;
  onContinueIteration: () => void;
  /** 取消跟踪中的任务（问题修复：执行中可打断子节点开发） */
  onCancelTask?: () => Promise<void>;
}

/**
 * 任务创建页右侧工具面板。
 *
 * 空态展示工具入口列表（从这里开始），点击入口在面板中打开对应内容；
 * 已打开内容以标签页共存，可单独关闭。面板顶部常驻工作目录选择行。
 */
export function TaskNewRightPanel({
  collapsed,
  width,
  openTools,
  activeTool,
  onOpenTool,
  onActivateTool,
  onCloseTool,
  workspaceDir,
  onWorkspaceDirChange,
  workspaceDirDisabled,
  trackedTask,
  taskLoading,
  orchestratorTodos,
  onDecideVerify,
  insight,
  onSteer,
  specification,
  isReady,
  creating,
  reviewing,
  onDocChange,
  onCreate,
  onApprovePlan,
  onRejectPlan,
  onContinueIteration,
  onCancelTask,
}: TaskNewRightPanelProps) {
  const { message } = App.useApp();
  // 桌面壳检测在渲染期完成一次：浏览器环境点击目录按钮时给出明确提示
  const [desktop] = useState(() => isDesktopShell());
  // 「+」下拉可继续打开的工具（未被打开的）
  const unopenedTools = TOOLS.filter((tool) => !openTools.includes(tool.key));

  /** 点击文件夹图标：桌面壳调起系统目录选择；浏览器环境提示不支持。 */
  const handlePickWorkspaceDir = async () => {
    if (workspaceDirDisabled) {
      return;
    }
    if (!desktop) {
      message.info('浏览器环境无法打开系统目录选择：请使用桌面版中枢');
      return;
    }
    try {
      const selected = await pickWorkspaceFolder();
      if (selected) {
        onWorkspaceDirChange?.(selected);
      }
    } catch (err) {
      message.warning(err instanceof Error ? err.message : '目录选择失败');
    }
  };

  const workspaceRow = (
    <div className="task-new__workspace-row" data-testid="workspace-dir-button">
      <Tooltip title={workspaceDir || '选择工作目录'}>
        <Button
          type="text"
          size="small"
          icon={<FolderOpenOutlined />}
          onClick={() => void handlePickWorkspaceDir()}
          disabled={workspaceDirDisabled}
          aria-label="选择工作目录"
        />
      </Tooltip>
      <Text
        className="task-new__workspace-dir"
        ellipsis={{ tooltip: workspaceDir }}
        style={{ color: COLORS.textSecondary }}
      >
        {workspaceDir || '选择工作目录'}
      </Text>
    </div>
  );

  return (
    <div
      className={`task-new__right${collapsed ? ' task-new__right--collapsed' : ''}`}
      style={collapsed ? undefined : { width }}
    >
      {!collapsed && (
        <>
          {workspaceRow}
          {openTools.length === 0 ? (
            <TaskToolEmpty onOpenTool={onOpenTool} />
          ) : (
            <div className="task-new__tool-panel">
              <div className="task-new__tool-tabs" data-testid="tool-tabs">
                {openTools.map((key) => (
                  <button
                    key={key}
                    type="button"
                    className={`task-new__tool-tab${activeTool === key ? ' task-new__tool-tab--active' : ''}`}
                    onClick={() => onActivateTool(key)}
                  >
                    <span>{toolMeta(key).title}</span>
                    <CloseOutlined
                      className="task-new__tool-tab-close"
                      onClick={(e) => {
                        e.stopPropagation();
                        onCloseTool(key);
                      }}
                      aria-label={`关闭 ${toolMeta(key).title}`}
                    />
                  </button>
                ))}
                {unopenedTools.length > 0 && (
                  <Dropdown
                    menu={{
                      items: unopenedTools.map((tool) => ({
                        key: tool.key,
                        label: (
                          <span className="task-new__tool-menu-item">
                            <span className="task-new__tool-menu-icon">{tool.icon}</span>
                            {tool.title}
                          </span>
                        ),
                      })),
                      onClick: ({ key }) => onOpenTool(key as ToolKey),
                    }}
                    trigger={['click']}
                  >
                    <Button
                      type="text"
                      size="small"
                      icon={<PlusOutlined />}
                      aria-label="打开更多工具"
                      data-testid="tool-add-button"
                    />
                  </Dropdown>
                )}
              </div>
              {activeTool === 'summary'
                && (trackedTask ? (
                  <TaskSummary
                    task={trackedTask}
                    loading={taskLoading}
                    orchestratorTodos={orchestratorTodos}
                    onDecideVerify={onDecideVerify}
                    insight={insight}
                    onSteer={onSteer}
                  />
                ) : orchestratorTodos.length > 0 ? (
                  // ADR-067 待办先行：任务创建前待办挂会话（TaskNew 会话阶段
                  // 轮询传入），有清单就先展示，不再被「暂无任务」空态遮挡；
                  // 复用 task-summary 容器样式（padding/边框/内部滚动），与
                  // 任务阶段观感一致
                  <div className="task-summary">
                    <OrchestratorTodoSection todos={orchestratorTodos} />
                  </div>
                ) : (
                  <div className="task-new__tool-placeholder" data-testid="summary-empty">
                    <Text type="secondary">暂无进行中的任务</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      创建任务后可在此查看执行进度与产物汇总
                    </Text>
                  </div>
                ))}
              {activeTool === 'artifacts'
                && (trackedTask ? (
                  <TaskDetailArtifacts taskId={trackedTask.id} open variant="panel" />
                ) : (
                  <div className="task-new__tool-placeholder" data-testid="artifacts-empty">
                    <Text type="secondary">暂无进行中的任务</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      创建任务后可在此浏览 Agent 交付的文件
                    </Text>
                  </div>
                ))}
              {activeTool && activeTool !== 'summary' && activeTool !== 'artifacts' && (
                <TaskNewDocPanel
                  specification={specification}
                  isReady={isReady}
                  creating={creating}
                  trackedTask={trackedTask}
                  taskLoading={taskLoading}
                  reviewing={reviewing}
                  activeTab={activeTool}
                  showTabs={false}
                  onTabChange={(key) => onActivateTool(key as ToolKey)}
                  onDocChange={onDocChange}
                  onCreate={onCreate}
                  onApprovePlan={onApprovePlan}
                  onRejectPlan={onRejectPlan}
                  onContinueIteration={onContinueIteration}
                  onCancelTask={onCancelTask}
                />
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
