import { useCallback, useEffect, useState } from 'react';
import { App } from 'antd';
import { getAgentNodeById, resetAgentNodeToken, updateAgentNodeName } from '@/api/agentNodes';
import { copyToClipboard } from '@/utils/clipboard';
import type { AgentNode } from '@/types/agentNode';

/** antd message 实例类型（App.useApp() 消费，避免静态方法警告） */
type MessageInstance = ReturnType<typeof App.useApp>['message'];

interface UseNodeDetailResult {
  /** 节点详情 */
  node: AgentNode | null;
  /** 详情加载中 */
  loading: boolean;
  /** Token 重置请求进行中 */
  resetting: boolean;
  /** 名称编辑态 */
  editingName: boolean;
  /** 名称编辑草稿 */
  nameDraft: string;
  /** 名称保存请求进行中 */
  savingName: boolean;
  setNameDraft: (value: string) => void;
  startEditName: () => void;
  cancelEditName: () => void;
  saveName: () => Promise<void>;
  resetToken: () => Promise<void>;
  copyToken: () => void;
}

/**
 * 节点详情数据加载与编辑动作（从 NodeDetailDrawer 拆出）。
 *
 * 抽屉打开时拉取节点详情（AbortController 取消在途请求），
 * 并提供 Token 重置/复制与节点名称编辑保存动作（ADR-016）。
 */
export function useNodeDetail(
  nodeId: string | null,
  open: boolean,
  message: MessageInstance,
): UseNodeDetailResult {
  const [node, setNode] = useState<AgentNode | null>(null);
  const [loading, setLoading] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [savingName, setSavingName] = useState(false);

  const loadDetail = useCallback(
    async (signal?: AbortSignal) => {
      if (!nodeId) {
        return;
      }
      setLoading(true);
      try {
        const detail = await getAgentNodeById(nodeId);
        if (signal?.aborted) {
          return;
        }
        setNode(detail);
      } catch (error) {
        if (signal?.aborted) {
          return;
        }
        const errorMessage = error instanceof Error ? error.message : '加载节点详情失败';
        message.error(errorMessage);
      } finally {
        if (!signal?.aborted) {
          setLoading(false);
        }
      }
    },
    [nodeId, message],
  );

  useEffect(() => {
    if (!open || !nodeId) {
      return;
    }
    const controller = new AbortController();
    const timeoutId = setTimeout(() => {
      void loadDetail(controller.signal);
    }, 0);
    return () => {
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, [open, nodeId, loadDetail]);

  const resetToken = useCallback(async () => {
    if (!nodeId) {
      return;
    }
    setResetting(true);
    try {
      const result = await resetAgentNodeToken(nodeId);
      setNode((prev) => (prev ? { ...prev, registration_token: result.registration_token } : prev));
      message.success('Token 重置成功');
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Token 重置失败';
      message.error(errorMessage);
    } finally {
      setResetting(false);
    }
  }, [nodeId, message]);

  const copyToken = useCallback(() => {
    if (node?.registration_token) {
      void copyToClipboard(node.registration_token, '注册 Token', message);
    }
  }, [node, message]);

  const startEditName = useCallback(() => {
    if (!node) {
      return;
    }
    setNameDraft(node.name);
    setEditingName(true);
  }, [node]);

  const cancelEditName = useCallback(() => {
    setEditingName(false);
    setNameDraft('');
  }, []);

  const saveName = useCallback(async () => {
    if (!node) {
      return;
    }
    const trimmed = nameDraft.trim();
    if (!trimmed) {
      message.error('节点名称不能为空');
      return;
    }
    if (trimmed === node.name) {
      setEditingName(false);
      return;
    }
    setSavingName(true);
    try {
      const updated = await updateAgentNodeName(node.id, trimmed);
      setNode(updated);
      setEditingName(false);
      message.success('节点名称已更新');
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '节点名称更新失败';
      message.error(errorMessage);
    } finally {
      setSavingName(false);
    }
  }, [node, nameDraft, message]);

  return {
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
  };
}
