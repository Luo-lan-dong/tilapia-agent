# ADR-025：Agent 资源按类型分发与更新地址云端下发

## 状态

Accepted

## 日期

2026-09-08

## 问题背景

[ADR-024](./024-agent-code-unified-artifact.md) 落地单一整体包后，实际使用暴露三个结构性问题：

1. **安装脚本与 agent 类型无绑定**：清单中脚本是全局唯一条目（`install-script-linux`），而安装命令生成处（`agent_node_management_service.get_install_script`）的 `agent_type` 参数仅做白名单校验、不参与脚本 URL 拼接。将来扩展视频生成、办公类 agent 时，必然出现「安装 code 类型节点却分发其他类型安装脚本」的错配。不同类型 agent 的安装流程完全不同，脚本必须与类型绑定。
2. **更新粒度对用户不友好**：中枢按资源条目（key）展示差异并让用户逐条勾选，但用户心智模型是「哪个类型的 agent 有更新」。脚本作为配套产物不应单独决策更新与否，应随所属类型的整体包一起更新。
3. **资源仓库地址（manifest URL）在中枢本地配置**：地址实际由发布端（RuoYi 后台）决定（桶/前缀变了地址就变），却要在中枢页面手工同步修改，两端割裂且易配错。

### 需求（用户已确认）

1. 脚本上传时可选择绑定的 agent 类型；脚本 key/path 按类型隔离
2. 中枢只展示「哪种类型的 agent 有更新」，更新按类型一键应用（包+脚本自动一起）
3. 中枢不再显示/维护资源仓库地址；检查更新（自动/手动）时先连 RuoYi 管理后台获取最新地址（Token 认证），获取失败回退上次缓存的地址

## 决策

### 1. key 规范 v2：资源条目按 agent 类型命名

| 资源   | key 规范                    | path 规范                                                       | 示例                     |
| ---- | ------------------------- | ------------------------------------------------------------- | ---------------------- |
| 整体包  | `agent-{type}-{platform}` | `agent-{type}/{ver}/opc-agent-{type}-{ver}-{platform}.tar.gz` | `agent-code-linux-x64` |
| 安装脚本 | `install-script-{type}`   | `install-scripts/{type}/install-agent-{type}-linux.sh`        | `install-script-code`  |

- 中枢按 key 前缀解析 `(kind, type)`：`agent-` 后第一段为 type；`install-script-` 后第一段为 type。manifest 结构零改动（类型编码在 key 中），中枢消费端纯解析逻辑

- 脚本平台维度暂不进 key（当前只有 linux 脚本；将来出现 Windows agent 时升级为 `install-script-{type}-{platform}`，再走一次 key 迁移）

### 2. 更新粒度：按 agent 类型分组展示与一键应用

- 中枢 `get_status_view` 输出按 type 分组：每个类型聚合其整体包与脚本条目状态，类型级 `has_update`（任一条目过时即为有更新）

- 应用更新按类型：前端勾选类型 → 后端应用该类型全部条目（脚本随包自动更新，不单独决策）

- 分发防错配：安装命令生成处按 `agent_type` 拼接脚本 URL（`install-scripts/{type}/...`），并校验本地清单中 `install-script-{type}` 存在——未发布该类型脚本时明确报错，而非静默用别的脚本

### 3. 资源仓库地址由服务端统一下发（配置写死，用户零配置）

- **中枢配置（发布前手动改）**：`AGENT_RESOURCE_HUB_CONFIG_URL`（官网/服务端配置接口地址）与 `AGENT_RESOURCE_HUB_TOKEN`（对接 Token）写在中枢配置文件（.env / Pydantic Settings），打包 exe 前由开发者手动指向生产服务器。**不存数据库、不提供页面配置**——用户侧中枢零配置零展示，杜绝用户改错

- **服务端接口（RuoYi）**：`GET /portal/open/hub/config`（走官网门户公开路径 `/portal/open/**`，X-Hub-Token 认证）返回 `{ manifestUrl, ... }`；nginx 仅反代 `/portal/open/**` 到若依后端，后端 8080 不直接暴露公网

- **检查流程（自动/手动共用）**：先调服务端接口取最新地址 → 成功则写入 `agent_resource_manifest_url`（运行期缓存）→ 用该地址检查云端；服务端不可达时回退缓存地址继续检查；两者皆无 → 状态 failed 并明确提示

- **安全边界**：token 仅防滥用（防脚本乱扫），非安全边界——接口返回的仓库地址对应 COS 公有读桶（manifest 本就匿名可读）；泄露 token 无法登录若依后台（登录体系完全隔离）、无法写 COS（写密钥永不下发）

- 该接口同时承载中枢远程配置（品牌名等未来需求），统一为「服务端配置下发」入口

### 4. 迁移（一次性）

现有 `install-script-linux` 条目下架，重发为 `install-script-code`（path `install-scripts/code/install-agent-code-linux.sh`）；安装脚本同步更新路径。仅 code 一种类型，无历史节点兼容负担（0.5.0 尚未批量部署）。

## 备选方案

| 方案                               | 优势                            | 劣势                                          | 结论           |
| -------------------------------- | ----------------------------- | ------------------------------------------- | ------------ |
| **A（本决策）：key 编码类型 + Token 下发地址** | manifest 结构零改动；类型隔离彻底；地址单一事实源 | key 解析约定需两端遵守                               | ✅ 选中         |
| B：manifest 条目加 agent\_type 属性字段  | key 不变                        | manifest 结构变更加属性，两端适配；key 仍无类型语义，安装命令错配风险仍在 | ❌ 否决         |
| C：中枢继续本地维护地址 + 页面隐藏              | 改动最小                          | 地址变更仍需两端手工同步，未解决割裂                          | ❌ 否决         |
| D：中枢每次 check 实时拉地址且无缓存           | 地址永远最新                        | RuoYi 宕机则中枢更新完全瘫痪，无离线韧性                     | ❌ 否决（缓存回退更稳） |

## 决策原因

1. **防错配是硬需求**：类型进 key 与 path，从命名层面杜绝跨类型分发
2. **用户心智对齐**：运维关心「类型是否有更新」而非资源条目
3. **地址单一事实源**：桶/前缀只在 RuoYi 一处配置，中枢自动跟随
4. **向后兼容**：manifest 协议不变，已部署中枢仅升级解析逻辑

## 影响

### 对 ADR-022 的影响（修订）

- `agent_resource_manifest_url` 从「中枢手工配置项」降级为「RuoYi 下发的运行期缓存」；中枢配置项变为 hub\_api\_url + hub\_token

- 云端清单 key 约定更新：`install-script-linux` → `install-script-{type}`

- 状态视图新增按 type 分组结构，前端按类型展示

### 对 ADR-023/024 的影响（修订）

- RuoYi 发布页：脚本上传需选择绑定的 agent 类型；keyOptions 按「资源大类 × 类型」组织

- ADR-024 的 key 规范表同步为 v2

### 对中枢安装命令的影响

- 脚本 URL 按类型拼接 + 清单存在性校验（`install-scripts/{type}/install-agent-{type}-linux.sh`）

## 关联文档

- [ADR-022](./022-agent-resource-cloud-distribution.md)、[ADR-023](./023-cloud-repository-management.md)、[ADR-024](./024-agent-code-unified-artifact.md)

- [025-agent-resource-type-binding-spec.md](../spec/025-agent-resource-type-binding-spec.md)

- [025-agent-resource-type-binding-harness.md](../spec/025-agent-resource-type-binding-harness.md)

- [025-agent-resource-type-binding-changes.md](../spec/025-agent-resource-type-binding-changes.md)

## 变更记录

| 日期         | 变更内容                                                                                                                                                                                                                                                              | 变更人  |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 2026-09-08 | 初始创建：key v2 按类型命名 + 更新按类型分组一键应用 + 仓库地址 RuoYi Token 下发（缓存回退）                                                                                                                                                                                                       | AI助手 |
| 2026-09-09 | 修订决策 3：原「hub 地址/Token 存中枢 system\_settings + 页面配置」改为**配置文件写死**（AGENT\_RESOURCE\_HUB\_CONFIG\_URL/TOKEN，发布前手动改，用户零配置零展示）；服务端接口从 `/cloud/hub/manifest-url` 挪到官网公开路径 `/portal/open/hub/config`（nginx 仅反代 `/portal/open/**`，若依后端 8080 不暴露公网）；撤销中枢 hub 配置相关 DB 列与页面配置卡 | AI助手 |

