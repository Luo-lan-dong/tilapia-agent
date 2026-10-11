# 版本发布管理与中枢更新 Spec（ADR-026 修订版）

关联 ADR：[ADR-026](../adr/026-hub-self-update.md)、[ADR-018](../adr/018-system-version-management.md)
关联 Harness：[026-release-management-harness.md](./026-release-management-harness.md)
关联 Changes：[026-release-management-changes.md](./026-release-management-changes.md)

## 1. 功能描述

1. **版本发布管理（RuoYi）**：后台新增「版本发布」菜单，按产品线（`hub` / `agent-code` / 未来扩展）录入版本号、标题、更新内容、发布日期；hub 产品线可上传安装包（存 COS）
2. **更新日志完全云端化（中枢）**：更新日志页数据源切换为 RuoYi `hub_releases`，按产品线分组展示（中枢 / Code Agent / …）；本地 `system_versions` 仅保留一条当前运行版本号（v0.5.0）
3. **检查中枢更新（ADR-026）**：中枢比对云端 hub 最新版本与本地当前版本，有新版则提示 + 展示更新日志 + 安装包下载链接，用户手动下载安装

## 2. 用户故事

| 编号 | 用户故事 | 验收要点 |
|------|---------|---------|
| US-1 | 作为运维，我在 RuoYi 后台选产品线录入版本与更新内容即可发布日志 | 「版本发布」页 CRUD；product+version 唯一约束 |
| US-2 | 作为运维，我发布中枢新版时能上传安装包供用户下载 | 上传存 COS `hub-releases/{version}/`，URL 自动写入 download_url |
| US-3 | 作为用户，我在中枢看到的是按产品分组的更新日志 | 中枢 / Code Agent 各自独立分组（Tabs），互不混杂 |
| US-4 | 作为用户，我想知道中枢是否有新版本可升级 | 「检查中枢更新」显示最新版与本地版比对结果；有新版时展示日志+下载链接 |
| US-5 | 作为用户，侧边栏始终能显示当前中枢版本 | v0.5.0 来自本地 `system_versions.is_current`；云端不可达不受影响 |
| US-6 | 作为运维，RuoYi 短暂不可达时中枢日志页不崩溃 | 展示错误提示与重试按钮，其他页面不受影响 |

## 3. 核心流程

### 3.1 版本发布（RuoYi 后台）

```
运维 → 版本发布页 → 新增
   ▼
选产品线（hub / agent-code）+ 版本号 + 标题 + 更新内容 + 发布日期
   ├─ hub 产品线：可选上传安装包 → COS hub-releases/{version}/ → download_url
   ▼
POST /cloud/release → 校验 product+version 唯一 → 入库
   ▼
中枢查询接口即时可见（无发布状态机，录入即可见）
```

### 3.2 中枢更新日志展示（分组）

```
更新日志页挂载
   ▼
GET /api/v1/settings/releases（中枢后端）
   ▼
后端 GET {base}/portal/open/hub/releases（X-Hub-Token，地址/Token 来自配置文件）
   ├─ 成功 ──► 按 product 分组返回
   │            { products: [ {product: "hub", label: "中枢", releases: [...]},
   │                           {product: "agent-code", label: "Code Agent", releases: [...]} ] }
   └─ 失败 ──► { products: [], error: "版本服务不可达..." }（前端展示错误 + 重试）
   ▼
前端 Tabs 按产品线渲染 Timeline（release_date 倒序）
```

### 3.3 检查中枢更新（ADR-026）

```
用户点击「检查中枢更新」（更新日志页 → 中枢 Tab 顶部）
   ▼
POST /api/v1/settings/check-hub-update
   ▼
后端拉取 hub 产品线最新版本（同 3.2 链路）
   ├─ 拉取失败 ──► { status: "failed", message }
   ▼
语义化版本比较（packaging.version）：latest vs 本地 is_current（v0.5.0）
   ├─ latest > current ──► { status: "update-available", latest, current,
   │                          title, content, download_url }（前端 Modal 展示 + 下载按钮）
   └─ latest ≤ current ──► { status: "up-to-date" }
```

### 3.4 本地当前版本维护

```
中枢发新版（如 v0.6.0）：
   ▼
运维执行 scripts/insert_current_version.py --version v0.6.0
   （事务内：旧 is_current 置 false → 插入新记录 is_current=true）
   ▼
侧边栏 / 检查更新基线自动切换
```

## 4. 接口定义

### 4.1 RuoYi 管理接口（登录态 + 权限 cloud:release:*）

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/cloud/release/list` | 分页列表，支持 product 筛选 |
| POST | `/cloud/release` | 新增（product+version 唯一校验） |
| PUT | `/cloud/release` | 修改 |
| DELETE | `/cloud/release/{ids}` | 删除（物理删除） |
| POST | `/cloud/release/upload` | hub 安装包上传（multipart），存 COS 返回 download_url |

### 4.2 RuoYi 中枢查询接口（X-Hub-Token）

`GET /portal/open/hub/releases`：

```json
{ "code": 200, "data": { "releases": [
  { "product": "hub", "version": "v0.6.0", "title": "...", "content": "...",
    "releaseDate": "2026-09-10", "downloadUrl": "https://.../hub-releases/v0.6.0/xxx.exe" }
] } }
```

Token 错误 401；空表返回空数组。

### 4.3 中枢新增端点

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| GET | `/api/v1/settings/releases` | 分组日志：`{ products: [{product, label, releases}], error? }` |
| POST | `/api/v1/settings/check-hub-update` | `{ status: update-available/up-to-date/failed, latest?, current?, title?, content?, downloadUrl?, message? }` |
| GET | `/api/v1/settings/versions` | 保留不变（侧边栏当前版本号） |

## 5. 降级链路

| 场景 | 行为 |
|------|------|
| RuoYi 不可达 / Token 错误 | 日志页 `error` 提示 + 重试按钮；检查更新返回 failed；侧边栏版本号不受影响 |
| `hub_releases` 无 hub 产品线记录 | 检查更新返回 failed（「中枢尚未发布过版本」） |
| `hub_releases` 为空 | 日志页各分组显示空状态 |
| 本地 `system_versions` 空表 | 侧边栏 `--`；检查更新 current 取 `packaging` 解析失败时按 0 处理（提示「本地版本未设置」） |
| agent 产品线版本与资源清单版本不一致 | 仅展示对照提示，不阻断（资源更新走 ADR-025 链路） |

## 6. 验收标准

1. RuoYi「版本发布」页可新增 hub / agent-code 两个产品线的版本记录，product+version 重复被拒绝
2. hub 产品线上传安装包后 COS 出现 `hub-releases/{version}/` 对象，download_url 正确
3. 无 Token / 错 Token 访问 `/portal/open/hub/releases` 返回 401；正确 Token 返回全部产品线版本
4. 中枢更新日志页按 Tabs 分组展示中枢与 Code Agent 的版本日志，日期倒序
5. RuoYi 发布 hub v0.6.0（> 本地 v0.5.0）后，中枢「检查中枢更新」提示有新版并给出下载链接
6. 停掉 RuoYi：日志页显示错误提示可重试；检查更新 failed；侧边栏仍显示 v0.5.0
7. 本地 `system_versions` 仅一条 v0.5.0（is_current=true）；侧边栏显示 v0.5.0
