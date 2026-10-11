/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：任务创建对话面板聚合
   消息流渲染（思考过程折叠 / 附件消息）与输入上传入口，状态同源于 chat 消息列表，
   拆分会导致上下文割裂与 props 透传膨胀，故整体豁免。 */
import { Button, Collapse, Dropdown, Image, Input, Progress, Typography, Upload } from 'antd';
import type { ClipboardEvent, ReactNode } from 'react';
import {
  CloseCircleFilled,
  CloseOutlined,
  FileZipOutlined,
  HistoryOutlined,
  NodeIndexOutlined,
  PlusOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import type {
  TaskBuildMode,
  ChatMessage,
  ChatMessageAttachment,
  TerminalApprovalRequest,
  UploadingAttachment,
} from '@/types/taskBuilding';
import type { AgentTraceRow } from '@/types/task';
import { COLORS } from '@/theme/tokens';
import { renderMarkdown } from '@/components/Markdown';
import { NodeTraceBlock } from '@/components/NodeTraceBlock';
import { SkillStepList } from '@/components/SkillStepList';
import { TaskNewCommandPalette } from '@/components/TaskNewCommandPalette';
import { INPUT_MAX_LENGTH } from '@/components/TaskNewInputBar';
import { sessionAttachmentPreviewUrl } from '@/api/attachments';
import { messageMentionNode } from '@/utils/chatMessage';

const { TextArea } = Input;
const { Text, Title } = Typography;

interface ThinkingCollapseProps {
  thinking: string;
}

/**
 * AI 思考过程折叠卡片（图三样式：带边框卡片 + 图标标题，默认收起，可展开）。
 */
function ThinkingCollapse({ thinking }: ThinkingCollapseProps) {
  return (
    <Collapse
      size="small"
      defaultActiveKey={[]}
      items={[
        {
          key: 'thinking',
          label: (
            <span className="task-new__thinking-label">
              <NodeIndexOutlined />
              思考过程
            </span>
          ),
          children: <div className="task-new__thinking">{thinking}</div>,
        },
      ]}
      className="task-new__thinking-collapse"
    />
  );
}

interface TaskNewChatPanelProps {
  messages: ChatMessage[];
  input: string;
  sending: boolean;
  mode: TaskBuildMode;
  /** 发送等待期阶段提示（如「正在编写规范文档…」），空串用默认文案 */
  stageText?: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  /** 打开历史会话抽屉 */
  onOpenSessions: () => void;
  /** 输入卡片底栏插槽（TaskNewInputBar，含模型/模式/发送等） */
  footer?: ReactNode;
  /** 当前会话 ID（ADR-039）：历史消息图片预览地址构造；新会话未建时为空 */
  sessionId?: string;
  /** 待发送图片附件（已上传成功） */
  pendingAttachments?: ChatMessageAttachment[];
  /** 上传中的附件（输入框内展示实时进度，ADR-039） */
  uploadingList?: UploadingAttachment[];
  /** 移除待发送附件 */
  onRemoveAttachment?: (id: string) => void;
  /** 取消上传中的附件 */
  onCancelUploading?: (key: string) => void;
  /** 选择/粘贴图片（File 列表，组件内过滤出图片类型） */
  onFilesPicked?: (files: File[]) => void;
  /** 终端命令审批请求（ADR-053，manual/ai_auto 模式 AI 执行前确认） */
  approvalRequest?: TerminalApprovalRequest | null;
  /** 审批决定回调（approve=true 批准 / false 拒绝） */
  onApprovalDecide?: (approve: boolean) => void;
  /** 节点轨迹分组（ADR-063 M3：每个节点一个折叠块，群聊式并列） */
  nodeTraceGroups?: NodeTraceGroup[];
  /** 轨迹回看加载中 */
  nodeTraceLoading?: boolean;
  /** 轨迹所属任务名（块头展示） */
  nodeTraceTaskName?: string;
  /** 任务进度（0-100，块头展示） */
  nodeTraceProgress?: number;
  /** 任一折叠块展开/收起（父层据此启停实时轮询，ADR「收起即停」） */
  onNodeTraceExpandChange?: (expanded: boolean) => void;
  /** `@节点` 候选（任务已派发节点）；空时隐藏 @ 按钮 */
  mentionCandidates?: NodeMentionCandidate[];
  /** 已选中的 @ 节点（输入区上方显示 chip） */
  mentionNode?: NodeMentionCandidate | null;
  /** 选中/移除 @ 节点 */
  onMentionPick?: (node: NodeMentionCandidate) => void;
  onMentionClear?: () => void;
}

/** 节点轨迹分组：同一 node_id 的行聚成一个折叠块 */
export interface NodeTraceGroup {
  nodeId: string | null;
  nodeName?: string;
  rows: AgentTraceRow[];
}

/** `@节点` 候选（ADR-063 M4）：当前任务链上的执行节点 */
export interface NodeMentionCandidate {
  id: string;
  name: string;
}

/**
 * 消息气泡内的附件展示行（ADR-039）：
 * 图片走缩略图（可点开预览）；zip 等非图片附件展示文件卡片。
 */
function MessageAttachments({
  message,
  sessionId,
}: {
  message: ChatMessage;
  sessionId?: string;
}) {
  const attachments = message.attachments ?? [];
  if (attachments.length === 0) {
    return null;
  }
  return (
    <div className="task-new__message-attachments">
      <Image.PreviewGroup>
        {attachments.map((item) =>
          item.mime.startsWith('image/') ? (
            <Image
              key={item.id}
              src={
                item.preview_url ??
                (sessionId ? sessionAttachmentPreviewUrl(sessionId, item.id) : undefined)
              }
              alt={item.file_name}
              width={88}
              height={88}
              style={{ objectFit: 'cover', borderRadius: 8 }}
              data-testid="message-attachment-image"
            />
          ) : (
            <div
              key={item.id}
              className="task-new__message-attachment-file"
              data-testid="message-attachment-file"
              title={item.file_name}
            >
              <FileZipOutlined />
              <span>{item.file_name}</span>
            </div>
          ),
        )}
      </Image.PreviewGroup>
    </div>
  );
}

/**
 * 任务创建页左侧聊天面板。
 *
 * 顶部右侧为显眼的「历史会话」入口（2026-09-13 二次改版从底栏上移）；
 * 输入卡片底栏（模型、模式、发送等）通过 footer 插槽由页面层组装注入。
 */
export function TaskNewChatPanel({
  messages,
  input,
  sending,
  mode,
  stageText,
  onInputChange,
  onSend,
  onOpenSessions,
  footer,
  sessionId,
  pendingAttachments = [],
  uploadingList = [],
  onRemoveAttachment,
  onCancelUploading,
  onFilesPicked,
  approvalRequest,
  onApprovalDecide,
  nodeTraceGroups = [],
  nodeTraceLoading = false,
  nodeTraceTaskName,
  nodeTraceProgress,
  onNodeTraceExpandChange,
  mentionCandidates = [],
  mentionNode = null,
  onMentionPick,
  onMentionClear,
}: TaskNewChatPanelProps) {
  /** 粘贴图片：从剪贴板提取图片文件交页面层上传（ADR-039） */
  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (!onFilesPicked) {
      return;
    }
    const files = Array.from(event.clipboardData?.files ?? []).filter((file) =>
      file.type.startsWith('image/'),
    );
    if (files.length > 0) {
      event.preventDefault();
      onFilesPicked(files);
    }
  };

  /** 文件选择：Upload 组件 beforeUpload 拦截，仅取图片与压缩包（ADR-039 P2；压缩包全格式） */
  const ARCHIVE_EXTENSIONS = /\.(zip|rar|7z|tar|gz|tgz|bz2|xz|txz)$/i;
  const isAllowedFile = (file: File) =>
    file.type.startsWith('image/') ||
    ARCHIVE_EXTENSIONS.test(file.name) ||
    /^application\/(zip|x-zip-compressed|vnd\.rar|x-rar-compressed|x-7z-compressed|gzip|x-gzip|x-bzip2|x-xz|x-tar)$/.test(
      file.type,
    );
  const handlePickFiles = (file: File): boolean => {
    if (isAllowedFile(file)) {
      onFilesPicked?.([file]);
    }
    return false; // 阻止 antd 自动上传，上传由页面层统一处理
  };

  // 工具轨迹消息（ADR-040）仅在会话持久化/恢复后出现，不直接渲染：
  // role:"tool" 的工具结果已由技能步骤卡片的 result_summary 呈现；
  // assistant 的 tool_calls 载体消息 content 为空，渲染会产生空气泡。
  // 状态层保留完整轨迹（下一轮回流 LLM 所需），仅展示层过滤。
  const visibleMessages = messages.filter((msg) => {
    if (msg.role === 'tool') {
      return false;
    }
    if (
      msg.role === 'assistant' &&
      !msg.content &&
      !msg.thinking &&
      !(msg.steps && msg.steps.length > 0)
    ) {
      return false;
    }
    return true;
  });

  return (
    <div className="task-new__chat">
      <div className="task-new__chat-header">
        <Button
          type="text"
          size="small"
          icon={<HistoryOutlined />}
          onClick={onOpenSessions}
          data-testid="history-button"
          className="task-new__history-button"
        >
          历史会话
        </Button>
      </div>
      <div className="task-new__messages">
        {messages.length === 0 && (
          <div className="task-new__welcome">
            <Title level={5} style={{ color: COLORS.textPrimary, marginTop: 0 }}>
              从一句话需求开始
            </Title>
            <Text style={{ color: COLORS.textSecondary }}>
              描述你想开发的功能，剩下的交给Tilapia
            </Text>
          </div>
        )}
        {visibleMessages.map((msg, index) => {
          // 连续 assistant 消息只在第一条显示"AI"标签（回合分割后不再反复出现）
          const showLabel = msg.role === 'user' || visibleMessages[index - 1]?.role !== 'assistant';
          const mentionSource = messageMentionNode(msg);
          return (
            <div key={index} className={`task-new__message task-new__message--${msg.role}`}>
              {showLabel && (
                <div className="task-new__message-label">
                  {msg.role === 'user' ? '我' : 'AI'}
                  {msg.queued && (
                    <span
                      className="task-new__queued-tag"
                      data-testid="queued-tag"
                      style={{ marginInlineStart: 6 }}
                    >
                      已排队
                    </span>
                  )}
                  {/* `@节点` 来源标注（ADR-063 D6，ADR-064 兼容 mention_node 直传）：
                      user 消息 = 定向发送标注 */}
                  {msg.role === 'user' && mentionSource && (
                    <span
                      className="task-new__mention-tag"
                      data-testid="mention-tag"
                      style={{ marginInlineStart: 6 }}
                    >
                      @{mentionSource}
                    </span>
                  )}
                  {/* 节点回复徽标（ADR-064 M3）：assistant+@节点 = 节点续聊回复，
                      复用 ADR-063 折叠块头节点徽标样式（node-trace__source-tag） */}
                  {msg.role === 'assistant' && mentionSource && (
                    <span
                      className="node-trace__source-tag"
                      data-testid="node-reply-badge"
                      style={{ marginInlineStart: 6 }}
                    >
                      @{mentionSource}
                    </span>
                  )}
                  {/* 发送失败标记（ADR-064 M3）：非 2xx 时气泡保留并显式报错 */}
                  {msg.failed && (
                    <span
                      className="task-new__failed-tag"
                      data-testid="message-failed-tag"
                      style={{ marginInlineStart: 6 }}
                    >
                      发送失败
                    </span>
                  )}
                </div>
              )}
              {msg.role === 'user' && (
                <MessageAttachments message={msg} sessionId={sessionId} />
              )}
              {mode === 'thinking' && msg.role === 'assistant' && msg.thinking && (
                <ThinkingCollapse thinking={msg.thinking} />
              )}
              {msg.role === 'assistant' && msg.steps && msg.steps.length > 0 && (
                <SkillStepList steps={msg.steps} />
              )}
              {/* 正文为空（仅有思考/步骤的回合）不渲染气泡，避免出现空白色块 */}
              {msg.content && (
                <div className="task-new__message-content">
                  {/* 用户消息为纯文本（可能是命令/路径，不应被 Markdown 改写）；
                      assistant 正文渲染 Markdown（模型输出 ###、**、代码块等） */}
                  {msg.role === 'assistant' ? renderMarkdown(msg.content) : msg.content}
                </div>
              )}
            </div>
          );
        })}
        {nodeTraceGroups.length > 0 && (
          <div className="task-new__node-traces" data-testid="node-trace-list">
            {nodeTraceGroups.map((group) => (
              <NodeTraceBlock
                key={group.nodeId ?? 'unknown'}
                nodeId={group.nodeId}
                nodeName={group.nodeName}
                taskName={nodeTraceTaskName}
                progress={nodeTraceProgress}
                rows={group.rows}
                loading={nodeTraceLoading}
                onExpandChange={onNodeTraceExpandChange}
              />
            ))}
          </div>
        )}
        {sending && messages[messages.length - 1]?.role === 'user' && (
          <div className="task-new__message task-new__message--assistant" data-testid="ai-thinking-bubble">
            <div className="task-new__message-label">AI</div>
            <div className="task-new__message-content">
              <span className="task-new__typing-dots" aria-label="AI 正在输入">
                <i />
                <i />
                <i />
              </span>
              <span className="task-new__stage-text">{stageText || 'AI 正在输入'}</span>
            </div>
          </div>
        )}
        {approvalRequest && (
          <div className="task-new__approval" data-testid="approval-card">
            <div className="task-new__approval-title">AI 请求执行终端命令</div>
            <code className="task-new__approval-command">{approvalRequest.command}</code>
            <div className="task-new__approval-actions">
              <Button size="small" type="primary" onClick={() => onApprovalDecide?.(true)}>
                批准执行
              </Button>
              <Button size="small" danger onClick={() => onApprovalDecide?.(false)}>
                拒绝
              </Button>
            </div>
          </div>
        )}
      </div>
      <div className="task-new__input">
        {input.startsWith('/') && !sending && (
          <TaskNewCommandPalette onSelect={onInputChange} />
        )}
        <div className="task-new__input-shell" data-testid="input-shell">
          {mentionNode && (
            <div className="task-new__mention-chip" data-testid="mention-node-chip">
              <TeamOutlined />
              <span>发给节点：{mentionNode.name}</span>
              <button
                type="button"
                className="task-new__mention-chip-remove"
                onClick={onMentionClear}
                aria-label={`取消 @ ${mentionNode.name}`}
              >
                <CloseOutlined style={{ fontSize: 10 }} />
              </button>
            </div>
          )}
          {(uploadingList.length > 0 || pendingAttachments.length > 0) && (
            <div className="task-new__pending-attachments" data-testid="pending-attachments">
              {uploadingList.map((item) => (
                <div
                  key={item.key}
                  className="task-new__pending-attachment task-new__pending-attachment--uploading"
                  data-testid="uploading-attachment"
                  title={`${item.file_name}（上传中 ${item.percent}%）`}
                >
                  {item.mime.startsWith('image/') && item.preview_url ? (
                    <img src={item.preview_url} alt={item.file_name} />
                  ) : (
                    <div className="task-new__pending-attachment-file">
                      <FileZipOutlined />
                      <span>{item.file_name}</span>
                    </div>
                  )}
                  <div className="task-new__uploading-mask">
                    <Progress type="circle" size={30} percent={item.percent} />
                  </div>
                  <button
                    type="button"
                    className="task-new__pending-attachment-remove"
                    onClick={() => onCancelUploading?.(item.key)}
                    aria-label={`取消上传 ${item.file_name}`}
                  >
                    <CloseCircleFilled />
                  </button>
                </div>
              ))}
              {pendingAttachments.map((item) => (
                <div key={item.id} className="task-new__pending-attachment">
                  {item.mime.startsWith('image/') && item.preview_url ? (
                    <img src={item.preview_url} alt={item.file_name} />
                  ) : (
                    <div className="task-new__pending-attachment-file" title={item.file_name}>
                      <FileZipOutlined />
                      <span>{item.file_name}</span>
                    </div>
                  )}
                  <button
                    type="button"
                    className="task-new__pending-attachment-remove"
                    onClick={() => onRemoveAttachment?.(item.id)}
                    aria-label={`移除附件 ${item.file_name}`}
                  >
                    <CloseCircleFilled />
                  </button>
                </div>
              ))}
            </div>
          )}
          <TextArea
            variant="borderless"
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
            onPaste={handlePaste}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault();
                onSend();
              }
            }}
            placeholder="描述你的任务需求，可粘贴图片或 zip（输入 / 可用命令）"
            autoSize={{ minRows: 2, maxRows: 6 }}
            maxLength={INPUT_MAX_LENGTH}
            className="task-new__input-textarea"
          />
          {/* 功能工具行内嵌输入框（2026-09-25 布局优化对齐设计稿）：
              左侧上传 + 模型/模式控件（footer 插槽），右侧计数 + 发送 */}
          {footer && (
            <div className="task-new__input-toolbar" data-testid="input-toolbar">
              <Upload
                accept="image/png,image/jpeg,image/webp,image/gif,.png,.jpg,.jpeg,.webp,.gif,.zip,.rar,.7z,.tar,.gz,.tgz,.bz2,.xz,.txz"
                showUploadList={false}
                beforeUpload={handlePickFiles}
                disabled={sending}
              >
                <Button
                  type="text"
                  size="small"
                  icon={<PlusOutlined />}
                  title="上传图片 / zip 压缩包（也可直接粘贴）"
                  data-testid="attachment-upload-button"
                  style={{ color: COLORS.textSecondary }}
                />
              </Upload>
              {mentionCandidates.length > 0 && (
                <Dropdown
                  menu={{
                    items: mentionCandidates.map((node) => ({
                      key: node.id,
                      label: `@${node.name}`,
                    })),
                    onClick: ({ key }) => {
                      const target = mentionCandidates.find((node) => node.id === key);
                      if (target) {
                        onMentionPick?.(target);
                      }
                    },
                  }}
                  trigger={['click']}
                >
                  <Button
                    type="text"
                    size="small"
                    icon={<TeamOutlined />}
                    title="@节点：把这段话直接发给节点 agent（执行中即补充发言，空闲即开新一轮）"
                    data-testid="mention-node-button"
                    style={{ color: COLORS.textSecondary }}
                  />
                </Dropdown>
              )}
              {footer}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
