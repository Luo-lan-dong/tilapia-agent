import { useCallback, useEffect, useState } from 'react';
import {
  App,
  Card,
  Drawer,
  List,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
} from 'antd';
import { fetchSkillDetail, fetchSkills, updateSkillEnabled } from '@/api/skills';
import type { SkillDetail, SkillSummary } from '@/types/skill';
import { COLORS } from '@/theme/tokens';

const { Title, Text, Paragraph } = Typography;

/**
 * 技能管理页（ADR-033）。
 *
 * 展示中枢 AI 对话 Agent 可调用的技能注册表：用途描述、参数说明、
 * 返回结构、启用开关；禁用的技能在对话中对 AI 不可见。
 */
export function Skills() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(true);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [toggling, setToggling] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchSkills();
      setSkills(data);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '技能列表加载失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    // 异步 IIFE 中加载（react-hooks/set-state-in-effect 要求 setState
    // 出现在异步回调而非 effect 同步调用链上）
    void (async () => {
      await loadSkills();
    })();
  }, [loadSkills]);

  const handleToggle = useCallback(
    async (skill: SkillSummary, enabled: boolean) => {
      setToggling(skill.skill_key);
      try {
        const updated = await updateSkillEnabled(skill.skill_key, { enabled });
        setSkills((prev) => prev.map((s) => (s.skill_key === updated.skill_key ? updated : s)));
        message.success(`${updated.name} 已${enabled ? '启用' : '禁用'}`);
      } catch (err) {
        message.error(err instanceof Error ? err.message : '操作失败');
      } finally {
        setToggling(null);
      }
    },
    [message],
  );

  const openDetail = useCallback(async (skillKey: string) => {
    setDetailOpen(true);
    setDetailLoading(true);
    try {
      setDetail(await fetchSkillDetail(skillKey));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  return (
    <div style={{ padding: 24 }}>
      <Space align="center" style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <div>
          <Title level={4} style={{ color: COLORS.textPrimary, margin: 0 }}>
            技能
          </Title>
          <Text style={{ color: COLORS.textSecondary }}>
            中枢 AI 在对话中可自主调用的能力；禁用后 AI 将不再调用该技能
          </Text>
        </div>
        <Tag color="green">{skills.filter((s) => s.enabled).length} 已启用</Tag>
      </Space>

      <Card styles={{ body: { padding: 0 } }}>
        <List
          loading={loading}
          dataSource={skills}
          renderItem={(skill) => (
            <List.Item
              style={{ padding: '16px 24px', cursor: 'pointer' }}
              onClick={() => void openDetail(skill.skill_key)}
              actions={[
                <span key="switch" onClick={(e) => e.stopPropagation()}>
                  <Switch
                    checked={skill.enabled}
                    loading={toggling === skill.skill_key}
                    onChange={(checked) => void handleToggle(skill, checked)}
                  />
                </span>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Text strong style={{ color: COLORS.textPrimary }}>
                      {skill.name}
                    </Text>
                    <Tag style={{ color: COLORS.textSecondary }}>{skill.skill_key}</Tag>
                    {!skill.enabled && <Tag color="default">已禁用</Tag>}
                  </Space>
                }
                description={
                  <Space direction="vertical" size={2}>
                    <Paragraph style={{ color: COLORS.textSecondary, margin: 0 }}>
                      {skill.description}
                    </Paragraph>
                    <Text style={{ color: COLORS.textMuted, fontSize: 12 }}>
                      参数：{skill.parameters_summary}
                    </Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>

      <Drawer
        title={detail ? `${detail.name}（${detail.skill_key}）` : '技能详情'}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={520}
        destroyOnHidden
      >
        {detailLoading || !detail ? (
          <Spin style={{ display: 'block', margin: '48px auto' }} />
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <div>
              <Title level={5} style={{ color: COLORS.textPrimary, margin: 0 }}>
                功能描述
              </Title>
              <Paragraph style={{ color: COLORS.textSecondary }}>{detail.description}</Paragraph>
            </div>
            <div>
              <Title level={5} style={{ color: COLORS.textPrimary, margin: 0 }}>
                返回结构
              </Title>
              <Paragraph style={{ color: COLORS.textSecondary }}>{detail.returns}</Paragraph>
            </div>
            <div>
              <Title level={5} style={{ color: COLORS.textPrimary, margin: 0 }}>
                参数定义（JSON Schema）
              </Title>
              <pre
                style={{
                  background: COLORS.codeBg,
                  border: `1px solid ${COLORS.border}`,
                  borderRadius: 8,
                  padding: 12,
                  fontSize: 12,
                  color: COLORS.textSecondary,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {JSON.stringify(detail.parameters, null, 2)}
              </pre>
            </div>
          </Space>
        )}
      </Drawer>
    </div>
  );
}
