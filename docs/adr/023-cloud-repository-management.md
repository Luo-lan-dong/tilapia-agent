# ADR-023：云端资源仓库管理（Tilapia-web × 腾讯云 COS）

## 状态

Accepted

## 日期

2026-09-06

## 问题背景

[ADR-022](./022-agent-resource-cloud-distribution.md) 决策 Agent 资源由云端仓库托管，并在 [022-agent-resource-cloud-repository-api.md](../spec/022-agent-resource-cloud-repository-api.md) 中定义了云端仓库的接口要求：

1. 云端仓库是**纯静态 HTTP 文件目录**（manifest.json + install-scripts/ + agent-templates/ + binaries/），无 API 逻辑，中枢（TilapiaHub）是唯一拉取客户端
2. 资源发布需要：上传文件到仓库、计算 sha256/size、更新 manifest.json 的 revision
3. 大文件（tilacode 二进制 \~130MB）要求支持流式读取

中枢侧的同步消费能力（ResourceSyncService）已在 ADR-022 落地完成，**但云端仓库本身尚不存在**——需要回答：仓库托管在哪、文件如何上传发布、manifest.json 如何维护。

新增的 Tilapia-web 官网项目（若依 RuoYi-Vue 3.9.2，Spring Boot 4.1.0 + JDK 17 + MySQL `tilapiaweb` 库 + RuoYi-Vue3 管理后台）具备现成的管理后台框架与权限体系，适合承载资源发布管理职责。

### 需求（用户已确认）

1. 资源文件实际上传到**腾讯云对象存储（COS）**
2. 上传功能在**管理后台**实现（页面上传，非脚本/控制台手工操作）
3. COS 存储桶的连接信息（SecretId/SecretKey/Bucket/Region 等）**保存在数据库**，可在管理后台随时配置修改

## 决策

### 1. 托管：COS 存储桶直连（公有读私有写）

- 云端仓库 `{ROOT}` = `https://{bucket}.cos.{region}.myqcloud.com/{base_prefix}/`（如 `https://xxx.cos.ap-guangzhou.myqcloud.com/agent/`）

- 桶权限：**公有读私有写**——满足 repository-api 规范「中枢拉取为匿名 GET」；写操作仅通过后端持有的密钥（SecretId/SecretKey）

- 目录结构、manifest.json 规范完全遵循 [022-agent-resource-cloud-repository-api.md](../spec/022-agent-resource-cloud-repository-api.md)（sha256 小写 64 位、path 相对无 `..`、revision 为 UTC ISO 时间戳等）

- 中枢侧零改动：在 TilapiaHub 系统设置中把 `agent_resource_manifest_url` 配置为 `{ROOT}` 即可接入

### 2. 管理后台新增「云端资源」模块（RuoYi 体系内实现）

两个页面：

| 页面     | 功能                                                                                                                       |
| ------ | ------------------------------------------------------------------------------------------------------------------------ |
| COS 配置 | SecretId/SecretKey/Bucket/Region/路径前缀（base\_prefix，默认 `agent/`）表单，存 MySQL 单行表 `cloud_cos_config`；「测试连接」按钮（headBucket 验证） |
| 资源发布   | 展示 COS 上当前 manifest.json（key/版本/大小/revision）；上传新资源（选资源 key + 填版本号 + 选文件）→ 发布；展示供复制的中枢配置 URL                              |

资源 key 固定三种（与 repository-api 规范一致）：`install-script-linux` / `agent-template-programming` / `tilacode-linux-x64`。

### 3. 上传链路：后端中转（浏览器 → RuoYi 后端 → COS）

- 后端接收 multipart 文件后**流式转存** COS（边读边算 sha256、传 COS），不整包缓冲内存

- Spring Boot multipart 上限调大（max-file-size 400MB / max-request-size 420MB）

- 凭证不出后端：浏览器只与 RuoYi 后端通信，COS SecretKey 不下发前端

### 4. 发布流程（manifest 维护自动化）

发布一个资源 = 后端一次原子性序列动作：

```
校验 COS 配置可用
  → 流式处理：本地临时落盘/管道，计算 sha256 与 size
  → 按 key+version 生成目标 path（遵循 repository-api 目录结构）
  → 上传资源文件到 COS（覆盖同 path 幂等）
  → 拉取 COS 上的 manifest.json（不存在则创建空清单）
  → 更新对应 key 条目（path/sha256/size/version）+ 根 revision = 当前 UTC ISO 时间戳
  → 上传 manifest.json 到 COS
  → 返回发布结果（含新 revision 与 manifest_url）
```

- revision 由后端自动 bump，中枢下次检查（≤6h 或手动）即发现新版本

- 中途失败可重发：同 path 覆盖上传，幂等

- 旧版本文件保留在桶内（manifest 只指向最新，不影响正确性；MVP 不做清理，作为后续迭代项）

### 5. 配置存储：MySQL 独立单行表

```sql
cloud_cos_config（config_id PK 单行，含 secret_id、secret_key、bucket_name、region、base_prefix）
```

- 不复用若依 `sys_config` 键值表（结构化字段 + 权限管控 + 单行约束）

- SecretKey **明文存库**（MVP 决策）：数据库本身处于受控网络（192.168.101.104 内网），接口返回时脱敏；后续迭代可引入应用层加密

- 未配置时发布/查询接口明确报错引导先配置

## 备选方案

| 方案                                | 优势                                                     | 劣势                                | 结论              |
| --------------------------------- | ------------------------------------------------------ | --------------------------------- | --------------- |
| **A（本决策）：COS 直连 + 管理后台发布**        | 完全符合 repository-api「纯静态目录+匿名 GET」；大文件不流经 Java 后端；发布即生效 | 桶需公有读                             | ✅ 选中            |
| B：RuoYi 后端代理中枢下载                  | 桶可私有                                                   | 违反规范（非静态直连）；130MB 流经后端；hub 依赖后端存活 | ❌ 否决            |
| C：前端 STS 临时凭证直传 COS               | 大文件不占后端带宽                                              | 需实现 STS 签发+回调确认，复杂度高；内网管理后台收益有限   | ❌ 否决（保留为后续迭代方向） |
| D：手工 coscli/控制台上传 + 脚本生成 manifest | 零开发                                                    | 无管理后台界面，密钥在个人手上，与用户需求冲突           | ❌ 否决            |
| E：其他对象存储/自建 nginx                 | -                                                      | 用户已确定腾讯云 COS                      | ❌ 否决            |

## 决策原因

1. **规范符合性**：COS 直连完全满足 ADR-022 与 repository-api 的纯静态目录 + 匿名 GET + 流式下载要求，中枢侧零改动
2. **职责清晰**：Tilapia-web 管理后台只做「发布端」，不承担下载流量
3. **密钥集中**：COS 密钥只在后端数据库 + 后端内存中出现，前端展示脱敏
4. **发布自动化**：sha256/size/revision 由后端自动维护，杜绝手写出错（对齐 hub 侧 `scripts/build_cloud_manifest.py` 的设计意图）
5. **复用若依体系**：权限（`cloud:*:*`）、菜单、CRUD 分层（controller/service/mapper）全部沿用框架既有模式

## 影响

### 对 ADR-022 的影响

- 云端仓库从「规划中的自建静态目录」落地为「腾讯云 COS + 管理后台发布」，manifest URL 形态确定为 COS URL；中枢消费协议不变

### 对 Tilapia-web（RuoYi）的影响

- 后端 `RuoYi-Vue`：新增 `cos_api` SDK 依赖；新增 `cloud_cos_config` 表（建表 SQL）；新增「云端资源」业务模块（controller/service/mapper/domain）；multipart 上限调大

- 前端 `RuoYi-Vue3`：新增「云端资源」一级菜单（COS 配置 + 资源发布两页）

- 安全影响：新增接口全部挂若依权限注解（`@RequiresPermissions`）；SecretKey 接口返回脱敏；上传发布为管理员级操作

### 对运维的影响

- 首次使用：建表 → 管理后台填 COS 配置 → 测试连接 → 上传初始资源（安装脚本/模板/二进制）

- 发布新版本：资源发布页上传即可，无需登录腾讯云控制台

- 中枢接入：复制资源发布页展示的 manifest\_url 配到 TilapiaHub 系统设置

## 关联文档

- [ADR-022 Agent 资源云端分发与热更新](./022-agent-resource-cloud-distribution.md)

- [022-agent-resource-cloud-repository-api.md](../spec/022-agent-resource-cloud-repository-api.md)（云端仓库接口规范，本决策的实现依据）

- [023-cloud-repository-management-spec.md](../spec/023-cloud-repository-management-spec.md)

- [023-cloud-repository-management-harness.md](../spec/023-cloud-repository-management-harness.md)

- [023-cloud-repository-management-changes.md](../spec/023-cloud-repository-management-changes.md)

## 变更记录

| 日期         | 变更内容                                                | 变更人  |
| ---------- | --------------------------------------------------- | ---- |
| 2026-09-06 | 初始创建：COS 直连托管 + 若依管理后台发布（配置存库/后端中转上传/manifest 自动维护） | AI助手 |

