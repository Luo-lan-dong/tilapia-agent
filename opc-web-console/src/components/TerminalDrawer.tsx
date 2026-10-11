/* 终端抽屉（ADR-053；多标签 2026-09-28）：PTY 交互式终端 + 权限三档切换。
   终端实例在 TerminalPane（单标签单实例），本组件只负责壳/标签栏/工具栏。 */
import { useEffect, useState } from 'react';
import { App as AntApp, Button, Drawer, Select, Space } from 'antd';
import { CloseOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { decideTerminalApproval, fetchPermissionMode, updatePermissionMode } from '@/api/terminal';
import { terminalStore, useTerminalState } from '@/stores/terminalStore';
import type { TerminalApprovalRequest } from '@/types/taskBuilding';
import { TerminalPane } from './TerminalPane';
import './TerminalDrawer.css';

const MODE_OPTIONS = [
  { value: 'manual', label: '手动审批' },
  { value: 'ai_auto', label: 'AI 自动审批' },
  { value: 'full', label: '完全访问' },
];

interface TerminalDrawerProps {
  /** 待审批命令（来自对话流 SSE command_approval_request） */
  approvalRequest?: TerminalApprovalRequest | null;
  /** 审批处理完成回调（父层清空状态） */
  onApprovalDone?: () => void;
}

/** 标签显示名：主终端「终端」，附加标签「终端 2/3/…」 */
function tabLabel(tab: string): string {
  const marker = tab.lastIndexOf('::t');
  if (marker < 0) {
    return '终端';
  }
  const index = Number(tab.slice(marker + 3));
  return Number.isInteger(index) && index > 1 ? `终端 ${index}` : '终端';
}

/**
 * 全局右侧终端抽屉：多标签（+ 新建），每个标签独立 PTY 会话；
 * 顶栏图标与 AI 执行（command_executing）均可打开。
 */
export function TerminalDrawer({ approvalRequest, onApprovalDone }: TerminalDrawerProps) {
  const { open, workDir, tabs, activeTab, reconnectNonce } = useTerminalState();
  const [mode, setMode] = useState('manual');
  const { message } = AntApp.useApp();

  useEffect(() => {
    if (open) {
      fetchPermissionMode()
        .then((res) => setMode(res.mode))
        .catch(() => undefined);
    }
  }, [open]);

  const handleModeChange = async (value: string) => {
    try {
      await updatePermissionMode(value);
      setMode(value);
      if (value === 'full') {
        message.warning('已切换为完全访问：AI 命令将不经审批直接执行');
      }
    } catch {
      message.error('权限模式切换失败');
    }
  };

  const handleApproval = async (approve: boolean) => {
    if (!approvalRequest) {
      return;
    }
    try {
      await decideTerminalApproval(approvalRequest.approval_id, approve);
      onApprovalDone?.();
      message.success(approve ? '已批准执行' : '已拒绝执行');
    } catch {
      message.error('审批操作失败');
    }
  };

  return (
    <Drawer
      open={open}
      onClose={() => terminalStore.setOpen(false)}
      width={640}
      mask={false}
      title="终端"
      styles={{ body: { padding: 0 } }}
      closeIcon={<CloseOutlined />}
      rootClassName="terminal-drawer"
    >
      <div className="terminal-drawer__body">
        <div className="terminal-drawer__toolbar">
          <div className="terminal-drawer__tabs" data-testid="terminal-tabs">
            {tabs.map((tab) => (
              <div
                key={tab}
                className={`terminal-drawer__tab${tab === activeTab ? ' is-active' : ''}`}
                onClick={() => terminalStore.setActiveTab(tab)}
                data-testid="terminal-tab"
              >
                <span>{tabLabel(tab)}</span>
                <CloseOutlined
                  className="terminal-drawer__tab-close"
                  onClick={(event) => {
                    event.stopPropagation();
                    terminalStore.closeTab(tab);
                  }}
                />
              </div>
            ))}
            <Button
              size="small"
              type="text"
              icon={<PlusOutlined />}
              title="新建终端"
              onClick={() => terminalStore.addTab()}
              data-testid="terminal-add-tab"
            />
          </div>
          <Space size={8} align="center">
            <span className="terminal-drawer__title">命令权限</span>
            <Select
              className="terminal-drawer__mode-select"
              size="small"
              value={mode}
              options={MODE_OPTIONS}
              onChange={handleModeChange}
              data-testid="terminal-mode-select"
            />
            {mode === 'full' && <span className="terminal-drawer__warning">AI 命令不经审批直接执行</span>}
            <Button
              size="small"
              type="text"
              icon={<ReloadOutlined />}
              title="重连当前终端"
              onClick={() => terminalStore.reconnectActive()}
            />
          </Space>
        </div>
        {activeTab && (
          <TerminalPane key={`${activeTab}#${reconnectNonce}`} tabKey={activeTab} workDir={workDir} />
        )}
        {approvalRequest && (
          <div className="terminal-drawer__approval" data-testid="terminal-approval-bar">
            <Space size={8}>
              <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
                待审批：{approvalRequest.command}
              </span>
              <Button size="small" type="primary" onClick={() => handleApproval(true)}>
                批准
              </Button>
              <Button size="small" danger onClick={() => handleApproval(false)}>
                拒绝
              </Button>
            </Space>
          </div>
        )}
      </div>
    </Drawer>
  );
}
