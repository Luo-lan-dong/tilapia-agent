/* 职责说明（coding-standards 4.3）：详情数据与编辑动作已拆至 useNodeDetail，
   信息展示区已拆至 NodeDetailInfo，本文件保留 Drawer 壳与状态接线。 */
import { App, Drawer, Spin, Typography } from 'antd';
import { NodeDetailInfo } from '@/components/NodeDetailInfo';
import { useNodeDetail } from '@/hooks/useNodeDetail';
import { COLORS } from '@/theme/tokens';

const { Text } = Typography;

/**
 * NodeDetailDrawer 属性定义
 */
interface NodeDetailDrawerProps {
  /** 当前选中的节点 ID，为 null 时不加载数据 */
  nodeId: string | null;
  /** 抽屉是否可见 */
  open: boolean;
  /** 关闭抽屉回调 */
  onClose: () => void;
}

/**
 * 节点详情抽屉组件。
 *
 * 展示指定节点的完整信息，支持复制注册 Token 与重置 Token。
 */
export function NodeDetailDrawer({ nodeId, open, onClose }: NodeDetailDrawerProps) {
  const { message } = App.useApp();
  const {
    node,
    loading,
    resetting,
    editingName,
    nameDraft,
    savingName,
    setNameDraft,
    startEditName,
    cancelEditName,
    saveName,
    resetToken,
    copyToken,
  } = useNodeDetail(nodeId, open, message);

  return (
    <Drawer
      title="节点详情"
      width={480}
      open={open}
      onClose={onClose}
      styles={{
        body: { background: COLORS.bgBase, padding: '24px' },
        header: { background: COLORS.bgContainer, borderBottom: '1px solid ' + COLORS.border },
      }}
    >
      {loading ? (
        <Spin tip="加载中...">
          <div style={{ minHeight: 48, marginTop: '48px' }} />
        </Spin>
      ) : node ? (
        <NodeDetailInfo
          nodeId={nodeId}
          node={node}
          editingName={editingName}
          nameDraft={nameDraft}
          savingName={savingName}
          onNameDraftChange={setNameDraft}
          onStartEditName={startEditName}
          onCancelEditName={cancelEditName}
          onSaveName={() => void saveName()}
          resetting={resetting}
          onCopyToken={copyToken}
          onResetToken={() => void resetToken()}
        />
      ) : (
        <Text style={{ color: COLORS.textSecondary }}>未选择节点</Text>
      )}
    </Drawer>
  );
}
