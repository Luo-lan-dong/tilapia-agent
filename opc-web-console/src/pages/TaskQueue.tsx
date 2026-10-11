import { useState } from 'react';
import { Alert, Button, Card, Row, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { TaskDetailDrawer } from '@/components/TaskDetailDrawer';
import { TaskTable } from '@/components/TaskTable';
import { useTaskList } from '@/hooks/useTaskList';
import { COLORS, primaryButtonStyle } from '@/theme/tokens';

const { Title, Text } = Typography;

/**
 * 任务队列页面。
 *
 * 展示全部任务，支持「新建任务」入口；手动派发改在 TaskDetailDrawer 操作区完成
 * （见 ADR-017，不再在列表行内做派发选择）。
 */
export function TaskQueue() {
  const navigate = useNavigate();
  const taskList = useTaskList({ limit: 50 });
  const [detailTaskId, setDetailTaskId] = useState<string | null>(null);

  return (
    <div style={{ padding: '24px', minHeight: '100vh', background: COLORS.bgBase }}>
      <Row justify="space-between" align="middle" style={{ marginBottom: '16px' }}>
        <div>
          <Title level={3} style={{ color: COLORS.textPrimary, margin: 0 }}>
            任务队列
          </Title>
          <Text style={{ color: COLORS.textSecondary }}>
            任务创建后默认进入自动派发队列；如需手动派发，请在任务详情抽屉操作
          </Text>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => navigate('/tasks/new')}
          style={{ ...primaryButtonStyle }}
        >
          新建任务
        </Button>
      </Row>

      {taskList.error && (
        <Alert
          message={taskList.error.message}
          type="error"
          showIcon
          style={{ marginBottom: '16px', background: 'var(--error-soft)', border: 'none' }}
        />
      )}

      <Card
        style={{
          background: COLORS.bgContainer,
          border: '1px solid ' + COLORS.border,
          borderRadius: '8px',
        }}
      >
        <TaskTable
          data={taskList.data}
          loading={taskList.loading}
          onViewDetail={setDetailTaskId}
          onChanged={() => taskList.refresh()}
        />
      </Card>

      <TaskDetailDrawer
        open={detailTaskId !== null}
        taskId={detailTaskId}
        onClose={() => setDetailTaskId(null)}
        onChanged={() => taskList.refresh()}
      />
    </div>
  );
}
