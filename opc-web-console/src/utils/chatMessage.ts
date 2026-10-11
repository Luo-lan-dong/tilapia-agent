import type { ChatMessage } from '@/types/taskBuilding';

/**
 * 消息的 `@节点` 来源标注归一化。
 *
 * 后端落库直传为 snake_case（ADR-064 消息契约 {role, content, mention_node,
 * reply_to?, ts}），本地乐观消息为 camelCase（ADR-063 M4 既有形态），
 * 渲染/轮询匹配处统一经此读取。
 */
export function messageMentionNode(message: ChatMessage): string | undefined {
  return message.mentionNode ?? message.mention_node;
}

/** 消息的节点回复关联 ID（node-chat 202 的 reply_to，双形态兼容）。 */
export function messageReplyTo(message: ChatMessage): string | undefined {
  return message.replyTo ?? message.reply_to;
}
