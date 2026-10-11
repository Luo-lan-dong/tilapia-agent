import { useEffect, useState } from 'react';
import { Alert, App, Button, Card, Col, Form, Input, Row, Space, Spin, Typography } from 'antd';
import { AIDispatchCard } from '@/components/AIDispatchCard';
import { BtMcpPanel } from '@/components/BtMcpPanel';
import { LLMModelsPanel } from '@/components/LLMModelsPanel';
import {
  fetchNetworkSettings,
  updateNetworkSettings,
} from '@/api/networkSettings';
import type { NetworkSettingsUpdateRequest } from '@/types/networkSettings';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';

const { Title, Text } = Typography;

/**
 * 根据当前页面地址推导 Hub 地址。
 *
 * 优先使用后端上报的实际监听端口（ADR-031：端口避让后可能与 8000 不同）；
 * 无实际端口时，桌面模式（前端由后端托管）取当前访问端口，dev 模式
 * （5173）回落 8000。
 */
function deriveHubUrlFromOrigin(actualPort: number | null): string {
  const { protocol, hostname, port } = window.location;
  const effectivePort =
    actualPort ?? (port && port !== '5173' ? port : '8000');
  return `${protocol}//${hostname}:${effectivePort}`;
}

/** 设置页卡片统一外观（对齐设计稿 panel-card：容器底色 + 描边 + 8px 圆角 + 20px 内边距） */
const panelCardStyle: React.CSSProperties = {
  background: COLORS.bgContainer,
  border: '1px solid ' + COLORS.border,
  borderRadius: '8px',
};

const panelCardBodyStyle = { padding: '20px' };

/**
 * 系统设置页面。
 *
 * 模型管理（ADR-030：OpenAI 兼容自定义模型列表）与网络配置，保存后立即生效。
 * 布局（设计稿 v2）：双栏 grid——左栏模型管理（14/24），右栏 AI 智能调度 +
 * 网络配置（10/24）；窄屏自动单列。
 */
export function Settings() {
  const { message } = App.useApp();
  const [networkForm] = Form.useForm<NetworkSettingsUpdateRequest>();
  const [loading, setLoading] = useState(false);
  const [networkSaving, setNetworkSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [actualPort, setActualPort] = useState<number | null>(null);

  useEffect(() => {
    async function loadConfig() {
      setLoading(true);
      setError(null);
      try {
        const networkData = await fetchNetworkSettings();
        setActualPort(networkData.actual_port ?? null);
        networkForm.setFieldsValue({
          hub_public_url: networkData.hub_public_url ?? '',
          frontend_url: networkData.frontend_url ?? '',
        });
      } catch (err) {
        setError(err instanceof Error ? err : new Error('加载配置失败'));
      } finally {
        setLoading(false);
      }
    }

    loadConfig();
  }, [networkForm]);

  const handleNetworkSubmit = async (values: NetworkSettingsUpdateRequest) => {
    setNetworkSaving(true);
    try {
      const payload: NetworkSettingsUpdateRequest = {
        hub_public_url: values.hub_public_url?.trim() || null,
        frontend_url: values.frontend_url?.trim() || null,
      };
      await updateNetworkSettings(payload);
      message.success('网络配置已保存，安装命令将立即使用新地址');
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : '保存失败';
      message.error(errorMessage);
    } finally {
      setNetworkSaving(false);
    }
  };

  const handleUseCurrentOrigin = () => {
    const hubUrl = deriveHubUrlFromOrigin(actualPort);
    const frontendUrl = window.location.origin;
    networkForm.setFieldsValue({
      hub_public_url: hubUrl,
      frontend_url: frontendUrl,
    });
  };

  if (loading) {
    return (
      <div style={{ padding: '24px', textAlign: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div style={{ padding: '24px', minHeight: '100vh', background: COLORS.bgBase }}>
      <div style={{ marginBottom: '24px' }}>
        <Title level={3} style={{ color: COLORS.textPrimary, margin: 0 }}>
          系统设置
        </Title>
        <Text style={{ color: COLORS.textSecondary }}>
          配置大模型 API 与网络地址，保存后立即生效
        </Text>
      </div>

      {error && (
        <Alert
          message={error.message}
          type="error"
          showIcon
          style={{ marginBottom: '16px', background: 'var(--error-soft)', border: 'none' }}
        />
      )}

      {/* 宝塔 MCP 服务器管理（ADR-049）：AI 部署一条龙的目标服务器连接 */}
      <Card style={{ ...panelCardStyle, marginBottom: 16 }} styles={{ body: panelCardBodyStyle }}>
        <BtMcpPanel />
      </Card>

      {/* 双栏 grid（设计稿 v2）：左 14/24 模型管理，右 10/24 调度+网络；窄屏单列 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card style={{ ...panelCardStyle, height: '100%' }} styles={{ body: panelCardBodyStyle }}>
            <LLMModelsPanel />
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
            <AIDispatchCard style={panelCardStyle} />
            <Card style={panelCardStyle} styles={{ body: panelCardBodyStyle }}>
              <div style={{ marginBottom: '16px' }}>
                <Title level={5} style={{ color: COLORS.textPrimary, margin: 0 }}>
                  网络配置
                </Title>
                <Text style={{ color: COLORS.textSecondary }}>
                  配置中枢与前端访问地址，Agent 安装命令将使用此处设置的中枢地址
                </Text>
                {actualPort != null && (
                  <Text
                    style={{ display: 'block', marginTop: 4, color: COLORS.textSecondary }}
                    data-testid="actual-port-hint"
                  >
                    后端当前实际监听端口：{actualPort}
                    （端口被占用时会自动避让，已装 Agent 重连时自动探测新端口）
                  </Text>
                )}
              </div>

              <Form
                form={networkForm}
                layout="vertical"
                onFinish={handleNetworkSubmit}
                disabled={loading}
              >
                <Form.Item
                  label="后端地址（Hub URL）"
                  name="hub_public_url"
                  extra="Agent 安装命令中的 --hub-url 将使用此地址，例如 http://192.168.1.10:8000"
                >
                  <Input placeholder="http://192.168.1.10:8000" />
                </Form.Item>

                <Form.Item
                  label="前端访问地址"
                  name="frontend_url"
                  extra="前端控制台访问地址，供参考与后续跨域配置使用"
                >
                  <Input placeholder="http://192.168.1.10:5173" />
                </Form.Item>

                <Form.Item style={{ marginBottom: 0 }}>
                  <Space>
                    <Button
                      type="primary"
                      htmlType="submit"
                      loading={networkSaving}
                      disabled={loading || !!error}
                      style={{ ...primaryButtonStyle }}
                    >
                      保存配置
                    </Button>
                    <Button
                      onClick={handleUseCurrentOrigin}
                      disabled={loading || !!error}
                    >
                      使用当前访问地址
                    </Button>
                  </Space>
                </Form.Item>
              </Form>
            </Card>
          </div>
        </Col>
      </Row>
    </div>
  );
}
