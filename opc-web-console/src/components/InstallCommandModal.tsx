/* 规范豁免说明（coding-standards 4.3，通用组件上限 200 行）：安装命令弹窗含 OS/版本联动选择与命令组装预览，状态链路单一，故豁免。 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App, Button, Input, Modal, Select, Space, Spin, Typography } from 'antd';
import { CopyOutlined } from '@ant-design/icons';
import { getInstallScript } from '@/api/agentNodes';
import { copyToClipboard } from '@/utils/clipboard';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';
import { buildInstallCommand } from '@/utils/installCommand';
import type { InstallScriptInfo } from '@/types/installScript';

const { Text } = Typography;

/**
 * InstallCommandModal 属性定义
 */
interface InstallCommandModalProps {
  /** 弹窗是否可见 */
  open: boolean;
  /** 关闭弹窗回调 */
  onClose: () => void;
}

/**
 * MVP 默认注册 Token，仅供演示使用
 */
const DEFAULT_REGISTRATION_TOKEN = 'demo-registration-token';

/**
 * 安装命令弹窗组件。
 *
 * 供用户选择操作系统与 Agent 类型，获取安装脚本元数据，
 * 并展示可一键复制到 Linux 终端执行的安装命令。
 */
export function InstallCommandModal({ open, onClose }: InstallCommandModalProps) {
  const { message } = App.useApp();
  const [scriptInfo, setScriptInfo] = useState<InstallScriptInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState(DEFAULT_REGISTRATION_TOKEN);
  const [operatingSystem, setOperatingSystem] = useState('linux');
  const [agentType, setAgentType] = useState('code');
  const [nodeName, setNodeName] = useState('');

  const loadScript = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const info = await getInstallScript({ agent_type: agentType, os: operatingSystem });
        if (signal?.aborted) {
          return;
        }
        setScriptInfo(info);
      } catch (err) {
        if (signal?.aborted) {
          return;
        }
        setError(err instanceof Error ? err.message : '获取安装脚本失败');
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    [agentType, operatingSystem]
  );

  useEffect(() => {
    if (!open) {
      return;
    }
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
      void loadScript(controller.signal);
    }, 0);
    return () => {
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [open, loadScript]);

  const installCommand = useMemo(() => {
    if (!scriptInfo?.script_url || !scriptInfo.hub_url) {
      return '';
    }
    return buildInstallCommand(
      scriptInfo.script_url,
      scriptInfo.hub_url,
      token.trim(),
      nodeName.trim()
    );
  }, [scriptInfo, token, nodeName]);

  const handleCopyCommand = () => {
    if (installCommand) {
      void copyToClipboard(installCommand, '安装命令', message);
    }
  };

  return (
    <Modal
      title="安装新节点"
      open={open}
      onCancel={onClose}
      footer={null}
      width={640}
      styles={{
        content: { background: COLORS.bgContainer },
        header: { background: COLORS.bgContainer, borderBottom: '1px solid ' + COLORS.border },
      }}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="large">
        <Space size="middle">
          <div>
            <Text style={{ color: COLORS.textSecondary, display: 'block', marginBottom: '8px' }}>
              操作系统
            </Text>
            <Select
              value={operatingSystem}
              options={[{ value: 'linux', label: 'Linux' }]}
              onChange={setOperatingSystem}
              style={{ width: 160 }}
            />
          </div>
          <div>
            <Text style={{ color: COLORS.textSecondary, display: 'block', marginBottom: '8px' }}>
              Agent 类型
            </Text>
            <Select
              value={agentType}
              options={[{ value: 'code', label: 'Code' }]}
              onChange={setAgentType}
              style={{ width: 160 }}
            />
          </div>
        </Space>

        <div>
          <Text style={{ color: COLORS.textSecondary, display: 'block', marginBottom: '8px' }}>
            节点名称
          </Text>
          <Input
            value={nodeName}
            onChange={(event) => setNodeName(event.target.value)}
            placeholder="留空则由 Agent 按 OS 平台+版本自动生成（如 Ubuntu 22.04）"
            maxLength={128}
            style={{ background: COLORS.bgBase, color: COLORS.textPrimary }}
          />
          <Text style={{ color: COLORS.textSecondary, fontSize: '12px' }}>
            可填可读名称如 dev-machine-A；留空不传 --agent-name 参数。
          </Text>
        </div>

        <div>
          <Text style={{ color: COLORS.textSecondary, display: 'block', marginBottom: '8px' }}>
            注册 Token
          </Text>
          <Input
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="请输入或粘贴注册 Token"
            style={{ background: COLORS.bgBase, color: COLORS.textPrimary }}
          />
          <Text style={{ color: COLORS.textSecondary, fontSize: '12px' }}>
            MVP 阶段默认使用 demo token，生产环境请替换为实际 Token
          </Text>
        </div>

        {loading ? (
          <Spin tip="加载安装脚本信息...">
          <div style={{ minHeight: 48, margin: '24px 0' }} />
        </Spin>
        ) : error ? (
          <Alert
            message={error}
            type="error"
            showIcon
            style={{ background: 'var(--error-soft)', border: 'none' }}
          />
        ) : (
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: '8px',
              }}
            >
              <Text style={{ color: COLORS.textSecondary }}>一键安装命令</Text>
              <Button
                type="primary"
                icon={<CopyOutlined />}
                onClick={handleCopyCommand}
                disabled={!installCommand}
                style={{
                  ...primaryButtonStyle,
                }}
              >
                复制命令
              </Button>
            </div>
            <Input.TextArea
              value={installCommand || '等待安装脚本信息...'}
              readOnly
              autoSize={{ minRows: 3, maxRows: 6 }}
              style={{
                background: COLORS.bgBase,
                color: COLORS.textPrimary,
                fontFamily: 'monospace',
              }}
            />
            {scriptInfo && (
              <Text style={{ color: COLORS.textSecondary, fontSize: '12px', display: 'block', marginTop: '8px' }}>
                版本：{scriptInfo.version} · 校验和：{scriptInfo.checksum}
              </Text>
            )}
          </div>
        )}
      </Space>
    </Modal>
  );
}
