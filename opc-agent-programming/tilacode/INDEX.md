# tilacode 文档索引

本索引统一管理 tilacode 项目的所有文档。tilacode 基于 opencode（MIT 协议）二次整理，作为 OPC Agent 的编程执行层。

## 一、项目文档（中文，本次整理）

| 文档 | 路径 | 用途 |
|------|------|------|
| 项目说明 | [README.md](./README.md) | 项目简介、安装、Agent 介绍 |
| 架构概览 | [ARCHITECTURE.md](./ARCHITECTURE.md) | monorepo 包结构、核心模块、数据流、关键概念 |
| HTTP API 清单 | [API.md](./API.md) | tilacode server 所有 HTTP 端点、请求/响应格式、Python 对接示例 |
| 改造对接指南 | [INTEGRATION.md](./INTEGRATION.md) | OPC Agent 对接架构、启动、任务流程、改造点、检查清单 |
| 文档索引 | [INDEX.md](./INDEX.md) | 本文件 |

## 二、原始文档（英文，来自 opencode）

| 文档 | 路径 | 用途 |
|------|------|------|
| 开发规范 | [AGENTS.md](./AGENTS.md) | 代码风格、分支命名、提交规范、Effect/Schema 约定、V2 Session 核心规则 |
| Session 运行时概念 | [CONTEXT.md](./CONTEXT.md) | System Context、Context Epoch、Session Drain 等术语与关系 |
| 贡献指南 | [CONTRIBUTING.md](./CONTRIBUTING.md) | 如何参与贡献 |
| 安全策略 | [SECURITY.md](./SECURITY.md) | 安全漏洞报告流程 |
| 统计 | [STATS.md](./STATS.md) | 项目统计信息 |
| 协议声明 | [NOTICE](./NOTICE) | MIT 协议合规声明（tilacode 修改说明） |
| 许可证 | [LICENSE](./LICENSE) | 原始 MIT 许可证 |

## 三、子包文档

| 包 | 路径 | 用途 |
|----|------|------|
| opencode（主包） | [packages/opencode/README.md](./packages/opencode/README.md) | 主包说明 |
| opencode/AGENTS | [packages/opencode/AGENTS.md](./packages/opencode/AGENTS.md) | 主包开发指南 |
| llm | [packages/llm/README.md](./packages/llm/README.md) | LLM 适配层使用指南 |
| llm/AGENTS | [packages/llm/AGENTS.md](./packages/llm/AGENTS.md) | LLM 包架构、Route 构造、协议文件风格 |
| llm/DESIGN | [packages/llm/DESIGN.md](./packages/llm/DESIGN.md) | LLM 包设计文档 |
| client | [packages/client/README.md](./packages/client/README.md) | 生成客户端入口说明 |
| sdk-next | [packages/sdk-next/README.md](./packages/sdk-next/README.md) | 新版 SDK（Embedded OpenCode）说明 |
| codemode | [packages/codemode/README.md](./packages/codemode/README.md) | 受限 JS 执行环境说明 |
| codemode/AGENTS | [packages/codemode/AGENTS.md](./packages/codemode/AGENTS.md) | codemode 开发指南 |
| desktop | [packages/desktop/README.md](./packages/desktop/README.md) | 桌面应用说明 |
| app/AGENTS | [packages/app/AGENTS.md](./packages/app/AGENTS.md) | Web UI 开发指南 |
| app/e2e/AGENTS | [packages/app/e2e/AGENTS.md](./packages/app/e2e/AGENTS.md) | E2E 测试指南 |
| stats/AGENTS | [packages/stats/AGENTS.md](./packages/stats/AGENTS.md) | 统计 UI 开发指南 |
| schema/AGENTS | [packages/schema/AGENTS.md](./packages/schema/AGENTS.md) | Schema 包开发指南 |
| docs | [packages/docs/README.md](./packages/docs/README.md) | Mintlify 文档站源码 |
| docs/openapi | [packages/docs/openapi.json](./packages/docs/openapi.json) | OpenAPI Schema（可能滞后于代码） |

## 四、技术规格（specs/）

V2 技术规格文档，描述各模块的设计意图：

| 文档 | 路径 | 用途 |
|------|------|------|
| Session 规格 | [specs/v2/session.md](./specs/v2/session.md) | V2 Session 核心设计 |
| Tools 规格 | [specs/v2/tools.md](./specs/v2/tools.md) | 工具系统设计 |
| Config 规格 | [specs/v2/config.md](./specs/v2/config.md) | 配置系统设计 |
| Provider Model | [specs/v2/provider-model.md](./specs/v2/provider-model.md) | Provider 模型设计 |
| Provider Policy | [specs/v2/provider-policy.md](./specs/v2/provider-policy.md) | Provider 策略设计 |
| Instructions | [specs/v2/instructions.md](./specs/v2/instructions.md) | 指令系统设计 |
| Catalog 生命周期 | [specs/v2/catalog-config-plugin-lifecycle.md](./specs/v2/catalog-config-plugin-lifecycle.md) | Catalog/Config/Plugin 生命周期 |
| Schema 变更 | [specs/v2/schema-changelog.md](./specs/v2/schema-changelog.md) | Schema 变更记录 |
| Todo | [specs/v2/todo.md](./specs/v2/todo.md) | 待办事项 |
| TUI 包 | [specs/tui-package.md](./specs/tui-package.md) | TUI 包设计 |
| Project | [specs/project.md](./specs/project.md) | Project 概念设计 |
| 移除 opencode-db | [specs/storage/remove-opencode-db.md](./specs/storage/remove-opencode-db.md) | 数据库迁移设计 |
| Effect SQLite 包 | [specs/storage/effect-sqlite-package.md](./specs/storage/effect-sqlite-package.md) | Effect SQLite 封装设计 |

## 五、配置与运行时

| 文档/文件 | 路径 | 用途 |
|----------|------|------|
| 主配置示例 | [.opencode/opencode.jsonc](./.opencode/opencode.jsonc) | 配置文件示例 |
| 自定义 Agent | [.opencode/agent/](./.opencode/agent) | Agent 定义目录 |
| 自定义命令 | [.opencode/command/](./.opencode/command) | 斜杠命令定义 |
| 自定义工具 | [.opencode/tool/](./.opencode/tool) | 工具扩展 |
| 术语表 | [.opencode/glossary/](./.opencode/glossary) | 多语言术语表 |
| TUI 主题 | [.opencode/themes/](./.opencode/themes) | TUI 主题文件 |
| 插件 | [.opencode/plugins/](./.opencode/plugins) | 插件脚本 |
| TUI 配置 | [.opencode/tui.json](./.opencode/tui.json) | TUI 配置 |

## 六、OPC Agent 集成决策文档

以下文档位于项目根 `docs/` 目录（tilacode 目录的上级），记录 tilacode 与 OPC Agent 集成的决策：

| 文档 | 路径 | 用途 |
|------|------|------|
| ADR-012 | `docs/adr/012-tilacode-execution-layer-integration.md` | 集成决策记录 |
| Spec | `docs/spec/tilacode-execution-layer-integration-spec.md` | 集成规格（用户故事、流程、验收标准） |
| Harness | `docs/spec/tilacode-execution-layer-integration-harness.md` | 测试金字塔、E2E 场景 |

## 七、文档更新记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-08 | 初始创建：清理多语言 README，新增 ARCHITECTURE.md / API.md / INTEGRATION.md / INDEX.md | AI助手 |
