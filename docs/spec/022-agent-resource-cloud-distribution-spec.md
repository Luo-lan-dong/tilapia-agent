# Agent 资源云端分发与热更新 Spec

关联 ADR：[ADR-022](../adr/022-agent-resource-cloud-distribution.md)
关联 Harness：[022-agent-resource-cloud-distribution-harness.md](./022-agent-resource-cloud-distribution-harness.md)
关联 Changes：[022-agent-resource-cloud-distribution-changes.md](./022-agent-resource-cloud-distribution-changes.md)

## 1. 功能描述

Agent 分发资源（安装脚本、Agent 模板 tarball、tilacode 二进制）与 TilapiaHub 安装程序解耦：

1. 资源由云端仓库托管（HTTP 静态目录 + `manifest.json` 总清单，URL 可配置）
2. 中枢**定期检查**云端是否有新版本：**有更新只提示，不自动下载**
3. 用户在设置页**确认后**才下载更新到本地缓存 `data/resources/`，`/static` 改为服务缓存内容
4. 离线兜底：云端失败用缓存续用；全新离线安装用 zip 导入
5. Agent 节点侧零改动（下载协议与 URL 不变，ADR-013 保持）

**两阶段分离**：检查（check，自动）与应用（update，用户确认）是独立动作。

## 2. 用户故事

| 编号 | 用户故事 | 验收要点 |
|------|---------|---------|
| US-1 | 作为运维，中枢定期检查云端，有新版本模板时**提醒我**而不是自动更新 | revision 变化后状态变 update-available，设置页出现「有可用更新」提示，不发生下载 |
| US-2 | 作为运维，我在提示里看到本地/云端版本对比，**点击更新**才下载 | 点击更新后按选定资源下载落盘，`/static` 立即服务新内容 |
| US-3 | 作为运维，我想手动随时检查 | 「立即检查」按钮触发检查并显示结果 |
| US-4 | 作为运维，我想知道当前在服务哪个版本的资源 | 设置页展示当前 revision 与各资源版本/大小 |
| US-5 | 作为运维，内网无法联网时仍能更新资源 | 上传 zip 资源包，校验通过后生效（不经检查阶段） |
| US-6 | 作为用户，安装包应该轻量 | 安装程序不包含 static 资源 |
| US-7 | 作为开发者，源码模式行为不变 | 开发模式 `/static` 回退到仓库 static/，测试全绿 |

## 3. 核心流程

### 3.1 检查流程（check，自动+手动）

```
触发（启动后台 / 定时 6h 可配 / 设置页「立即检查」）
        │
        ▼
读取 agent_resource_manifest_url（system_settings，热更）
   ├─ 为空 ──► 状态=disabled，结束
        │
        ▼
下载 {url}/manifest.json（超时 30s）
   ├─ 失败 ──► 状态=offline（保留上次已知云端版本信息），结束
        │
        ▼
比对：云端各资源 sha256/version vs 本地缓存 manifest
   ├─ 全部一致 ──► 状态=up-to-date
   └─ 存在差异 ──► 状态=update-available
                    记录云端清单快照（供更新页展示对比），不下载任何资源
```

### 3.2 应用流程（update，用户确认后）

```
设置页展示差异列表（本地版本 vs 云端版本，按资源条目勾选）
        │ 用户勾选并点击「应用更新」
        ▼
逐资源下载选定项到 data/resources/.tmp/{path}
   ├─ 任一 sha256 校验失败 ──► 整体失败，清理 tmp，旧缓存保持完整
        │
        ▼
原子替换：tmp rename 到 data/resources/{path}
写入本地 manifest.json
        │
        ▼
状态=up-to-date，/static 立即服务新内容
```

### 3.3 `/static` 挂载选择

```
create_app 时：
data/resources/manifest.json 存在？ ──► StaticFiles(data/resources)
                  │ 否
                  ▼
resource_root/static 存在？ ──► StaticFiles(resource_root/static)（开发/离线基线回退）
                  │ 否
                  ▼
不挂载（Agent 安装接口 404，其余功能正常）
```

更新落盘后无需重启——挂载目录固定为缓存路径，内容变化即生效。

### 3.4 zip 导入流程（离线兜底）

```
设置页上传 zip ──► 校验（根含 manifest.json、逐资源 sha256 匹配）
   ├─ 失败 ──► 400 返回明确错误，不落盘
        │
        ▼
解压到 .tmp ──► 原子替换 ──► 写本地 manifest ──► 状态=up-to-date
```

## 4. 降级链路

| 场景 | 行为 |
|------|------|
| manifest URL 未配置 | 状态 disabled；缓存/回退基线继续服务 |
| 云端不可达 | 状态 offline；缓存续用；保留上次已知云端版本信息 |
| 检查发现新版本 | 仅提示（update-available），不下载 |
| 应用更新时任一资源校验失败 | 整体失败，旧缓存保持完整（无半更新） |
| 缓存为空且无回退基线 | Agent 安装接口 404，其余功能正常 |
| 更新进行中重复触发 | 进程内互斥锁跳过 |
| zip 结构非法 | 400 + 明确错误，不落盘 |

## 5. 接口定义

### GET /api/v1/settings/resources

```json
{
  "code": 0, "message": "ok",
  "data": {
    "status": "up-to-date|update-available|offline|failed|disabled|imported|syncing",
    "manifest_url": "https://resources.example.com/agent/",
    "local_revision": "2026-09-01T12:00:00Z",
    "cloud_revision": "2026-09-06T12:00:00Z",
    "last_checked_at": "2026-09-06T12:30:00Z",
    "resources": [
      {"key": "agent-template-programming", "local_version": "0.4.4", "cloud_version": "0.4.5",
       "cloud_path": "agent-templates/...", "cloud_sha256": "…", "size": 1234567,
       "present": true, "outdated": true}
    ]
  }
}
```

### PUT /api/v1/settings/resources/manifest-url

```json
{"url": "https://resources.example.com/agent/"}
```
写入 `system_settings.agent_resource_manifest_url`（空=禁用），并触发一次检查。

### POST /api/v1/settings/resources/check

触发检查（不下载），返回状态与差异列表。

### POST /api/v1/settings/resources/update

```json
{"keys": ["agent-template-programming", "install-script-linux"]}
```
应用更新：下载 keys 对应资源（keys 缺省=全部有差异资源），返回结果。

### POST /api/v1/settings/resources/import（multipart zip）

导入本地资源包，返回校验与落盘结果。

## 6. 配置项

| 键 | 位置 | 默认 | 说明 |
|----|------|------|------|
| agent_resource_manifest_url | system_settings（热更） | 空 | 云端 manifest URL；空=禁用检查 |
| RESOURCE_SYNC_INTERVAL_SECONDS | .env / env 注入 | 21600（6h） | 定时检查间隔；0=禁用定时；最小 60 |

## 7. 前端页面（系统设置 → 资源管理）

- 状态卡：状态 Tag（up-to-date / **有可用更新** / offline / disabled）+ 本地/云端 revision + last_checked_at
- 「有可用更新」时：资源差异列表（本地版本 → 云端版本），**默认勾选**过时条目 + 「应用更新」按钮
- 操作行：manifest URL 输入与保存、「立即检查」按钮、「导入资源包 zip」上传
- 更新中按钮 loading；结果 message 提示

## 8. 验收标准

1. 云端 revision 更新后，定时/手动检查使状态变 update-available 并出现提示，**无任何自动下载**
2. 用户在设置页点击「应用更新」后，`/static/agent-templates/...` 返回新内容，Agent 安装命令无需变化即用新模板
3. 云端不可达时中枢启动不受阻，状态 offline，缓存版本继续服务
4. 安装程序不包含 static（PyInstaller 产物无 `_internal/static`），体积显著下降
5. 全新环境（无缓存无云端）导入合法 zip 后 `/static` 正常服务
6. 应用更新时 sha256 不匹配 → 整体失败，旧缓存完整
7. 源码模式（未配置 URL、无缓存）回退仓库 static/，现有测试全绿
8. 更新进行中重复触发被互斥，不产生半更新
