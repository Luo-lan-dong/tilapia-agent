# ADR-026：中枢自身升级机制与版本发布管理（检查提示 + 手动安装）

## 状态

Accepted（2026-09-09 修订并启动实施）

## 日期

2026-09-08（初版）/ 2026-09-09（修订）

## 问题背景

资源链路已完全云端化（ADR-022/023/025），但仍有两个结构性问题：

1. **中枢 TilapiaHub 程序自身**的升级是原始方式：手动下载新版安装包、手动重装。版本管理（ADR-018 `system_versions` 表）只维护本地更新日志展示，无升级引导。
2. **更新日志数据源已失效且不可持续**：`system_versions` 表数据于 2026-09-09 发现丢失（迁移只建结构、初始数据靠手工脚本，且仅存的 v0.1.0 内容早已过时）。更新日志由中枢本地 Python 脚本维护的模式无法支撑多产品线（中枢 + 多类型 Agent）的发布节奏，用户也无法在管理后台自助编写。

### 需求（用户已确认，2026-09-09）

1. 在 RuoYi 管理后台新增「版本发布」菜单，统一编写/管理更新日志
2. 更新日志按产品线独立展示：中枢的更新日志独立、每个子 Agent 类型（如 code）的更新内容独立
3. 更新日志**完全云端化**（方案 A）：历史日志全部存 RuoYi，中枢本地仅保留一条当前运行版本号
4. 中枢能感知新版本并引导升级（检查提示 + 更新日志 + 下载链接，手动安装）
5. 中枢自身当前版本号定为 `v0.5.0`（与 Code Agent 0.5.0 时代对齐）

## 决策

### 1. RuoYi 新表 `hub_releases`：产品线维度的版本发布

| 字段                                                    | 类型                    | 说明                                                                       |
| ----------------------------------------------------- | --------------------- | ------------------------------------------------------------------------ |
| id                                                    | bigint autoincrement  | 主键                                                                       |
| product                                               | varchar(32) NOT NULL  | 产品线：`hub` / `agent-code` / 未来 `agent-video`…（与 ADR-025 key 规范中的 type 对齐） |
| version                                               | varchar(32) NOT NULL  | 版本号（中枢 `v0.5.0`；Agent `0.5.0`），`uk_product_version(product, version)` 唯一 |
| title                                                 | varchar(255) NOT NULL | 版本标题                                                                     |
| content                                               | text NOT NULL         | 更新内容（多行纯文本，前端按 pre-wrap 渲染）                                              |
| release\_date                                         | date NOT NULL         | 发布日期                                                                     |
| download\_url                                         | varchar(512) NULL     | 安装包下载地址（hub 产品线必填用于升级引导；agent 产品线可空——安装包走 ADR-022 资源仓库）                  |
| create\_by / create\_time / update\_by / update\_time | -                     | RuoYi 通用字段（物理删除，不设 del\_flag）                                            |

- 管理接口：`GET/POST/PUT/DELETE /cloud/release`（权限 `cloud:release:*`，管理后台登录态）

- 安装包上传：hub 产品线可上传 exe/zip 到 COS（复用 ADR-023 COS 配置与客户端），对象键 `hub-releases/{version}/{文件名}`，公有读，URL 写入 `download_url`

### 2. 中枢查询接口（Token 认证，官网公开路径）

`GET /portal/open/hub/releases`（header `X-Hub-Token`，与 ADR-025 `/portal/open/hub/config` 同路径组、同 Token）：

```json
{ "code": 200, "data": { "releases": [
  { "product": "hub", "version": "v0.5.1", "title": "...", "content": "...",
    "releaseDate": "2026-09-10", "downloadUrl": "https://.../hub-releases/..." }
] } }
```

按 `release_date` 倒序返回全部产品线版本；Token 错误 401。

### 3. 中枢更新日志完全云端化（修订 ADR-018）

- 更新日志页数据源切换为 RuoYi `hub_releases`：中枢新增 `GET /api/v1/settings/releases`，按产品线分组返回（`hub` → 「中枢」，`agent-code` → 「Code Agent」，未知 product 原样展示）

- **本地** **`system_versions`** **表降级为「当前运行版本号」单一载体**（仅保留一条 `is_current=true` 记录，初始 `v0.5.0`）；历史日志不再本地维护，本地表数据丢失不再影响功能

- RuoYi 不可达时：更新日志页展示错误提示（保留侧边栏版本号，来自本地表）

- Agent 产品线的版本号与资源管理页（ADR-025）云端清单版本可对照展示，帮助识别「该版本是否已在用」

### 4. 检查中枢更新（MVP：检查 + 提示 + 下载引导，不做自动替换安装）

```
RuoYi 管理后台                        中枢 TilapiaHub
┌──────────────────┐                ┌──────────────────────┐
│ hub_releases 表    │  ←上传安装包    │ 更新日志页「中枢」Tab    │
│ 版本号/说明/安装包  │ ─────────────► │ 「检查中枢更新」按钮     │
│ product=hub       │   Token 查询    │ → 比对本地当前版本(v0.5.0) │
└──────────────────┘                │ → 有新版：提示+更新日志    │
                                      │   +安装包下载链接         │
                                      │ → 用户手动下载运行安装包    │
                                      └──────────────────────┘
```

- 端点：`POST /api/v1/settings/check-hub-update`；服务端地址与 Token 来自配置文件（`AGENT_RESOURCE_HUB_CONFIG_URL` 去掉末尾 `/portal/open/hub/config` 得到 base，或直接同 base 拼接 `/portal/open/hub/releases`；`AGENT_RESOURCE_HUB_TOKEN`）——**复用 ADR-025 终态配置（配置文件写死，用户零配置），不存数据库、无页面配置**

- 版本比较：语义化比较（`packaging.version`，缺失时回退手写比较），云端 hub 最新版 > 本地 `is_current` 版本 → 有更新

- 安装动作由用户手动完成：下载安装包 → 运行 Inno Setup 安装程序（ADR-021，覆盖安装保留数据目录）

### 不做自动替换的原因（维持初版决策）

1. Windows 桌面程序运行中无法替换自身 exe（杀进程+替换+重启编排复杂、半替换变砖风险）
2. Inno Setup 安装器已处理数据目录保留、快捷方式等
3. 中枢是「大脑」，升级失败影响所有节点运维，人工确认成本可接受

## 备选方案

| 方案                                                    | 优势                    | 劣势                         | 结论         |
| ----------------------------------------------------- | --------------------- | -------------------------- | ---------- |
| **A（本决策）：hub\_releases 产品线统一发布 + 日志完全云端化 + 检查提示手动安装** | 单一数据源；多产品线天然扩展；本地无状态化 | RuoYi 不可达时日志不可看（侧边栏版本不受影响） | ✅ 选中（用户确认） |
| B：本地保留历史日志副本 + 云端双源合并                                 | RuoYi 宕机仍可看历史         | 双源同步复杂；本地丢数据问题复现（本次事故根源）   | ❌ 否决（用户确认） |
| C：agent 日志挂在资源发布页（分散入口）                               | 少一个菜单                 | 发布页已承载文件上传职责，日志入口分散难维护     | ❌ 否决（用户确认） |
| D：全自动更新（下载→退出→替换→重启）                                  | 全自动                   | Windows 自替换编排危险；半更新变砖风险    | ❌ 否决（远期可选） |
| E：仅本地更新日志展示（ADR-018 现状）                               | 零开发                   | 数据已丢失；无升级引导；无法多产品线分组       | ❌ 否决       |

## 决策原因

1. **数据源单一化**：本次 `system_versions` 数据丢失事故的直接根因是本地手工维护模式脆弱；云端化后中枢本地只剩一条当前版本号，无历史可丢
2. **产品线建模与 ADR-025 对齐**：`product` 取值与资源 key 的 type 一致，未来新增 agent 类型时发布/展示零改造
3. **复用已验证链路**：Token 认证（ADR-025 `/portal/open/**`）、COS 上传与公有读（ADR-023）、当前版本权威来源（ADR-018 is\_current）
4. **升级引导零风险**：检查+提示+手动安装，失败模式只有「不升级」，不存在「升坏」

## 影响

### 对 ADR-018 的影响（修订）

- `system_versions` 从「版本+更新日志存储」降级为「当前运行版本号」载体；历史日志数据源迁移至 RuoYi `hub_releases`

- 本地当前版本基线修正为 `v0.5.0`

- `GET /settings/versions` 保留（侧边栏当前版本号来源）

### 对 ADR-025 的影响

- `/portal/open/**` 公开路径组新增 `/portal/open/hub/releases`（同 Token 认证）；中枢侧配置零新增

### 对 ADR-023 的影响

- COS 新增 `hub-releases/` 前缀用途（hub 安装包）；发布端新增安装包上传能力

## 关联文档

- [ADR-018 系统版本管理](./018-system-version-management.md)、[ADR-021 桌面部署](./021-hub-windows-desktop-deployment.md)、[ADR-022](./022-agent-resource-cloud-distribution.md)、[ADR-023](./023-cloud-repository-management.md)、[ADR-025](./025-agent-resource-type-binding.md)

- [026-release-management-spec.md](../spec/026-release-management-spec.md)

- [026-release-management-harness.md](../spec/026-release-management-harness.md)

- [026-release-management-changes.md](../spec/026-release-management-changes.md)

## 变更记录

| 日期         | 变更内容                                                                                                                                                                                                                                                                         | 变更人  |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 2026-09-08 | 初始创建：检查+提示+下载引导（手动安装），不做自动替换                                                                                                                                                                                                                                                 | AI助手 |
| 2026-09-09 | 修订并启动实施：① `hub_releases` 增加产品线维度（product=hub/agent-code），统一承载中枢与全部 Agent 类型更新日志；② 更新日志完全云端化（修订 ADR-018，本地仅存当前版本号，起因：system\_versions 数据丢失事故）；③ Token/地址配置修正为 ADR-025 终态（配置文件写死，撤销初版 hub\_api\_url/hub\_token DB 配置引用）；④ 查询接口定为 `/portal/open/hub/releases`；⑤ 中枢当前版本基线 v0.5.0 | AI助手 |

