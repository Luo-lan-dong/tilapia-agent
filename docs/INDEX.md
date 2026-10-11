# OPC 超级个体 · 项目文档索引

本项目所有技术决策、开发规范、模块设计文档统一存放于此目录。

> **关于历史文档中的「RuoYi 管理后台 / 官网」**：部分历史决策（ADR-018/023/025/026 等）
> 提及的管理后台为**作者私有部署的可选云端资源仓库**，不是本开源仓库的组成部分；
> 开源部署无需此组件（相关功能默认关闭，详见 [DEPLOYMENT.md](./ops/DEPLOYMENT.md)）。

> **AI 快速检索指南（目录即索引，勿读全表）**
>
> | 目标 | 检索方式 |
> |---|---|
> | 决策文档（ADR/Spec/Harness/Changes，`NNN-` 序号主键） | `Glob docs/**/NNN-*`（如 `docs/**/063-*`）或 `python scripts/check_docs.py --map`；本文件仅用于查模块中文名/浏览全貌 |
> | 开发规范 | `Glob docs/standards/*.md`（固定 9 份；各规范加载时机见 `.trae/rules/coding-standards.md` 清单表） |
> | 问题排查记录 | `Glob docs/issues/*.md`（文件名 = `日期-主题-排查记录.md`，按日期/关键词定位，无需读内容） |
> | 文档更新记录 | `docs/changelog/YYYY-MM.md`（按月分文件，新增记录写当月文件，本文件不内嵌） |
> | Ops 手册（Runbook/验收操作单/构建指南） | `Glob docs/ops/*.md` |
> | 架构设计 | `Glob docs/architecture/*.md` |
> | 全目录动态地图 | `python scripts/check_docs.py --tree`（每文件 + H1 标题，永不过期） |

## 一、决策文档（Decision Records）

按 `.trae/rules/decision-documents.md` 要求，所有重大功能/架构变更必须先写决策文档。

### 1.1 自然语言决策类 Spec

| 模块                            | Spec                                                                                                              | Harness                                                                                                                 |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| 中央调度中枢                        | [002-central-dispatch-hub-spec.md](./spec/002-central-dispatch-hub-spec.md)                                               | [002-central-dispatch-hub-harness.md](./spec/002-central-dispatch-hub-harness.md)                                               |
| Agent 节点通信协议                  | [004-agent-node-protocol-spec.md](./spec/004-agent-node-protocol-spec.md)                                                 | [004-agent-node-protocol-harness.md](./spec/004-agent-node-protocol-harness.md)                                                 |
| 任务与节点状态管理                     | [003-task-node-state-spec.md](./spec/003-task-node-state-spec.md)                                                         | [003-task-node-state-harness.md](./spec/003-task-node-state-harness.md)                                                         |
| 文件存储与交付物管理                    | [005-file-storage-spec.md](./spec/005-file-storage-spec.md)                                                               | [005-file-storage-harness.md](./spec/005-file-storage-harness.md)                                                               |
| 前端管理控制台仪表盘                    | [007-web-console-dashboard-spec.md](./spec/007-web-console-dashboard-spec.md)                                             | [007-web-console-dashboard-harness.md](./spec/007-web-console-dashboard-harness.md)                                             |
| 节点管理与 Linux 编程 Agent MVP      | [008-node-management-and-programming-agent-linux-spec.md](./spec/008-node-management-and-programming-agent-linux-spec.md) | [008-node-management-and-programming-agent-linux-harness.md](./spec/008-node-management-and-programming-agent-linux-harness.md) |
| LLM Provider 配置管理             | [009-llm-provider-management-spec.md](./spec/009-llm-provider-management-spec.md)                                         | [009-llm-provider-management-harness.md](./spec/009-llm-provider-management-harness.md)                                         |
| 任务创建 Spec Chat 多轮对话           | [010-task-creation-spec-chat-design.md](./spec/010-task-creation-spec-chat-design.md)                                     | [010-task-creation-spec-chat-harness.md](./spec/010-task-creation-spec-chat-harness.md)                                         |
| tilacode 执行层集成                | [012-tilacode-execution-layer-integration-spec.md](./spec/012-tilacode-execution-layer-integration-spec.md)               | [012-tilacode-execution-layer-integration-harness.md](./spec/012-tilacode-execution-layer-integration-harness.md)               |
| 编程 Agent 预编译二进制分发             | [013-programming-agent-binary-distribution-spec.md](./spec/013-programming-agent-binary-distribution-spec.md)             | [013-programming-agent-binary-distribution-harness.md](./spec/013-programming-agent-binary-distribution-harness.md)             |
| Agent 节点真实系统资源监控              | [014-agent-node-metrics-spec.md](./spec/014-agent-node-metrics-spec.md)                                                   | [014-agent-node-metrics-harness.md](./spec/014-agent-node-metrics-harness.md)                                                   |
| 节点显示名自定义 + Agent 类型更名为 `code` | [016-agent-node-display-name-and-code-type-spec.md](./spec/016-agent-node-display-name-and-code-type-spec.md)             | [016-agent-node-display-name-and-code-type-harness.md](./spec/016-agent-node-display-name-and-code-type-harness.md)             |
| 任务派发模式（手动 / 自动）+ 分配节点显示名称     | [017-task-dispatch-type-and-window-spec.md](./spec/017-task-dispatch-type-and-window-spec.md)                             | [017-task-dispatch-type-and-window-harness.md](./spec/017-task-dispatch-type-and-window-harness.md)                             |
| 中枢系统版本管理与更新日志                 | [018-system-version-management-spec.md](./spec/018-system-version-management-spec.md)                                     | [018-system-version-management-harness.md](./spec/018-system-version-management-harness.md)                                     |
| tilacode 品牌化路径重命名             | [019-tilacode-branding-spec.md](./spec/019-tilacode-branding-spec.md)                                                     | [019-tilacode-branding-harness.md](./spec/019-tilacode-branding-harness.md)                                                     |
| 中枢 Windows 桌面一体化部署            | [021-hub-windows-desktop-deployment-spec.md](./spec/021-hub-windows-desktop-deployment-spec.md)                           | [021-hub-windows-desktop-deployment-harness.md](./spec/021-hub-windows-desktop-deployment-harness.md)                           |
| Agent 资源云端分发与热更新              | [022-agent-resource-cloud-distribution-spec.md](./spec/022-agent-resource-cloud-distribution-spec.md)                     | [022-agent-resource-cloud-distribution-harness.md](./spec/022-agent-resource-cloud-distribution-harness.md)                     |
| 云端资源仓库管理（Tilapia-web × 腾讯云 COS） | [023-cloud-repository-management-spec.md](./spec/023-cloud-repository-management-spec.md)                         | [023-cloud-repository-management-harness.md](./spec/023-cloud-repository-management-harness.md)                                 |
| Code Agent 统一产物与 Nuitka 源码保护 | [024-agent-code-unified-artifact-spec.md](./spec/024-agent-code-unified-artifact-spec.md)                           | [024-agent-code-unified-artifact-harness.md](./spec/024-agent-code-unified-artifact-harness.md)                                   |
| Agent 资源按类型分发与更新地址云端下发 | [025-agent-resource-type-binding-spec.md](./spec/025-agent-resource-type-binding-spec.md)                     | [025-agent-resource-type-binding-harness.md](./spec/025-agent-resource-type-binding-harness.md)                                   |
| 版本发布管理与中枢更新                 | [026-release-management-spec.md](./spec/026-release-management-spec.md)                                       | [026-release-management-harness.md](./spec/026-release-management-harness.md)                                                     |
| 任务工作目录绑定与产物资源管理器            | [028-task-workspace-and-artifacts-spec.md](./spec/028-task-workspace-and-artifacts-spec.md)                   | [028-task-workspace-and-artifacts-harness.md](./spec/028-task-workspace-and-artifacts-harness.md)                                 |
| 任务执行模型选择与凭据中枢下发              | [029-task-executor-model-spec.md](./spec/029-task-executor-model-spec.md)                                   | [029-task-executor-model-harness.md](./spec/029-task-executor-model-harness.md)                                                   |
| 多模型管理与自定义 OpenAI 兼容接入         | [030-llm-models-management-spec.md](./spec/030-llm-models-management-spec.md)                               | [030-llm-models-management-harness.md](./spec/030-llm-models-management-harness.md)                                                |
| 中枢端口避让与 Agent 端口探测自愈         | [031-hub-port-avoidance-spec.md](./spec/031-hub-port-avoidance-spec.md)                                     | [031-hub-port-avoidance-harness.md](./spec/031-hub-port-avoidance-harness.md)                                                      |
| 中枢 AI 技能体系与智能调度               | [033-ai-skills-and-smart-dispatch-spec.md](./spec/033-ai-skills-and-smart-dispatch-spec.md)                   | [033-ai-skills-and-smart-dispatch-harness.md](./spec/033-ai-skills-and-smart-dispatch-harness.md)                                  |
| 跨 Agent 任务现场迁移与项目上下文记忆       | [034-task-handoff-context-spec.md](./spec/034-task-handoff-context-spec.md)                             | [034-task-handoff-context-harness.md](./spec/034-task-handoff-context-harness.md)                                                  |
| Agent 地址上报与中枢重启主动反连           | [035-agent-address-registry-reverse-notify-spec.md](./spec/035-agent-address-registry-reverse-notify-spec.md) | [035-agent-address-registry-reverse-notify-harness.md](./spec/035-agent-address-registry-reverse-notify-harness.md)                        |
| 会话内任务链自动挂接与规范文档按需生成           | [036-session-task-chain-spec.md](./spec/036-session-task-chain-spec.md)                                     | [036-session-task-chain-harness.md](./spec/036-session-task-chain-harness.md)                                                      |
| 三件套规范文档版本化目录                       | [037-agent-spec-docs-versioned-dir-spec.md](./spec/037-agent-spec-docs-versioned-dir-spec.md)               | [037-agent-spec-docs-versioned-dir-harness.md](./spec/037-agent-spec-docs-versioned-dir-harness.md)                                |
| 集群仪表盘 v2                              | [038-dashboard-v2-spec.md](./spec/038-dashboard-v2-spec.md)                                     | [038-dashboard-v2-harness.md](./spec/038-dashboard-v2-harness.md)                                  |
| 多模态附件与自动选模                        | [039-multimodal-attachments-spec.md](./spec/039-multimodal-attachments-spec.md)                | [039-multimodal-attachments-harness.md](./spec/039-multimodal-attachments-harness.md)              |
| 中枢 Agent Loop 改进路线图 | [040-orchestrator-agent-loop-roadmap.md](./spec/040-orchestrator-agent-loop-roadmap.md) | （P0 起逐项配套 Harness） |
| Orchestrator Agent Loop v1（轨迹回流+真流式） | [040-orchestrator-agent-loop-v1-spec.md](./spec/040-orchestrator-agent-loop-v1-spec.md) | [040-orchestrator-agent-loop-v1-harness.md](./spec/040-orchestrator-agent-loop-v1-harness.md) |
| 上下文管理 v1（窗口元数据+占用展示+压缩与历史回查） | [041-context-management-v1-spec.md](./spec/041-context-management-v1-spec.md) | [041-context-management-v1-harness.md](./spec/041-context-management-v1-harness.md) |
| 环境上下文注入 v1（日期时间+集群概况 Baseline/增量） | [042-context-environment-injection-spec.md](./spec/042-context-environment-injection-spec.md) | [042-context-environment-injection-harness.md](./spec/042-context-environment-injection-harness.md) |
| 执行保护 v1（技能超时+Doom Loop 检测） | [043-execution-guard-v1-spec.md](./spec/043-execution-guard-v1-spec.md) | [043-execution-guard-v1-harness.md](./spec/043-execution-guard-v1-harness.md) |
| LLM 客户端韧性 v1（重试退避+对话降级链+Cache 观测） | [044-llm-resilience-v1-spec.md](./spec/044-llm-resilience-v1-spec.md) | [044-llm-resilience-v1-harness.md](./spec/044-llm-resilience-v1-harness.md) |
| Steering v1（执行中插话：队列提升+配额重置） | [045-steering-v1-spec.md](./spec/045-steering-v1-spec.md) | [045-steering-v1-harness.md](./spec/045-steering-v1-harness.md) |
| 三件套生成协议重构（对话判定+三文档直出 Markdown） | [046-spec-builder-markdown-three-round-spec.md](./spec/046-spec-builder-markdown-three-round-spec.md) | [046-spec-builder-markdown-three-round-harness.md](./spec/046-spec-builder-markdown-three-round-harness.md) |
| 中枢日志系统 v1（运行日志分层+执行链路事件表+子 Agent 日志按需拉取） | [047-logging-v1-spec.md](./spec/047-logging-v1-spec.md) | [047-logging-v1-harness.md](./spec/047-logging-v1-harness.md) |
| 开发规则文档库与可选规则包下发 | [048-rule-docs-dispatch-spec.md](./spec/048-rule-docs-dispatch-spec.md) | [048-rule-docs-dispatch-harness.md](./spec/048-rule-docs-dispatch-harness.md) |
| 宝塔 MCP 部署一条龙服务 | [049-baota-mcp-deployment-spec.md](./spec/049-baota-mcp-deployment-spec.md) | [049-baota-mcp-deployment-harness.md](./spec/049-baota-mcp-deployment-harness.md) |
| 宝塔工具治理三层防线与经验记忆 | [050-bt-tool-governance-experience-memory-spec.md](./spec/050-bt-tool-governance-experience-memory-spec.md) | [050-bt-tool-governance-experience-memory-harness.md](./spec/050-bt-tool-governance-experience-memory-harness.md) |
| 前端亮色主题与颜色收敛 | [052-light-theme-spec.md](./spec/052-light-theme-spec.md)（前置调研：[052-light-theme-color-plan.md](./spec/052-light-theme-color-plan.md)） | [052-light-theme-harness.md](./spec/052-light-theme-harness.md) |
| 中枢 PTY 交互式终端与命令三档权限 | [053-hub-terminal-spec.md](./spec/053-hub-terminal-spec.md) | [053-hub-terminal-harness.md](./spec/053-hub-terminal-harness.md) |
| 压缩投影边界与摘要热前缀复用（P1） | [054-compaction-boundary-prefix-spec.md](./spec/054-compaction-boundary-prefix-spec.md) | [054-compaction-boundary-prefix-harness.md](./spec/054-compaction-boundary-prefix-harness.md) |
| LLM 调用参数与窗口口径校正（P2） | [056-llm-params-calibration-spec.md](./spec/056-llm-params-calibration-spec.md) | [056-llm-params-calibration-harness.md](./spec/056-llm-params-calibration-harness.md) |
| 工具清单结构化（宝塔 MCP 目录） | [057-bt-tools-inventory-spec.md](./spec/057-bt-tools-inventory-spec.md) | [057-bt-tools-inventory-harness.md](./spec/057-bt-tools-inventory-harness.md) |
| 工具轮预算改为进度驱动 | [058-tool-round-budget-spec.md](./spec/058-tool-round-budget-spec.md) | [058-tool-round-budget-harness.md](./spec/058-tool-round-budget-harness.md) |
| tilacode 前缀缓存命中率优化（P0–P4） | [060-tilacode-prefix-cache-spec.md](./spec/060-tilacode-prefix-cache-spec.md) | [060-tilacode-prefix-cache-harness.md](./spec/060-tilacode-prefix-cache-harness.md) |
| 编程执行层缺陷全量修复与进度可视化（P0–P2） | [055-execution-layer-hardening-spec.md](./spec/055-execution-layer-hardening-spec.md) | [055-execution-layer-hardening-harness.md](./spec/055-execution-layer-hardening-harness.md) |
| 调度准入与产物韧性（双门槛+排队重扫+增量同步+故障改派） | [059-scheduling-resilience-spec.md](./spec/059-scheduling-resilience-spec.md) | [059-scheduling-resilience-harness.md](./spec/059-scheduling-resilience-harness.md) |
| 子 Agent 全自主开发能力加固（M1–M7） | [061-agent-autonomy-hardening-spec.md](./spec/061-agent-autonomy-hardening-spec.md) | [061-agent-autonomy-hardening-harness.md](./spec/061-agent-autonomy-hardening-harness.md) |
| 办公 Agent 节点（opc-agent-office：文档/PPT/查资料） | [062-office-agent-spec.md](./spec/062-office-agent-spec.md) | [062-office-agent-harness.md](./spec/062-office-agent-harness.md) |
| 任务卡死检测与自动失败兜底 | [015-task-stale-detector-spec.md](./spec/015-task-stale-detector-spec.md) | [015-task-stale-detector-harness.md](./spec/015-task-stale-detector-harness.md) |
| 节点并发任务调度 | [032-concurrent-task-scheduling-spec.md](./spec/032-concurrent-task-scheduling-spec.md) | [032-concurrent-task-scheduling-harness.md](./spec/032-concurrent-task-scheduling-harness.md) |
| 部署 SOP 动态固化与坑地图精简 | [051-sop-knowledge-solidify-spec.md](./spec/051-sop-knowledge-solidify-spec.md) | [051-sop-knowledge-solidify-harness.md](./spec/051-sop-knowledge-solidify-harness.md) |
| 节点 Agent 会话轨迹镜像（群聊式折叠块 + `@节点` 对话） | [063-node-agent-trace-mirror-spec.md](./spec/063-node-agent-trace-mirror-spec.md) | [063-node-agent-trace-mirror-harness.md](./spec/063-node-agent-trace-mirror-harness.md) |
| 节点续聊对话（@空闲节点 = 真对话） | [064-node-chat-conversation-spec.md](./spec/064-node-chat-conversation-spec.md) | [064-node-chat-conversation-harness.md](./spec/064-node-chat-conversation-harness.md) |
| 办公节点交付闭环与能力增强（素材读取/Excel 与文档编辑另存/产出增强/交付质量闸 L0-L1-L2/轨迹镜像/检索闭环/成本护栏） | [065-office-delivery-loop-spec.md](./spec/065-office-delivery-loop-spec.md) | [065-office-delivery-loop-harness.md](./spec/065-office-delivery-loop-harness.md) |
| 任务终态自动唤醒编排（节点 completed/failed → 中枢自动核对交付物/推进待办/汇报/询问下一阶段） | [066-task-terminal-orchestrator-wake-spec.md](./spec/066-task-terminal-orchestrator-wake-spec.md) | [066-task-terminal-orchestrator-wake-harness.md](./spec/066-task-terminal-orchestrator-wake-harness.md) |
| 待办先行与派发确认门禁（中枢待办挂会话支持无任务先行 + tasks.dispatch_hold 挡住调度器自动派发） | [067-todo-first-dispatch-hold-spec.md](./spec/067-todo-first-dispatch-hold-spec.md) | [067-todo-first-dispatch-hold-harness.md](./spec/067-todo-first-dispatch-hold-harness.md) |
| 编排 AI 产物感知与文件访问（list_artifacts/read_file 技能 + Upload 文件源打通 + 本地打包兜底） | [068-orchestrator-artifact-access-spec.md](./spec/068-orchestrator-artifact-access-spec.md) | [068-orchestrator-artifact-access-harness.md](./spec/068-orchestrator-artifact-access-harness.md) |
| 前端 UI 盘点：任务域（继承 ADR-052；路径清单/往返流程图/页面清单/共用组件） | [052-ui-audit-task-domain.md](./spec/052-ui-audit-task-domain.md) | —（附属文档，无独立 harness） |
| 前端 UI 盘点：B 线交接说明（自包含，给新会话） | [052-ui-audit-handoff.md](./spec/052-ui-audit-handoff.md) | —（附属文档，无独立 harness） |

### 1.2 架构决策记录 ADR

| 序号                                                            | 标题                                       | 状态       |
| ------------------------------------------------------------- | ---------------------------------------- | -------- |
| [ADR-001](./adr/001-frontend-stack.md)                        | 前端技术栈选型                                  | Accepted |
| [ADR-002](./adr/002-backend-stack.md)                         | 后端技术栈选型                                  | Accepted |
| [ADR-003](./adr/003-database-stack.md)                        | 数据库与缓存选型                                 | Accepted |
| [ADR-004](./adr/004-communication-protocol.md)                | 节点通信协议选型                                 | Accepted |
| [ADR-005](./adr/005-file-storage.md)                          | 统一文件存储选型                                 | Accepted |
| [ADR-006](./adr/006-agent-node-deployment.md)                 | Agent 节点部署与接入方案                          | Accepted |
| [ADR-007](./adr/007-web-console-dashboard.md)                 | 前端管理控制台仪表盘                               | Accepted |
| [ADR-008](./adr/008-agent-node-linux-programming-mvp.md)      | Linux 编程 Agent 节点 MVP 与节点管理              | Accepted |
| [ADR-009](./adr/009-llm-provider-management.md)               | LLM Provider 配置管理                        | Accepted |
| [ADR-010](./adr/010-task-creation-spec-chat.md)               | 任务创建 Spec Chat 多轮对话构建                    | Accepted |
| [ADR-011](./adr/011-frontend-testing-framework.md)            | 前端测试框架选型（Vitest + RTL + Playwright）      | Accepted |
| [ADR-012](./adr/012-tilacode-execution-layer-integration.md)  | 基于 tilacode（原 opencode）集成编程执行层           | Accepted |
| [ADR-013](./adr/013-programming-agent-binary-distribution.md) | 编程 Agent 预编译二进制分发协议                      | Superseded（见 ADR-024/025） |
| [ADR-014](./adr/014-agent-node-metrics.md)                    | Agent 节点真实系统资源监控                         | Accepted |
| [ADR-015](./adr/015-task-stale-detector.md)                   | 任务卡死检测与自动失败兜底                            | Accepted |
| [ADR-016](./adr/016-agent-node-display-name-and-code-type.md) | 节点显示名自定义 + Agent 类型更名为 `code`            | Accepted |
| [ADR-017](./adr/017-task-dispatch-type-and-window.md)         | 任务派发模式（手动 / 自动）+ 分配节点显示名称                | Accepted |
| [ADR-018](./adr/018-system-version-management.md)             | 中枢系统版本管理与更新日志                            | Accepted |
| [ADR-019](./adr/019-tilacode-branding-rename.md)              | tilacode 品牌化路径重命名（.opencode → .tilacode） | Accepted |
| [ADR-020](./adr/020-agent-node-reconnection.md)               | Agent 节点应用内断线重连与任务对账机制                   | Accepted |
| [ADR-021](./adr/021-hub-windows-desktop-deployment.md)        | 中枢 Windows 桌面一体化部署（内嵌 PG + 桌面窗口）         | Accepted |
| [ADR-022](./adr/022-agent-resource-cloud-distribution.md)     | Agent 资源云端分发与热更新（与安装程序解耦）                | Superseded（见 ADR-024/025） |
| [ADR-023](./adr/023-cloud-repository-management.md)           | 云端资源仓库管理（Tilapia-web × 腾讯云 COS）              | Accepted |
| [ADR-024](./adr/024-agent-code-unified-artifact.md)          | Code Agent 统一产物与 Nuitka 源码保护                       | Accepted（key v2 见 ADR-025） |
| [ADR-025](./adr/025-agent-resource-type-binding.md)           | Agent 资源按类型分发与更新地址云端下发                       | Accepted |
| [ADR-026](./adr/026-hub-self-update.md)                       | 中枢自身升级机制与版本发布管理（检查提示+手动安装+日志云端化）                        | Accepted |
| [ADR-028](./adr/028-task-workspace-binding-and-artifact-sync.md) | 任务工作目录绑定与产物资源管理器（WS 实时清单+按需拉取）                | Accepted（D2 见 ADR-059） |
| [ADR-029](./adr/029-task-executor-model-dispatch.md)          | 任务执行模型选择与凭据中枢下发（免费模型降级选项）                     | Accepted |
| [ADR-030](./adr/030-llm-models-management.md)                 | 多模型管理与自定义 OpenAI 兼容接入（模型列表管理+按任务指定）             | Accepted |
| [ADR-031](./adr/031-hub-port-avoidance-and-agent-self-heal.md) | 中枢端口避让与 Agent 端口探测自愈（兜底窗口+端口可视化+URL 校准）    | Accepted |
| [ADR-032](./adr/032-concurrent-task-scheduling.md)             | 节点并发任务调度（资源水位预筛 + Agent 自评拒绝 + 改派等待）           | Accepted（判据见 ADR-059） |
| [ADR-033](./adr/033-ai-skills-and-smart-dispatch.md)           | 中枢 AI 技能体系与智能调度（Orchestrator 统一对话 + Skills + AI 派发决策） | Accepted |
| [ADR-034](./adr/034-task-handoff-context.md)                   | 跨 Agent 任务现场迁移与项目上下文记忆（handoff bundle + project_context.md） | Accepted |
| [ADR-035](./adr/035-agent-address-registry-reverse-notify.md)  | Agent 地址上报与中枢重启主动反连（并行探测 + 地址注册表 + notify API） | Accepted |
| [ADR-036](./adr/036-session-task-chain-and-spec-command.md)    | 会话内任务链自动挂接、工作目录可读命名与规范文档按需生成（含 /spec 命令）     | Accepted |
| [ADR-037](./adr/037-agent-spec-docs-versioned-dir.md)          | 三件套规范文档版本化目录（docs/specs/<标题-短ID>/，防覆盖可追溯）           | Accepted |
| [ADR-038](./adr/038-dashboard-v2-cluster-metrics-alerts-deliverables.md) | 集群仪表盘 v2（8 统计卡 + 三 Tab 左栏 + review 状态 + token 用量记录） | Accepted |
| [ADR-039](./adr/039-multimodal-attachments-model-auto-select.md) | 多模态附件与执行模型自动选择（图片/文件上传 + 视觉能力标记 + 自动选模） | Accepted |
| [ADR-040](./adr/040-orchestrator-agent-loop-v1.md) | 中枢 Orchestrator Agent Loop v1（工具轨迹持久化回流 + 多轮循环重构 + 真流式） | Accepted |
| [ADR-041](./adr/041-context-management-v1.md) | 中枢上下文管理 v1（窗口元数据 + token 计量占用展示 + 压缩与历史回查） | Accepted（修订见 ADR-054/056） |
| [ADR-042](./adr/042-context-environment-injection.md) | 环境上下文注入 v1（日期时间 + 集群概况 Baseline/增量注入） | Accepted |
| [ADR-043](./adr/043-execution-guard-v1.md) | Orchestrator 执行保护 v1（技能超时 + Doom Loop 检测） | Accepted |
| [ADR-044](./adr/044-llm-resilience-v1.md) | LLM 客户端韧性 v1（重试退避 + 对话降级链 + Prompt Cache 观测） | Accepted |
| [ADR-045](./adr/045-orchestrator-steering-v1.md) | Orchestrator Steering v1（执行中插话：队列提升 + 配额重置） | Accepted |
| [ADR-046](./adr/046-spec-builder-markdown-three-round.md) | 三件套生成协议重构（对话判定 + 三文档串行直出 Markdown，废除 JSON Mode 单调用） | Accepted |
| [ADR-047](./adr/047-logging-v1.md) | 中枢日志系统 v1（运行日志分层 + 执行链路事件表 + 子 Agent 日志按需拉取） | Accepted |
| [ADR-048](./adr/048-rule-docs-dispatch.md) | 开发规则文档库与可选规则包下发（文档管理页 + 任务绑定版本锁定 + 节点端按需加载索引） | Accepted |
| [ADR-049](./adr/049-baota-mcp-deployment.md) | 宝塔 MCP 部署一条龙服务（中枢直连多服务器管理 + 工具代理 + 部署技能） | Accepted |
| [ADR-050](./adr/050-bt-tool-governance-experience-memory.md) | 宝塔工具治理三层防线与经验记忆系统（SOP 白名单 + 参数名 schema 硬校验 + 经验记忆闭环） | Accepted |
| [ADR-051](./adr/051-sop-knowledge-solidify.md) | 部署 SOP 动态固化与坑地图精简（知识迁入记忆体系：sop_records 表 + sop_save 固化 + 坑地图常量） | Accepted |
| [ADR-052](./adr/052-light-theme.md) | 前端亮色主题与颜色收敛（14 语义 CSS 变量双态 + ConfigProvider 切算法 + 防回潮 CI） | Accepted |
| [ADR-053](./adr/053-hub-terminal-pty.md) | 中枢 PTY 交互式终端与命令执行三档权限（pywinpty + run_command 技能 + 手动审批/AI 自动/完全访问 + 前端终端抽屉自动展开） | Accepted |
| [ADR-054](./adr/054-compaction-boundary-and-prefix-reuse.md) | 压缩投影边界与摘要热前缀复用（P1：安全切点治 400 中断 + 摘要真前缀复用降本） | Accepted |
| [ADR-055](./adr/055-execution-layer-hardening.md) | 编程执行层缺陷全量修复与进度可视化（P0–P2：无进度看门狗 + 事件语义对齐 + 进度全链路可视化 + 安全边界） | Accepted |
| [ADR-056](./adr/056-llm-params-calibration.md) | LLM 调用参数与窗口口径校正（P2：窗口 64K→1M + 输出分档 8192→32768 + 思考模式显式化 + reasoning_content 回传） | Accepted |
| [ADR-057](./adr/057-bt-tools-inventory-compaction.md) | 工具清单结构化（宝塔 MCP 目录不再机械截断：紧凑全量 98/98 + 显式 bt_mcp_schema 恢复指令） | Accepted |
| [ADR-058](./adr/058-tool-round-budget-progress-driven.md) | 工具轮预算改为进度驱动（轮数上限 8→40 降级为安全网 + 新增「无新信息」进度检测 + 逼近上限警示） | Accepted |
| [ADR-059](./adr/059-scheduling-resilience-and-artifact-sync.md) | 调度准入与产物韧性（可用内存>1G 且 负载<80 双门槛 + 无自主候选即询问 + 10s 排队重扫 + 执行中增量产物同步 + 故障改派复用现场） | Accepted |
| [ADR-060](./adr/060-tilacode-prefix-cache.md) | tilacode 前缀缓存命中率优化（P0 可观测 → P1 system 拆稳定/易变块并把易变内容移尾 → P2 env 去波动 → P3 工具声明稳定化 → P4 压缩复用热前缀） | Accepted |
| [ADR-061](./adr/061-agent-autonomy-hardening.md) | 子 Agent 全自主开发能力加固（M1 干活纪律/LSP 回灌 → M2 权限语义收敛 → M3 防打转与会话级重试 → M4 可执行验收+完成闸 → M5 预算化与会话镜像 → M6 经验记忆下沉跨机复用 → M7 前端视觉反馈；一次只做一个模块） | Accepted（待逐模块确认后实施） |
| [ADR-062](./adr/062-office-agent-node.md) | 办公 Agent 节点（独立 office 类型 + 自研 LLM 工具循环：docx/pptx/DDG 检索/Scrapling 抓取 + 凭据中枢下发 + Linux 先行） | Accepted |
| [ADR-063](./adr/063-node-agent-trace-mirror.md) | 节点 Agent 会话轨迹镜像（群聊式折叠块 + `@节点` 对话：结构化会话视图 / 只读旁观 / 整体落库；M1 通道 → M2 采集 → M3 折叠块 → M4 `@节点` → M5 收尾） | Accepted（M1–M5 已完成，2026-10-02；D4 空闲分支已被 ADR-064 修订为节点续聊） |
| [ADR-064](./adr/064-node-chat-conversation.md) | 节点续聊对话（@空闲节点 = 真对话：下线建任务路径 + `hub.node_chat`/`agent.node_chat_reply` 契约 + seq 接续过程展示 + 消息持久化） | Accepted（M1–M4 全部完成，2026-10-05；真实环境 E2E 与浏览器实测通过） |
| [ADR-065](./adr/065-office-delivery-loop.md) | 办公节点交付闭环与能力增强（M1 素材读取 → M2 表格与编辑另存 → M3 产出增强 → M4 交付质量闸 L0/L1/L2 + 回灌 + `verify_blocked` → M5 轨迹镜像 → M6 检索闭环 → M7 成本护栏；一次只做一个模块） | Accepted（2026-10-05 立项，M0 文档完成，M1 起实施） |
| [ADR-066](./adr/066-task-terminal-orchestrator-wake.md) | 任务终态自动唤醒编排（completed/failed → 自动注入唤醒消息 → 后台编排轮：核对交付物/推进中枢待办/汇报/下一阶段询问确认；开关+同链限流+steering 互斥防环） | Accepted（2026-10-06 立项，用户裁决方案 A——平台自主性闭环） |
| [ADR-067](./adr/067-todo-first-dispatch-hold.md) | 待办先行与派发确认门禁（中枢待办挂会话支持无任务先行 + create_task 置 dispatch_hold 挡调度器 + smart_dispatch/手动派发清除） | Accepted（2026-10-07 立项，用户实测两截图裁决） |
| [ADR-068](./adr/068-orchestrator-artifact-access.md) | 编排 AI 产物感知与文件访问（list_artifacts/read_file 会话 scope 技能 + Upload 代执行文件源扩展 + 本地打包兜底） | Accepted（2026-10-08 立项，用户实测部署卡产物环节裁决） |

### 1.3 变更清单（Changes）

> 按 `.trae/rules/decision-documents.md` 要求，每份新决策需配套 `模块名-changes.md` 变更清单，开发过程中边开发边实时登记文件/函数级修改明细。历史决策（ADR-001 \~ ADR-018）不追溯补写。

| 模块                              | Changes 文档                                                                                          | 关联 ADR                                                    |
| ------------------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| tilacode 品牌化路径重命名               | [019-tilacode-branding-changes.md](./spec/019-tilacode-branding-changes.md)                                 | [ADR-019](./adr/019-tilacode-branding-rename.md)          |
| Agent 节点断线重连与任务对账               | [020-agent-node-reconnection-changes.md](./spec/020-agent-node-reconnection-changes.md)                     | [ADR-020](./adr/020-agent-node-reconnection.md)           |
| 中枢 Windows 桌面一体化部署              | [021-hub-windows-desktop-deployment-changes.md](./spec/021-hub-windows-desktop-deployment-changes.md)       | [ADR-021](./adr/021-hub-windows-desktop-deployment.md)    |
| Agent 资源云端分发与热更新                | [022-agent-resource-cloud-distribution-changes.md](./spec/022-agent-resource-cloud-distribution-changes.md) | [ADR-022](./adr/022-agent-resource-cloud-distribution.md) |
| 云端资源仓库管理（Tilapia-web × 腾讯云 COS） | [023-cloud-repository-management-changes.md](./spec/023-cloud-repository-management-changes.md) | [ADR-023](./adr/023-cloud-repository-management.md) |
| Code Agent 统一产物与 Nuitka 源码保护 | [024-agent-code-unified-artifact-changes.md](./spec/024-agent-code-unified-artifact-changes.md) | [ADR-024](./adr/024-agent-code-unified-artifact.md) |
| Agent 资源按类型分发与更新地址云端下发 | [025-agent-resource-type-binding-changes.md](./spec/025-agent-resource-type-binding-changes.md) | [ADR-025](./adr/025-agent-resource-type-binding.md) |
| 版本发布管理与中枢更新 | [026-release-management-changes.md](./spec/026-release-management-changes.md) | [ADR-026](./adr/026-hub-self-update.md) |
| 任务工作目录绑定与产物资源管理器 | [028-task-workspace-and-artifacts-changes.md](./spec/028-task-workspace-and-artifacts-changes.md) | [ADR-028](./adr/028-task-workspace-binding-and-artifact-sync.md) |
| 任务执行模型选择与凭据下发 | [029-task-executor-model-changes.md](./spec/029-task-executor-model-changes.md) | [ADR-029](./adr/029-task-executor-model-dispatch.md) |
| 多模型管理与自定义接入 | [030-llm-models-management-changes.md](./spec/030-llm-models-management-changes.md) | [ADR-030](./adr/030-llm-models-management.md) |
| 中枢端口避让与 Agent 探测自愈 | [031-hub-port-avoidance-changes.md](./spec/031-hub-port-avoidance-changes.md) | [ADR-031](./adr/031-hub-port-avoidance-and-agent-self-heal.md) |
| 节点并发任务调度 | [032-concurrent-task-scheduling-changes.md](./spec/032-concurrent-task-scheduling-changes.md) | [ADR-032](./adr/032-concurrent-task-scheduling.md) |
| 新建对话页输入底栏布局改版 | [010-task-creation-spec-chat-ui-layout-changes.md](./spec/010-task-creation-spec-chat-ui-layout-changes.md) | [ADR-010](./adr/010-task-creation-spec-chat.md) |
| 中枢 AI 技能体系与智能调度 | [033-ai-skills-and-smart-dispatch-changes.md](./spec/033-ai-skills-and-smart-dispatch-changes.md) | [ADR-033](./adr/033-ai-skills-and-smart-dispatch.md) |
| 跨 Agent 任务现场迁移与项目上下文记忆 | [034-task-handoff-context-changes.md](./spec/034-task-handoff-context-changes.md) | [ADR-034](./adr/034-task-handoff-context.md) |
| Agent 地址上报与中枢重启主动反连 | [035-agent-address-registry-reverse-notify-changes.md](./spec/035-agent-address-registry-reverse-notify-changes.md) | [ADR-035](./adr/035-agent-address-registry-reverse-notify.md) |
| 会话内任务链自动挂接与规范文档按需生成 | [036-session-task-chain-changes.md](./spec/036-session-task-chain-changes.md) | [ADR-036](./adr/036-session-task-chain-and-spec-command.md) |
| 三件套规范文档版本化目录 | [037-agent-spec-docs-versioned-dir-changes.md](./spec/037-agent-spec-docs-versioned-dir-changes.md) | [ADR-037](./adr/037-agent-spec-docs-versioned-dir.md) |
| 集群仪表盘 v2 | [038-dashboard-v2-changes.md](./spec/038-dashboard-v2-changes.md) | [ADR-038](./adr/038-dashboard-v2-cluster-metrics-alerts-deliverables.md) |
| 系统设置页布局改版 | [030-settings-ui-layout-changes.md](./spec/030-settings-ui-layout-changes.md) | ADR-030/031/033（UI 迭代，无新 ADR） |
| 产物面板 UI 迭代（目录树美化+可拖动分隔条+Markdown 渲染） | [028-artifacts-panel-ui-changes.md](./spec/028-artifacts-panel-ui-changes.md) | [ADR-028](./adr/028-task-workspace-binding-and-artifact-sync.md)（UI 迭代，无新 ADR） |
| 多模态附件与自动选模 | [039-multimodal-attachments-changes.md](./spec/039-multimodal-attachments-changes.md) | [ADR-039](./adr/039-multimodal-attachments-model-auto-select.md) |
| Orchestrator Agent Loop v1 | [040-orchestrator-agent-loop-v1-changes.md](./spec/040-orchestrator-agent-loop-v1-changes.md) | [ADR-040](./adr/040-orchestrator-agent-loop-v1.md) |
| 上下文管理 v1 | [041-context-management-v1-changes.md](./spec/041-context-management-v1-changes.md) | [ADR-041](./adr/041-context-management-v1.md) |
| 环境上下文注入 v1 | [042-context-environment-injection-changes.md](./spec/042-context-environment-injection-changes.md) | [ADR-042](./adr/042-context-environment-injection.md) |
| 执行保护 v1 | [043-execution-guard-v1-changes.md](./spec/043-execution-guard-v1-changes.md) | [ADR-043](./adr/043-execution-guard-v1.md) |
| LLM 客户端韧性 v1 | [044-llm-resilience-v1-changes.md](./spec/044-llm-resilience-v1-changes.md) | [ADR-044](./adr/044-llm-resilience-v1.md) |
| Steering v1 | [045-steering-v1-changes.md](./spec/045-steering-v1-changes.md) | [ADR-045](./adr/045-orchestrator-steering-v1.md) |
| 三件套生成协议重构 | [046-spec-builder-markdown-three-round-changes.md](./spec/046-spec-builder-markdown-three-round-changes.md) | [ADR-046](./adr/046-spec-builder-markdown-three-round.md) |
| 中枢日志系统 v1 | [047-logging-v1-changes.md](./spec/047-logging-v1-changes.md) | [ADR-047](./adr/047-logging-v1.md) |
| 开发规则文档库与可选规则包下发 | [048-rule-docs-dispatch-changes.md](./spec/048-rule-docs-dispatch-changes.md) | [ADR-048](./adr/048-rule-docs-dispatch.md) |
| 宝塔 MCP 部署一条龙服务 | [049-baota-mcp-deployment-changes.md](./spec/049-baota-mcp-deployment-changes.md) | [ADR-049](./adr/049-baota-mcp-deployment.md) |
| 宝塔工具治理三层防线与经验记忆 | [050-bt-tool-governance-experience-memory-changes.md](./spec/050-bt-tool-governance-experience-memory-changes.md) | [ADR-050](./adr/050-bt-tool-governance-experience-memory.md) |
| SOP 动态固化与坑地图精简 | [051-sop-knowledge-solidify-changes.md](./spec/051-sop-knowledge-solidify-changes.md) | [ADR-051](./adr/051-sop-knowledge-solidify.md) |
| 前端亮色主题与颜色收敛 | [052-light-theme-changes.md](./spec/052-light-theme-changes.md) | [ADR-052](./adr/052-light-theme.md) |
| 中枢 PTY 交互式终端 | [053-hub-terminal-changes.md](./spec/053-hub-terminal-changes.md) | [ADR-053](./adr/053-hub-terminal-pty.md) |
| 上下文管理 P0（计量校准+触发重构） | [041-context-management-p0-changes.md](./spec/041-context-management-p0-changes.md) | [ADR-041 修订 2026-09-27](./adr/041-context-management-v1.md) |
| 压缩投影边界与摘要热前缀复用（P1） | [054-compaction-boundary-prefix-changes.md](./spec/054-compaction-boundary-prefix-changes.md) | [ADR-054](./adr/054-compaction-boundary-and-prefix-reuse.md) |
| LLM 调用参数与窗口口径校正（P2） | [056-llm-params-calibration-changes.md](./spec/056-llm-params-calibration-changes.md) | [ADR-056](./adr/056-llm-params-calibration.md) |
| 工具清单结构化（宝塔 MCP 目录） | [057-bt-tools-inventory-changes.md](./spec/057-bt-tools-inventory-changes.md) | [ADR-057](./adr/057-bt-tools-inventory-compaction.md) |
| 工具轮预算改为进度驱动 | [058-tool-round-budget-changes.md](./spec/058-tool-round-budget-changes.md) | [ADR-058](./adr/058-tool-round-budget-progress-driven.md) |
| 编程执行层缺陷全量修复与进度可视化（P0–P2） | [055-execution-layer-hardening-changes.md](./spec/055-execution-layer-hardening-changes.md) | [ADR-055](./adr/055-execution-layer-hardening.md) |
| 调度准入与产物韧性（双门槛+排队重扫+增量同步+故障改派） | [059-scheduling-resilience-changes.md](./spec/059-scheduling-resilience-changes.md) | [ADR-059](./adr/059-scheduling-resilience-and-artifact-sync.md) |
| 调度准入 · D5「先询问」分支落地（待确认改派挂起态 `requeue_pending`） | [059-scheduling-resilience-d5-ask-branch.md](./spec/059-scheduling-resilience-d5-ask-branch.md) | [ADR-059](./adr/059-scheduling-resilience-and-artifact-sync.md) |
| tilacode 前缀缓存命中率优化（P0–P4） | [060-tilacode-prefix-cache-changes.md](./spec/060-tilacode-prefix-cache-changes.md) | [ADR-060](./adr/060-tilacode-prefix-cache.md) |
| 子 Agent 全自主开发能力加固（M1–M7） | [061-agent-autonomy-hardening-changes.md](./spec/061-agent-autonomy-hardening-changes.md) | [ADR-061](./adr/061-agent-autonomy-hardening.md) |
| 办公 Agent 节点（opc-agent-office） | [062-office-agent-changes.md](./spec/062-office-agent-changes.md) | [ADR-062](./adr/062-office-agent-node.md) |
| 节点 Agent 会话轨迹镜像 | [063-node-agent-trace-mirror-changes.md](./spec/063-node-agent-trace-mirror-changes.md) | [ADR-063](./adr/063-node-agent-trace-mirror.md) |
| 节点续聊对话 | [064-node-chat-conversation-changes.md](./spec/064-node-chat-conversation-changes.md) | [ADR-064](./adr/064-node-chat-conversation.md) |
| 办公节点交付闭环与能力增强 | [065-office-delivery-loop-changes.md](./spec/065-office-delivery-loop-changes.md) | [ADR-065](./adr/065-office-delivery-loop.md) |
| 任务终态自动唤醒编排 | [066-task-terminal-orchestrator-wake-changes.md](./spec/066-task-terminal-orchestrator-wake-changes.md) | [ADR-066](./adr/066-task-terminal-orchestrator-wake.md) |
| 待办先行与派发确认门禁 | [067-todo-first-dispatch-hold-changes.md](./spec/067-todo-first-dispatch-hold-changes.md) | [ADR-067](./adr/067-todo-first-dispatch-hold.md) |
| 编排 AI 产物感知与文件访问 | [068-orchestrator-artifact-access-changes.md](./spec/068-orchestrator-artifact-access-changes.md) | [ADR-068](./adr/068-orchestrator-artifact-access.md) |
| 设计 token 底座落地（尺寸档位/组件层/语义变量，继承 ADR-052） | [052-design-token-foundation-changes.md](./spec/052-design-token-foundation-changes.md) | [ADR-052](./adr/052-light-theme.md) |

## 二、开发规范（Standards）

| 文档                                                                             | 说明                                                 |
| ------------------------------------------------------------------------------ | -------------------------------------------------- |
| [coding-standards.md](./standards/coding-standards.md)                         | 代码风格、目录结构、命名规范                                     |
| [git-commit-standards.md](./standards/git-commit-standards.md)                 | Git 提交规范、分支策略                                      |
| [api-standards.md](./standards/api-standards.md)                               | 接口协议、响应格式、错误码                                      |
| [database-standards.md](./standards/database-standards.md)                     | 表/字段命名、索引、迁移规范                                     |
| [agent-node-standards.md](./standards/agent-node-standards.md)                 | Agent 节点开发、通信、日志、交付物规范                             |
| [logging-standards.md](./standards/logging-standards.md)                       | 日志级别、结构化日志、接口日志、敏感信息脱敏                             |
| [environment-config-standards.md](./standards/environment-config-standards.md) | 环境划分、配置管理、Pydantic Settings、生产安全                   |
| [frontend-component-standards.md](./standards/frontend-component-standards.md) | Ant Design 组件使用规则、前端组件文件组织、样式管理                    |
| [backend-component-standards.md](./standards/backend-component-standards.md)   | FastAPI、Pydantic、SQLAlchemy、Alembic、异步、Import、异常层级 |

## 三、架构设计（Architecture）

| 文档                                                                      | 说明                |
| ----------------------------------------------------------------------- | ----------------- |
| [system-architecture.md](./architecture/system-architecture.md)         | 四层系统架构设计          |
| [deployment-architecture.md](./architecture/deployment-architecture.md) | MVP 最小集群与生产演进部署架构 |
| [data-flow.md](./architecture/data-flow.md)                             | 任务全链路、心跳、迁移、文件数据流 |

## 四、模块设计（Modules）

| 模块    | 文档 |
| ----- | -- |
| （待补充） | -  |

## 五、运维与测试（Ops & Testing）

| 文档                                                             | 说明                            |
| -------------------------------------------------------------- | ----------------------------- |
| [hub-desktop-build-guide.md](./ops/hub-desktop-build-guide.md) | 中枢 Windows 桌面版构建发布指南（exe/安装包） |
| [code-agent-build-guide.md](./ops/code-agent-build-guide.md)   | Code Agent 整体包构建发布指南（Nuitka/Linux 服务器，ADR-024） |
| [adr061-acceptance-runbook.md](./ops/adr061-acceptance-runbook.md) | ADR-061 验收操作单（权限/验收闸/预算/用量/经验记忆，可复制执行） |
| [adr063-acceptance-runbook.md](./ops/adr063-acceptance-runbook.md) | ADR-063 验收操作单（轨迹落库/折叠块手测/@节点双语义/脱敏红线，可复制执行） |
| [e2e-dev-loop-runbook.md](./ops/e2e-dev-loop-runbook.md) | 端到端「开发→编译→更新→测试」循环操作手册（拓扑与免密、版本规则、构建/分发/节点 deploy 命令、踩坑清单；规则挂 `.trae/rules/e2e-dev-loop.md`） |
| [office-agent-acceptance-runbook.md](./ops/office-agent-acceptance-runbook.md) | 办公节点验收操作单（ADR-065 M1–M7：前置发布/本地自测/端到端差异/逐模块观察点/已知边界清单 B1–B10，可复制执行） |

## 六、文档更新记录

> 更新记录已外移至 [`docs/changelog/`](./changelog/)（按月分文件）。**新增记录请写入当月文件**，本文件不再内嵌更新记录（2026-10-05 文档体系改造，规则见 `.trae/rules/decision-documents.md`）。

## 七、问题排查记录（Issues）

线上/实测问题的排查记录，存放于 `docs/issues/`（命名 `YYYY-MM-DD-主题.md`）。每份记录包含现象、根因（代码级）、证据与修复建议；修复实施后在对应 ADR/Changes 登记明细。

| 日期 | 文档 | 状态 | 涉及问题 |
| --- | --- | --- | --- |
| 2026-10-05 | [2026-10-05-升级漏换tilacode导致任务秒失败-排查记录.md](./issues/2026-10-05-升级漏换tilacode导致任务秒失败-排查记录.md) | 已修复并实测 | 任务 4 秒内 `failed`（progress 0、`agent-trace` 0 行）：根因＝上次升级**手工只 `cp opc-agent`、漏换 `tilacode`**，节点留着 9月28 旧 tilacode（早于 2026-10-01 catalog 装配修复）→ 新增 C1 预检报 `tilacode_preflight_catalog_empty`。修复：用官方 `deploy-opc-agent.sh` 整体覆盖 `bin/`（含 tilacode）→ 复跑探针任务正常执行、轨迹 23 行。经验已入 Runbook §3.3/§4/§6.3 |
| 2026-10-05 | [2026-10-05-节点离线-注册竞速超时-排查记录.md](./issues/2026-10-05-节点离线-注册竞速超时-排查记录.md) | 已修复并发布 | 两 code 节点同时离线：真因＝节点注册竞速超时写死 1s，而「中枢与 DB 分机 + VPN 高 RTT」下注册端到端 2.6s → 必超时（`TimeoutError` str 空 → 日志 `error=` 空难排查）。修复：`endpoint_probe.py` 竞速超时 1s→10s + 失败日志补异常类型名；构建 0.6.0 发布两节点 |
| 2026-09-27 | [2026-09-27-子Agent待办不回传与任务卡10%-排查记录.md](./issues/2026-09-27-子Agent待办不回传与任务卡10%-排查记录.md) | 根因已修复，待节点部署 | 子 Agent 待办不回传/进度恒 10%：tilacode server 仅启动时快照用户级配置且不热重载，而 ADR-055 D3 的任务专属 provider（hub-<provider>-<task_id>）在任务执行时才写入用户级配置 → runTurn 报 ModelUnavailableError → 会话零事件 → 仅剩静默心跳。修复：provider 同步写入工作目录项目配置（tilacode 随会话动态加载），Agent 端测试 157 全过；节点为 Nuitka 二进制需走构建发布更新 |
| 2026-09-26 | [2026-09-26-思考工具堆叠与宝塔静态站不可见-排查记录.md](./issues/2026-09-26-思考工具堆叠与宝塔静态站不可见-排查记录.md) | 已定位根因，待修复 | ①Orchestrator 对话流「思考-工具」堆叠（前端回合分割只看 `last.content`，工具轮不封口，多轮思考/步骤全部合并）；②宝塔 MCP 建站「能访问但面板看不到」（HTML 静态项目与网站 PHP 项目是两个独立模块 + `HtmlProjectInfo` 参数名误用 + 工具族串用）；附「经验记忆系统」MVP 设计草案（experience_records 表 + 失败自动记录/收尾总结/命中注入闭环 + 前端 /memory 页面） |
| 2026-09-27 | [2026-09-27-上下文计量失真与压缩误触发-排查记录.md](./issues/2026-09-27-上下文计量失真与压缩误触发-排查记录.md) | 已定位根因；**P0 已实施并验证**（ADR-041 修订，103 项回归全过）；③④⑤ 留 P1/P2 | ①`estimate_messages_tokens` 相对 provider 真实 `prompt_tokens` **高估 1.66x**（实测 33,168 vs 20,137；密度 2.48~2.54 被按常数 2 计价），触发线 37,344 折算真实仅占窗口 34% → 「30% 多就压缩」；②触发判定读原始 `messages` 而非投影后 `prompt_messages`，压缩后估算不回落 → **越线后每轮空转**，且 `context_compacting` 事件在压缩前无条件下发 → 用户看到「每次都在压缩」；③压缩切点 `cutoff` 硬切进工具轮 → 投影首条为 `tool` 而父级 `assistant(tool_calls)` 被切掉 → provider 400「Messages with role 'tool' must be a response to a preceding message with 'tool_calls'」→ 对话直接中断（3/7 个已压缩会话踩中）；附带观测：摘要调用缓存命中率≈0、`max_tokens=8192` 已触顶 2 次、编排链路未下发 `reasoning_effort` |
| 2026-09-29 | **完成 ADR-061 全部模块（M1a/M2a/M2b/M3/M4/M5a/M5b/M5c/M5d/M6/M7）**「子 Agent 全自主开发能力加固」。**已交付**：M1a 干活纪律（提示词三级结构）、M2a 权限语义（unattended + 拒绝原因可读 + 节点去 blanket allow + 存量工作区自愈）、M2b 中枢下发权限策略（事件/服务/端点 + 节点只追加白名单 + 配置即缓存 + reject 带原因 + 审计）、M3 防打转与有上限重试、M4 验收闸（`verify.json` 分层判据 + 节点 L0~L4 执行器 + 完成闸 + 自修≤5轮 + `verify_blocked` 阻断态 + 对话区找人 + 24h 兜底 + 决策入口与前端决策 UI）、M5a 用量落库（`tool_call_logs.kind=llm_usage` + `tasks.progress_tool`）、M5b 预算闸（80% 软只发收尾指令 / 100% 硬停；step+token 双维度；幂等）、M5c 只读镜像（动作级时间线 + 用量曲线与 95.2% 基线对比 + 三条告警 + `@节点` 发言 + 仪表盘聚合）、M5d 中枢编排器待办（任务级独立表 + `todo_write` 技能 + 摘要下段只读渲染）、M6 经验记忆跨机复用（节点环境签名经心跳上报 + 同环境优先召回 + 元规则文档注入 + 定时整理只出草稿待确认）、M7 前端视觉反馈（节点浏览器探针：文本优先结构化反馈 + 退出码即判据 + `--shots` 可选截图）。**验证**：节点全量 **223 passed（exit 0）**、`ruff` 全过、`bun typecheck` 在 `packages/core` exit 0；中枢跨模块集合 **103 passed**（另修正 1 处用例期望）、涉及文件 `ruff` 全过；前端 `TaskSummary` 9 + `TaskInsightPanel` 6 + `TaskSummary` 决策/洞察用例全绿；9 个迁移全部在库生效（最新 `20260928i_experience_env`）。**登记待执行（用户 gated）**：G1 发布二进制（已构建未发布，发布须同时设 `OPENCODE_CHANNEL=local` 与 `OPENCODE_VERSION`）、G2 重打节点模板 tarball（含 M7 浏览器依赖烘焙 `PLAYWRIGHT_BROWSERS_PATH=0`）、G3 源码级 E2E（含 E2E-A1~A8 / IT-A3 / IT-A5，已授权待指定时机）。明细见 [ADR-061](./adr/061-agent-autonomy-hardening.md) 与其 Spec/Harness/Changes | AI助手 |
| 2026-09-29 | 新增 [ADR-061 验收操作单（Runbook）](./ops/adr061-acceptance-runbook.md)：把全部模块的**可执行验收步骤**固化成「复制命令 → 对照通过标准」的操作单（权限 deny / ask 两条路径、验收闸与阻断决策、无法验证、24h 兜底、预算闸 80%/100%、用量洞察与告警、中枢待办、经验跨机复用、M7 降级路径、回归命令、收尾复位），附**结果记录表**（回填 Harness 对应 `UT-*`）、优先级建议与「已知未真实验证项」清单 | AI助手 |
| 2026-10-01 | 定位并修复 **模型目录装配竞态**（`ModelUnavailableError` 真因）：`plugin/internal.ts` 的后台装配 → 改为 location 建好即可用；`state.ts` 单个 transform 失败不再整批作废；`models-dev.ts` 元数据不可用降级为 `{}`。同批新增 **C1 派发前预检**（`task_bridge._preflight_session_model`，缺 provider/模型立即失败）与 **C2 产物预检门禁**（`scripts/build-code-agent.sh`，Nuitka 前跑 `debug config`/`debug v2` 断言，含产物命名错配拦截）。记录见 [Runbook §15](./ops/adr061-acceptance-runbook.md) 与 [构建指南 §12](./ops/code-agent-build-guide.md) | AI助手 |

## 八、子项目（Subprojects）

### Tilapia-web（官网）

官网前后端分离项目，位于仓库根目录 `Tilapia-web/`，基于若依（RuoYi）v3.9.2 开源框架，与中枢（`opc-central-dispatch-hub/`）相互独立部署。

| 组成   | 目录                          | 技术栈                                                                    |
| ---- | --------------------------- | ---------------------------------------------------------------------- |
| 前端门户 | `Tilapia-web/ruoyi-portal/` | Vue 3 + Vite（面向访客的官网页面）                                                |
| 管理后台 | `Tilapia-web/RuoYi-Vue3/`   | Vue3 + Element Plus + Vite（若依标准管理后台）                                   |
| 后端   | `Tilapia-web/RuoYi-Vue/`    | Spring Boot + Spring Security + Redis + JWT + MyBatis（MySQL，Maven 多模块） |

项目自带文档（若依体系，位于 `Tilapia-web/doc/`）：

| 文档                                    | 说明                |
| ------------------------------------- | ----------------- |
| [项目介绍.md](../Tilapia-web/doc/项目介绍.md) | 前后端目录结构、配置文件、核心技术 |
| [前端手册.md](../Tilapia-web/doc/前端手册.md) | 若依前端开发手册          |
| [后台手册.md](../Tilapia-web/doc/后台手册.md) | 若依后台开发手册          |
| [插件集成.md](../Tilapia-web/doc/插件集成.md) | 若依插件集成说明          |
| [组件文档.md](../Tilapia-web/doc/组件文档.md) | 若依组件文档            |

> 关联决策：ADR-022 云端资源仓库后续将基于本项目落地，接口要求见 [022-agent-resource-cloud-repository-api.md](./spec/022-agent-resource-cloud-repository-api.md)。

