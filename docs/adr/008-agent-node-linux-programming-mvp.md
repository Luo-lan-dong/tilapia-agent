# ADR-008：Linux 编程 Agent 节点 MVP 与节点管理

## 状态

Accepted

## 日期

2026-08-04

## 问题背景

ADR-006 已确定 Agent 节点采用「中枢托管模板 + 安装脚本 + 自动注册」的接入方式，并计划支持多种 Agent 类型（编程、视频、文案、设计等）。

当前需要落地第一个可运行的 Agent 节点类型——**编程 Agent（programming）**，并配套前端节点管理页面。MVP 阶段需要验证的是：

- 一条 Linux 安装命令即可完成部署。
- 安装后自动加入系统自启并持续心跳。
- 中枢能感知节点在线状态。
- 用户可通过前端查看节点、获取安装命令。

为了控制 MVP 范围，本阶段**不实现真实的代码编写/执行能力**，任务下发后节点仅做占位响应，真正的执行器后续迭代。

## 决策

1. **只实现 Linux 版本**，Windows 版本延后。
2. **Agent 能力范围**：安装 → 自启 → 注册 → 心跳保持在线。任务执行仅打印日志并占位回复，不真正运行代码。
3. **安装脚本分发**：中枢通过 `StaticFiles` 提供 `install-agent-programming-linux.sh`，脚本内从中枢拉取 OPC 项目独有的 Agent 模板；第三方依赖（Python、pip 等）由脚本内置系统命令安装，GPU 驱动等后续优化。
4. **自启方式**：Linux 使用 systemd 服务。
5. **注册 Token**：由中枢生成，安装脚本通过命令行参数传入。
6. **前端节点管理**：新增 `/nodes` 页面，支持节点列表、节点详情、按系统展示安装命令、复制注册 Token。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| A（本决策）：Linux 占位 Agent + 前端节点管理 | MVP 范围最小，快速验证接入闭环 | 不验证真实编程能力 | ✅ 选中 |
| B：直接实现带代码执行能力的编程 Agent | 一步到位 | 工作量大，沙箱、安全、依赖复杂 | ❌ 延后 |
| C：用 Docker 容器分发 Agent | 环境一致 | MVP 增加 Docker 依赖，GPU 场景更复杂 | ❌ 延后 |

## 决策原因

1. **先验证接入链路**：注册、心跳、状态同步是整个系统的基础，必须先跑通。
2. **降低 MVP 风险**：真实代码执行涉及沙箱、安全扫描、交付物管理，复杂度远高于接入本身。
3. **与 ADR-006 保持一致**：继续采用脚本 + 模板 + 自动注册的方式，不引入新的部署模式。

## 影响

### 对系统架构的影响

- 中枢新增安装脚本静态分发和注册 Token 管理。
- 新增独立项目 `opc-agent-programming/`，作为后续各类型 Agent 的参考模板。
- 前端新增节点管理页面，与仪表盘并列。

### 对开发的影响

- 需要维护 `opc-agent-programming/` 的目录结构、安装脚本和 systemd 配置。
- 需要为前端 `/nodes` 页面补充列表、详情、安装命令等接口。

### 对安全的影响

- 注册 Token 仍按现有机制生成，建议设置有效期（后续迭代）。
- 安装脚本通过 HTTP 分发，生产环境应切 HTTPS（后续迭代）。

### 对运维的影响

- 新 Linux 机器接入流程：复制安装命令 → 执行 → 自动注册并上线。
- 节点升级通过重新执行安装脚本完成。

## 关联文档

- [ADR-006 Agent 节点部署与接入方案](./006-agent-node-deployment.md)
- [004-agent-node-protocol-spec.md](../spec/004-agent-node-protocol-spec.md)
- [agent-node-standards.md](../standards/agent-node-standards.md)
- [008-node-management-and-programming-agent-linux-spec.md](../spec/008-node-management-and-programming-agent-linux-spec.md)
