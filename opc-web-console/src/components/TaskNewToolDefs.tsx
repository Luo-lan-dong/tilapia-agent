import type { ReactNode } from 'react';
import {
  CheckCircleOutlined,
  FileTextOutlined,
  FolderOutlined,
  OrderedListOutlined,
  ProfileOutlined,
  SolutionOutlined,
} from '@ant-design/icons';
import type { DocTabKey } from '@/components/TaskNewDocPanel';

/** 工具面板工具键：文档三件套 + 方案 + 任务摘要 + 产物 */
export type ToolKey = DocTabKey | 'summary' | 'artifacts';

export interface ToolMeta {
  key: ToolKey;
  title: string;
  description: string;
  icon: ReactNode;
}

/** 工具面板入口清单（空态展示，参考主流 IDE 工具面板交互） */
export const TOOLS: ToolMeta[] = [
  { key: 'summary', title: '任务摘要', description: '查看任务执行进度、产物汇总及关联信息', icon: <ProfileOutlined /> },
  { key: 'artifacts', title: '产物', description: '浏览和预览 Agent 交付的文件', icon: <FolderOutlined /> },
  { key: 'spec', title: '规范文档', description: 'AI 生成并实时更新，也可手动编辑', icon: <FileTextOutlined /> },
  { key: 'tasks', title: '任务清单', description: 'AI 拆解的开发任务列表', icon: <OrderedListOutlined /> },
  { key: 'checklist', title: '验收清单', description: '任务完成的验收标准', icon: <CheckCircleOutlined /> },
  { key: 'plan', title: '方案', description: '方案审批与执行进度跟踪', icon: <SolutionOutlined /> },
];

/** 按 key 获取工具元数据（未知 key 回退第一个工具，理论上不发生） */
export function toolMeta(key: ToolKey): ToolMeta {
  return TOOLS.find((tool) => tool.key === key) ?? TOOLS[0];
}
