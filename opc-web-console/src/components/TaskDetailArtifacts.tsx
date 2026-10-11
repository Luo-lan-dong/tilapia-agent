/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：产物资源管理器的
   工具条 / 文件树 / 分隔条 / 预览四个区块状态同源（文件清单 + 选中文件 + 视图模式 +
   树宽度），拆分会造成 props 透传膨胀；ADR-028 UI 迭代范围内保持单组件。 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { App, Button, Empty, Input, Spin, Tag, Tooltip, Tree, Typography } from 'antd';
import {
  CodeOutlined,
  CopyOutlined,
  DownloadOutlined,
  DownOutlined,
  EyeOutlined,
  FileImageOutlined,
  FileMarkdownOutlined,
  FileOutlined,
  FileTextOutlined,
  FileZipOutlined,
  FolderOutlined,
  ReloadOutlined,
  SearchOutlined,
  UpOutlined,
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import {
  downloadArtifactsUrl,
  fetchArtifactFile,
  fetchArtifactTree,
  type ArtifactTreeNode,
} from '@/api/tasks';
import { COLORS } from '@/theme/tokens';
import { renderMarkdown } from '@/components/Markdown';
import { useResizablePanel } from '@/hooks/useResizablePanel';
import './TaskDetailArtifacts.css';

const { Text } = Typography;

interface TaskDetailArtifactsProps {
  /** 任务 ID（打开抽屉时加载清单并轮询刷新） */
  taskId: string | null;
  open: boolean;
  /** 展示形态：drawer 任务详情抽屉（默认，固定最大高度）；panel 工具面板（填满可用高度） */
  variant?: 'drawer' | 'panel';
}

/** 清单轮询间隔（毫秒）：跟随 Agent 30s 上报周期减半 */
const TREE_POLL_INTERVAL_MS = 15000;
/** 树宽度范围与持久化键（panel / drawer 分别记忆） */
const TREE_WIDTH_MIN = 160;
const TREE_WIDTH_MAX = 560;

/** 可渲染 Markdown 的扩展名 */
const MARKDOWN_EXTENSIONS = ['.md', '.markdown'];

/** 代码/文本类扩展名（用文本图标而非通用文件图标） */
const TEXT_EXTENSIONS = [
  '.txt',
  '.json',
  '.jsonc',
  '.yaml',
  '.yml',
  '.toml',
  '.ini',
  '.env',
  '.csv',
  '.log',
  '.xml',
  '.sql',
];

/** 按扩展名返回树节点图标（视觉区分文件类型） */
function iconForFile(name: string) {
  const lowered = name.toLowerCase();
  if (MARKDOWN_EXTENSIONS.some((ext) => lowered.endsWith(ext))) {
    return <FileMarkdownOutlined />;
  }
  if (/\.(png|jpe?g|gif|webp|svg|bmp|ico)$/.test(lowered)) {
    return <FileImageOutlined />;
  }
  if (/\.(zip|tar|gz|tgz|7z|rar)$/.test(lowered)) {
    return <FileZipOutlined />;
  }
  if (TEXT_EXTENSIONS.some((ext) => lowered.endsWith(ext))) {
    return <FileTextOutlined />;
  }
  return <FileOutlined />;
}

/** 人类可读的文件大小 */
function formatSize(size: number | undefined): string {
  if (size === undefined || size === null || Number.isNaN(size)) {
    return '';
  }
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** 判断路径是否为 Markdown 文件 */
function isMarkdownPath(path: string): boolean {
  const lowered = path.toLowerCase();
  return MARKDOWN_EXTENSIONS.some((ext) => lowered.endsWith(ext));
}

/** 转义正则元字符（搜索关键字按字面量匹配） */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * 将扁平路径清单转换为 antd Tree 数据（按目录聚合、同层排序）。
 *
 * 文件节点标题包含大小与类型图标；`keyword` 命中时对文件名做高亮。
 */
function buildTreeData(files: ArtifactTreeNode[], keyword: string): { nodes: DataNode[]; keys: string[] } {
  interface Node {
    children: Map<string, Node>;
    size?: number;
  }
  const root: Node = { children: new Map() };
  for (const file of files) {
    let current = root;
    const parts = file.path.split('/');
    parts.forEach((part, index) => {
      if (!current.children.has(part)) {
        current.children.set(part, { children: new Map() });
      }
      current = current.children.get(part)!;
      if (index === parts.length - 1) {
        current.size = file.size;
      }
    });
  }

  const allKeys: string[] = [];
  const loweredKeyword = keyword.trim().toLowerCase();
  const hitPattern = loweredKeyword ? new RegExp(escapeRegExp(keyword.trim()), 'gi') : null;

  /** 标题：名字（+ 大小），关键字命中处高亮 */
  const renderTitle = (name: string, size?: number) => {
    const sizeText = formatSize(size);
    let nameNode: ReactNode = name;
    if (hitPattern && hitPattern.test(name)) {
      const segments = name.split(hitPattern);
      nameNode = (
        <>
          {segments.map((segment, index) =>
            segment.toLowerCase() === loweredKeyword ? (
              <span key={`hit-${index}`} className="artifacts__hit">
                {segment}
              </span>
            ) : (
              <span key={`seg-${index}`}>{segment}</span>
            ),
          )}
        </>
      );
    }
    return (
      <span className="artifacts__node">
        <span className="artifacts__node-name" title={name}>
          {nameNode}
        </span>
        {sizeText && <span className="artifacts__node-size">{sizeText}</span>}
      </span>
    );
  };

  const toDataNode = (name: string, node: Node, prefix: string): DataNode => {
    const key = prefix ? `${prefix}/${name}` : name;
    allKeys.push(key);
    if (node.children.size === 0) {
      return {
        key,
        title: renderTitle(name, node.size),
        icon: iconForFile(name),
        isLeaf: true,
      };
    }
    return {
      key,
      title: renderTitle(name),
      icon: <FolderOutlined />,
      children: [...node.children.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([childName, child]) => toDataNode(childName, child, key)),
    };
  };

  const nodes = [...root.children.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, node]) => toDataNode(name, node, ''));
  return { nodes, keys: allKeys };
}

/**
 * 任务产物资源管理器（ADR-028）。
 *
 * 展示 Agent 上报的产物文件树，支持单文件文本预览（Markdown 可切换渲染/源码）、
 * 目录展开收起、按名搜索与整目录打包下载；清单在打开期间按固定间隔轮询刷新。
 */
export function TaskDetailArtifacts({ taskId, open, variant = 'drawer' }: TaskDetailArtifactsProps) {
  const { message } = App.useApp();
  const [files, setFiles] = useState<ArtifactTreeNode[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [preview, setPreview] = useState<{ path: string; content: string } | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [keyword, setKeyword] = useState('');
  const [showSource, setShowSource] = useState(false);
  /** 受控展开键（null = 非受控，使用 antd 默认全收起） */
  const [expandedKeys, setExpandedKeys] = useState<React.Key[] | null>(null);

  const isPanel = variant === 'panel';
  // 树宽度可拖动并持久化（panel/drawer 分别记忆，互不干扰）
  const {
    width: treeWidth,
    isResizing,
    handleResizeStart,
  } = useResizablePanel(isPanel ? 240 : 220, {
    min: TREE_WIDTH_MIN,
    max: TREE_WIDTH_MAX,
    storageKey: `opc:artifacts-tree-width:${variant}`,
    // direction 语义=把手在树块的哪一侧：panel 树贴右缘（把手在树左缘，
    // 向左拖变宽）；drawer 树在左（把手在树右缘，向右拖变宽）
    direction: isPanel ? 'left' : 'right',
  });

  const loadTree = useCallback(async () => {
    if (!taskId) {
      return;
    }
    setLoading(true);
    try {
      const tree = await fetchArtifactTree(taskId);
      setFiles(tree.files ?? []);
      setTruncated(Boolean(tree.truncated));
    } catch {
      // 清单拉取失败保持现有展示（可能为 Agent 离线），不打断浏览
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  // 渲染期状态调整（React 官方推荐模式）：任务切换/关闭时清空旧数据，
  // 替代 effect 内同步 setState（react-hooks/set-state-in-effect 禁止）
  const currentKey = open && taskId ? taskId : null;
  const [prevKey, setPrevKey] = useState<string | null>(null);
  if (currentKey !== prevKey) {
    setPrevKey(currentKey);
    setFiles([]);
    setPreview(null);
    setExpandedKeys(null);
  }

  useEffect(() => {
    if (!currentKey) {
      return undefined;
    }
    // 首帧加载放入宏任务：避免 effect 同步调用链上的 setState（react-hooks 规则）
    const initial = setTimeout(() => void loadTree(), 0);
    const timer = setInterval(() => void loadTree(), TREE_POLL_INTERVAL_MS);
    return () => {
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, [currentKey, loadTree]);

  const { nodes: treeData, keys: allTreeKeys } = useMemo(
    () => buildTreeData(files, keyword),
    [files, keyword],
  );

  /** 搜索过滤后的可见文件数（用于工具条展示） */
  const visibleCount = useMemo(() => {
    const lowered = keyword.trim().toLowerCase();
    if (!lowered) {
      return files.length;
    }
    return files.filter((file) => file.path.toLowerCase().includes(lowered)).length;
  }, [files, keyword]);

  const handleSelectFile = async (key: unknown) => {
    if (!taskId || typeof key !== 'string') {
      return;
    }
    setPreviewLoading(true);
    try {
      const file = await fetchArtifactFile(taskId, key);
      // Markdown 默认渲染视图；每次选中新文件重置为默认
      setShowSource(false);
      if (file.binary) {
        setPreview({ path: key, content: '（二进制文件，不支持预览，可通过打包下载获取）' });
      } else {
        setPreview({ path: key, content: file.content ?? '' });
      }
    } catch (err) {
      message.error(err instanceof Error ? err.message : '文件预览失败');
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleDownload = () => {
    if (!taskId) {
      return;
    }
    setDownloading(true);
    // 打包由后端流式返回，浏览器原生处理下载进度
    window.location.href = downloadArtifactsUrl(taskId);
    setTimeout(() => setDownloading(false), 3000);
  };

  /** 复制当前预览内容到剪贴板 */
  const handleCopyContent = async () => {
    if (!preview) {
      return;
    }
    try {
      await navigator.clipboard.writeText(preview.content);
      message.success('已复制文件内容');
    } catch {
      message.warning('复制失败：浏览器未授权剪贴板');
    }
  };

  /** 复制当前文件路径 */
  const handleCopyPath = async () => {
    if (!preview) {
      return;
    }
    try {
      await navigator.clipboard.writeText(preview.path);
      message.success('已复制文件路径');
    } catch {
      message.warning('复制失败：浏览器未授权剪贴板');
    }
  };

  const previewIsMarkdown = preview ? isMarkdownPath(preview.path) : false;
  const previewIsBinary = preview?.content.startsWith('（二进制文件') ?? false;

  // 文件树区块（panel 形态贴面板最右侧；宽度可拖动）
  const treeBlock = (
    <div className="artifacts__tree" style={{ width: treeWidth, flex: `0 0 ${treeWidth}px` }}>
      <Tree
        showIcon
        treeData={treeData}
        selectable
        {...(expandedKeys ? { expandedKeys, onExpand: (keys) => setExpandedKeys([...keys]) } : {})}
        onSelect={(keys) => {
          const key = keys[0];
          const isFile = files.some((item) => item.path === key);
          if (isFile) {
            void handleSelectFile(key);
          }
        }}
        data-testid="artifacts-tree"
      />
    </div>
  );

  // 预览区块（panel 形态在左；drawer 形态在右）
  const previewBlock = (
    <div className="artifacts__preview">
      {previewLoading ? (
        <Spin />
      ) : preview ? (
        <>
          <div className="artifacts__preview-head">
            <Tag color="green" className="artifacts__preview-path" title={preview.path}>
              {preview.path}
            </Tag>
            <div className="artifacts__toolbar-actions">
              {previewIsMarkdown && !previewIsBinary && (
                <Tooltip title={showSource ? '切换到渲染视图' : '切换到源码视图'}>
                  <Button
                    size="small"
                    type="text"
                    icon={showSource ? <EyeOutlined /> : <CodeOutlined />}
                    onClick={() => setShowSource((prev) => !prev)}
                    data-testid="artifacts-view-toggle"
                  >
                    {showSource ? '渲染' : '源码'}
                  </Button>
                </Tooltip>
              )}
              <Tooltip title="复制文件路径">
                <Button
                  size="small"
                  type="text"
                  icon={<CopyOutlined />}
                  onClick={() => void handleCopyPath()}
                  aria-label="复制文件路径"
                />
              </Tooltip>
              <Tooltip title="复制文件内容">
                <Button
                  size="small"
                  type="text"
                  icon={<CopyOutlined />}
                  onClick={() => void handleCopyContent()}
                  aria-label="复制文件内容"
                />
              </Tooltip>
            </div>
          </div>
          {previewIsMarkdown && !showSource && !previewIsBinary ? (
            <div data-testid="artifacts-preview">{renderMarkdown(preview.content)}</div>
          ) : (
            <pre className="artifacts__source" data-testid="artifacts-preview">
              {preview.content.split('\n').map((line, index) => (
                // 行号视图：纯文本文件按行渲染，便于定位
                <div className="artifacts__source-line" key={`ln-${index}`}>
                  <span className="artifacts__source-no">{index + 1}</span>
                  <span className="artifacts__source-text">{line || ' '}</span>
                </div>
              ))}
            </pre>
          )}
        </>
      ) : (
        <div className="artifacts__preview-empty">
          <FileTextOutlined style={{ fontSize: 32, opacity: 0.3 }} />
          <Text style={{ color: COLORS.textSecondary }}>
            点击{isPanel ? '右侧' : '左侧'}文件查看内容
          </Text>
          <Text style={{ color: COLORS.textSecondary, fontSize: 12 }}>
            Markdown 文件默认渲染展示，可切换源码视图；支持复制路径与内容
          </Text>
        </div>
      )}
    </div>
  );

  return (
    <div className="artifacts">
      <div className="artifacts__toolbar">
        <div className="artifacts__toolbar-meta">
          <Text style={{ color: COLORS.textSecondary }}>
            共 {visibleCount} 个文件
            {keyword.trim() && visibleCount !== files.length ? ` / ${files.length}` : ''}
            {truncated ? '（清单已截断）' : ''}
          </Text>
          {loading && <Spin size="small" />}
        </div>
        <div className="artifacts__toolbar-actions">
          <Input
            size="small"
            allowClear
            prefix={<SearchOutlined />}
            placeholder="按文件名筛选"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            style={{ width: 150 }}
            data-testid="artifacts-search"
          />
          <Tooltip title="展开全部目录">
            <Button
              size="small"
              type="text"
              icon={<DownOutlined />}
              onClick={() => setExpandedKeys([...allTreeKeys])}
              aria-label="展开全部目录"
            />
          </Tooltip>
          <Tooltip title="收起全部目录">
            <Button
              size="small"
              type="text"
              icon={<UpOutlined />}
              onClick={() => setExpandedKeys([])}
              aria-label="收起全部目录"
            />
          </Tooltip>
          <Button
            size="small"
            icon={<DownloadOutlined />}
            loading={downloading}
            onClick={handleDownload}
            data-testid="artifacts-download-button"
          >
            打包下载
          </Button>
        </div>
      </div>

      {files.length === 0 && !loading ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Text style={{ color: COLORS.textSecondary }}>
              暂无产物清单；任务执行中会自动出现，Agent 离线时不可用
            </Text>
          }
        />
      ) : (
        // panel 形态：预览在左、树在右贴面板最右缘（设计稿）；drawer 形态：树左 / 预览右
        <div className={`artifacts__browse${isPanel ? '' : ' artifacts__browse--drawer'}`}>
          {isPanel ? (
            <>
              {previewBlock}
              <div
                className={`artifacts__splitter${isResizing ? ' artifacts__splitter--active' : ''}`}
                onMouseDown={handleResizeStart}
                role="separator"
                aria-orientation="vertical"
                aria-label="调整文件树宽度"
                data-testid="artifacts-splitter"
              />
              {treeBlock}
            </>
          ) : (
            <>
              {treeBlock}
              <div
                className={`artifacts__splitter${isResizing ? ' artifacts__splitter--active' : ''}`}
                onMouseDown={handleResizeStart}
                role="separator"
                aria-orientation="vertical"
                aria-label="调整文件树宽度"
                data-testid="artifacts-splitter"
              />
              {previewBlock}
            </>
          )}
        </div>
      )}

      {/* 面板底部刷新入口（设计稿：底部居中） */}
      <div style={{ display: 'flex', justifyContent: isPanel ? 'center' : 'flex-start' }}>
        <Button size="small" icon={<ReloadOutlined />} onClick={() => void loadTree()}>
          刷新清单
        </Button>
      </div>
    </div>
  );
}
