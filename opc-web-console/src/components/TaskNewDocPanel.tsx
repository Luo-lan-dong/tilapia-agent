import { useState } from 'react';
import type { ReactNode } from 'react';
import {
  CheckCircleOutlined,
  EditOutlined,
  EyeOutlined,
  FileTextOutlined,
  OrderedListOutlined,
  SolutionOutlined,
} from '@ant-design/icons';
import { Button, Input, Tabs, Tooltip, Typography } from 'antd';
import { MarkdownView } from '@/components/MarkdownView';
import { PlanReviewPanel } from '@/components/PlanReviewPanel';
import type { Task } from '@/types/task';
import type { TaskSpecification } from '@/types/taskBuilding';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';

const { TextArea } = Input;
const { Text } = Typography;

/** 三件套文档的展示模式：默认渲染 Markdown，可切只读源码或进入编辑 */
type DocViewMode = 'rendered' | 'source' | 'edit';

/** 文档面板 Tab 键：三件套 + 方案（方案为只读审批视图，不入 specification） */
export type DocTabKey = keyof TaskSpecification | 'plan';

interface TaskNewDocPanelProps {
  specification: TaskSpecification;
  isReady: boolean;
  creating: boolean;
  /** 跟踪中的任务详情（方案 Tab 审批视图数据源） */
  trackedTask: Task | undefined;
  /** 任务详情加载中 */
  taskLoading: boolean;
  /** 审批请求进行中 */
  reviewing: boolean;
  activeTab: DocTabKey;
  /** 是否渲染内置 Tab 切换栏（工具面板模式由外层标签管理，传 false） */
  showTabs?: boolean;
  onTabChange: (key: DocTabKey) => void;
  onDocChange: (key: keyof TaskSpecification, value: string) => void;
  onCreate: () => void;
  onApprovePlan: () => void;
  onRejectPlan: (feedback: string) => void;
  onContinueIteration: () => void;
  /** 取消执行中任务（可选，方案 Tab 显示取消按钮） */
  onCancelTask?: () => Promise<void>;
}

const tabItems: { key: DocTabKey; label: string; icon: ReactNode }[] = [
  { key: 'spec', label: '规范文档', icon: <FileTextOutlined /> },
  { key: 'tasks', label: '任务清单', icon: <OrderedListOutlined /> },
  { key: 'checklist', label: '验收清单', icon: <CheckCircleOutlined /> },
  { key: 'plan', label: '方案', icon: <SolutionOutlined /> },
];

/**
 * 任务创建页右侧文档面板。
 *
 * spec / tasks / checklist 三个 Tab 为可编辑三件套；「方案」Tab 为任务
 * 跟踪与审批视图（方案全文、批准/驳回、本轮修改说明）。
 *
 * 面板默认展开，可通过顶部图标栏切换 Tab。
 */
export function TaskNewDocPanel({
  specification,
  isReady,
  creating,
  trackedTask,
  taskLoading,
  reviewing,
  activeTab,
  showTabs = true,
  onTabChange,
  onDocChange,
  onCreate,
  onApprovePlan,
  onRejectPlan,
  onContinueIteration,
  onCancelTask,
}: TaskNewDocPanelProps) {
  // 三件套默认渲染 Markdown（此前为可编辑 TextArea 纯文本）；编辑仍需保留，
  // 故提供「渲染 / 编辑」切换；源码视图由 MarkdownView 内部提供。
  const [viewMode, setViewMode] = useState<DocViewMode>('rendered');
  const [prevTab, setPrevTab] = useState<DocTabKey>(activeTab);
  // 切换文档 Tab 时回到渲染视图（渲染期状态调整，替代 effect 内 setState）
  if (activeTab !== prevTab) {
    setPrevTab(activeTab);
    setViewMode('rendered');
  }

  return (
    <div className="task-new__docs" data-testid="task-new-doc-panel">
      {showTabs && (
        <div className="task-new__docs-header">
          <Tabs
            activeKey={activeTab}
            onChange={(key) => onTabChange(key as DocTabKey)}
            items={tabItems.map(({ key, label }) => ({ key, label }))}
            className="task-new__tabs"
          />
          <div className="task-new__docs-controls">
            {tabItems.map(({ key, label, icon }) => (
              <Tooltip key={key} title={label} placement="bottom">
                <Button
                  type={activeTab === key ? 'primary' : 'text'}
                  icon={icon}
                  onClick={() => onTabChange(key)}
                  aria-label={label}
                />
              </Tooltip>
            ))}
          </div>
        </div>
      )}
      <>
        {activeTab === 'plan' ? (
          <div className="task-new__plan-review">
            <PlanReviewPanel
              task={trackedTask}
              loading={taskLoading}
              reviewing={reviewing}
              onApprove={onApprovePlan}
              onReject={onRejectPlan}
              onContinueIteration={onContinueIteration}
              onCancelTask={onCancelTask}
            />
          </div>
        ) : viewMode === 'edit' ? (
          <TextArea
            value={specification[activeTab]}
            onChange={(e) => onDocChange(activeTab, e.target.value)}
            placeholder="AI 生成的内容将展示在这里，你也可以直接编辑"
            className="task-new__editor"
          />
        ) : (
          <MarkdownView
            content={specification[activeTab]}
            resetKey={activeTab}
            showActions={false}
            className="task-new__doc-view"
            emptyText="AI 生成的内容将展示在这里"
            testId={`doc-content-${activeTab}`}
          />
        )}
        <div className="task-new__actions">
          {activeTab !== 'plan' && (
            <Tooltip title={viewMode === 'edit' ? '切换到渲染视图' : '编辑文档'}>
              <Button
                size="small"
                icon={viewMode === 'edit' ? <EyeOutlined /> : <EditOutlined />}
                onClick={() => setViewMode(viewMode === 'edit' ? 'rendered' : 'edit')}
                data-testid="doc-view-toggle"
              >
                {viewMode === 'edit' ? '预览' : '编辑'}
              </Button>
            </Tooltip>
          )}
          <Text style={{ color: COLORS.textSecondary }}>
            {isReady ? 'AI 已确认信息足够，可以创建任务' : '需求确认后此按钮将启用'}
          </Text>
          <Button
            type="primary"
            disabled={!isReady}
            loading={creating}
            onClick={onCreate}
            style={{ ...primaryButtonStyle }}
          >
            创建任务
          </Button>
        </div>
      </>
    </div>
  );
}
