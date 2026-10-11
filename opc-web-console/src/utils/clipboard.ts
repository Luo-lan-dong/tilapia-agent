import { App } from 'antd';

/**
 * antd message 实例类型。
 *
 * 通过 `ReturnType<typeof App.useApp>['message']` 推导，
 * 避免直接依赖 antd 内部子模块路径，保证版本兼容性。
 */
type MessageInstance = ReturnType<typeof App.useApp>['message'];

/**
 * 使用传统的 document.execCommand('copy') 作为兼容性降级方案。
 *
 * 部分浏览器扩展或安全策略会拦截 navigator.clipboard.writeText，
 * 此时通过创建临时 textarea 并选中再执行 execCommand 仍能完成复制。
 */
function fallbackCopyTextToClipboard(text: string): boolean {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-9999px';
  textArea.style.top = '-9999px';
  textArea.setAttribute('readonly', '');
  document.body.appendChild(textArea);

  textArea.focus();
  textArea.select();

  try {
    const successful = document.execCommand('copy');
    return successful;
  } finally {
    document.body.removeChild(textArea);
  }
}

/**
 * 将文本写入剪贴板并给出反馈。
 *
 * message 实例由调用方通过 `App.useApp()` 获取后传入，
 * 避免 antd 静态方法 `message.xxx()` 无法消费动态主题 Context 的警告。
 */
export async function copyToClipboard(
  text: string,
  label: string,
  message: MessageInstance,
): Promise<void> {
  if (!text) {
    message.warning('没有可复制的内容');
    return;
  }

  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      message.success(`${label}已复制`);
      return;
    }
  } catch (err) {
    console.warn('navigator.clipboard.writeText 失败，尝试降级方案', err);
  }

  // 降级方案：document.execCommand('copy')
  const fallbackSuccess = fallbackCopyTextToClipboard(text);
  if (fallbackSuccess) {
    message.success(`${label}已复制`);
    return;
  }

  message.error('复制失败，请手动复制');
}
