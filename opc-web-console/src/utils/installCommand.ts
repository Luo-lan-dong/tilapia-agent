/**
 * 从 VITE_API_BASE_URL 推导 Hub 服务根地址。
 *
 * 若配置为绝对路径且以 /api 结尾，则去掉 /api；
 * 若配置为相对路径 /api，则使用当前页面 origin。
 */
export function getHubUrl(): string {
  const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api';

  if (apiBaseUrl.startsWith('http')) {
    return apiBaseUrl.replace(/\/api\/?$/, '');
  }

  if (typeof window !== 'undefined') {
    return window.location.origin;
  }

  return '';
}

/**
 * 根据脚本地址、Hub 地址、Token 与可选节点显示名组装一键安装命令。
 *
 * @param scriptUrl  安装脚本 URL
 * @param hubUrl     中枢根地址
 * @param token      注册 Token
 * @param agentName  可选节点显示名；非空时追加 ``--agent-name "<value>"``
 *                   （双引号包裹，调用方需保证 value 不含未转义双引号）
 */
export function buildInstallCommand(
  scriptUrl: string,
  hubUrl: string,
  token: string,
  agentName?: string
): string {
  const trimmed = (agentName ?? '').trim();
  const base = `curl -fsSL ${scriptUrl} | sudo bash -s -- --hub-url ${hubUrl} --token ${token}`;
  if (!trimmed) {
    return base;
  }
  // agentName 内的双引号做转义，避免 shell 解析错误
  const escaped = trimmed.replace(/"/g, '\\"');
  return `${base} --agent-name "${escaped}"`;
}
