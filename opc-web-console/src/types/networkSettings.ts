/**
 * 网络配置模型
 */
export interface NetworkSettings {
  /** 中枢公网/局域网地址，用于 Agent 安装命令 */
  hub_public_url: string | null;
  /** 前端控制台访问地址 */
  frontend_url: string | null;
  /** 后端实际监听端口（ADR-031：端口避让后可能与 Hub URL 端口不同；手动 uvicorn 启动时为 null） */
  actual_port: number | null;
}

/**
 * 网络配置更新请求
 */
export interface NetworkSettingsUpdateRequest {
  hub_public_url?: string | null;
  frontend_url?: string | null;
}
