import { useEffect, useState } from 'react';
import { Select, Tooltip, Typography } from 'antd';
import { fetchLLMModels } from '@/api/llmProvider';
import type { LLMModelItem } from '@/types/llmProvider';
import type { ExecutorModelSelection } from '@/types/task';

interface TaskExecutorModelSelectProps {
  value: ExecutorModelSelection;
  onChange: (selection: ExecutorModelSelection) => void;
  /** 中枢系统设置当前使用中的模型名（用于选项文案展示，缺失时不显示） */
  hubModelName?: string;
  disabled?: boolean;
}

/** 选择器选项编码：auto / hub / custom:<provider_id> */
type SelectKey = 'auto' | 'hub' | `custom:${string}`;

/** 下拉选项：label 为选中态**短文案**（窄面板不撑爆底栏），desc 为完整描述 */
interface ExecutorOption {
  value: SelectKey;
  label: string;
  desc?: string;
}

function toKey(selection: ExecutorModelSelection): SelectKey {
  if (selection.source === 'auto') {
    return 'auto';
  }
  if (selection.source === 'custom' && selection.provider_id) {
    return `custom:${selection.provider_id}`;
  }
  return 'hub';
}

/**
 * 子 Agent 执行模型选择器（ADR-029 / ADR-030 / ADR-039）。
 *
 * 选项：自动（由中枢按任务内容选模）、中枢当前使用中模型、
 * 已添加的自定义模型（分组展示）。
 *
 * 布局约束（2026-10-05）：选中态展示**短文案**（如「中枢·deepseek-flash」），
 * 完整描述只在下拉项内展示——否则底栏在窄面板下被长文案撑爆换行。
 */
export function TaskExecutorModelSelect({
  value,
  onChange,
  hubModelName,
  disabled,
}: TaskExecutorModelSelectProps) {
  const [models, setModels] = useState<LLMModelItem[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchLLMModels()
      .then((items) => {
        if (!cancelled) {
          setModels(items);
        }
      })
      .catch(() => {
        // 列表加载失败静默：仅显示中枢默认选项
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const options: { label: string; title: string; options: ExecutorOption[] }[] = [
    {
      label: '智能选择',
      title: '智能选择',
      options: [
        { value: 'auto', label: '智能', desc: '自动（由中枢按任务内容选择）' },
      ],
    },
    {
      label: '中枢模型',
      title: '中枢模型',
      options: [
        {
          value: 'hub',
          label: hubModelName ? `中枢·${hubModelName}` : '中枢默认',
          desc: hubModelName ? `中枢配置（${hubModelName}）` : '中枢配置（跟随系统设置）',
        },
      ],
    },
    ...(models.length > 0
      ? [
          {
            label: '自定义模型',
            title: '自定义模型',
            options: models.map((item) => ({
              value: `custom:${item.id}` as SelectKey,
              label: item.model,
              desc: `${item.model}（${item.provider}）${item.supports_vision ? ' · 支持图片' : ''}`,
            })),
          },
        ]
      : []),
  ];

  return (
    <Tooltip title="决定任务派发到节点后由哪个模型执行；「智能」由中枢按任务内容选择（带图任务优先支持图片的模型）；凭据由中枢实时下发">
      <Select<SelectKey>
        className="task-new__executor-select"
        size="small"
        value={toKey(value)}
        onChange={(key) => {
          if (key === 'auto') {
            onChange({ source: 'auto' });
          } else if (key === 'hub') {
            onChange({ source: 'hub' });
          } else {
            onChange({ source: 'custom', provider_id: key.slice('custom:'.length) });
          }
        }}
        disabled={disabled}
        data-testid="executor-model-select"
        options={options}
        optionRender={(option) => {
          const data = option.data as ExecutorOption;
          return (
            <div className="task-new__executor-option">
              <span>{data.label}</span>
              {data.desc && (
                <Typography.Text type="secondary" className="task-new__executor-option-desc">
                  {data.desc}
                </Typography.Text>
              )}
            </div>
          );
        }}
      />
    </Tooltip>
  );
}
