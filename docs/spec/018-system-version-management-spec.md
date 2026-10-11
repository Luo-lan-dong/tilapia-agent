# 中枢系统版本管理与更新日志 Spec

关联 ADR：[ADR-018](../adr/018-system-version-management.md)
关联 Harness：[018-system-version-management-harness.md](./018-system-version-management-harness.md)

## 1. 功能描述

在中枢系统中引入版本管理能力：

1. 版本记录（版本号、标题、更新内容、发布日期、是否当前版本）保存在数据库 `system_versions` 表中。
2. 前端侧边栏底部常驻显示当前版本号。
3. 系统设置新增「更新日志」二级菜单，页面展示所有版本记录并标记当前版本。
4. MVP 阶段版本记录为只读展示，数据由 Python 脚本维护。

## 2. 用户故事

| 编号 | 用户故事 | 验收要点 |
|------|---------|---------|
| US-1 | 作为使用者，我在打开控制台时，希望立即看到当前中枢版本号，以确认部署的是哪个版本 | 侧边栏底部显示 `vX.Y.Z`；接口异常时显示 `--` 且不报错 |
| US-2 | 作为使用者，我想查看系统的全部更新日志，了解每个版本新增/修复了什么 | 「系统设置 → 更新日志」页面按时间倒序列出所有版本 |
| US-3 | 作为使用者，我想一眼看出当前运行的是哪个版本 | 当前版本在列表中有「当前版本」标记 |
| US-4 | 作为运维，我希望发布新版本后只需更新数据库记录，前端即自动展示 | 新版本 `is_current=true` 插入后，侧边栏与更新日志页自动反映 |

## 3. 核心流程

### 3.1 版本数据读取流程

```
前端（AppLayout / Changelog 页面）
        │
        │  GET /api/v1/settings/versions
        ▼
FastAPI settings 端点
        │
        │  SystemVersionService.list_versions(session)
        ▼
PostgreSQL system_versions 表
  （按 release_date 倒序，created_at 倒序）
        │
        ▼
响应: ApiResponse<VersionResponse[]>
  { id, version, title, content, release_date, is_current }
```

### 3.2 侧边栏版本号展示逻辑

```
AppLayout 挂载
   │
   ├─ 请求成功且存在 is_current=true ──► 显示版本号（如 v0.1.0）
   │
   ├─ 请求成功但无 is_current 记录 ───► 显示 --（数据未初始化）
   │
   └─ 请求失败 ──────────────────────► 显示 --（静默降级，不阻塞菜单）
```

### 3.3 版本发布流程（脚本维护，MVP）

```
运维发布新版本
   │
   ▼
执行 Python 脚本（事务内）:
   1. UPDATE system_versions SET is_current=false WHERE is_current=true
   2. INSERT 新版本记录 (version, title, content, release_date, is_current=true)
   │
   ▼
前端下次请求即返回新当前版本
```

## 4. 降级链路

| 场景 | 行为 |
|------|------|
| `system_versions` 表为空 | 侧边栏显示 `--`；更新日志页显示 `a-empty` 空状态 |
| 接口 5xx / 网络异常 | 侧边栏显示 `--`；更新日志页显示错误提示，不影响其他菜单 |
| 多条 `is_current=true`（理论不可能，DB 部分唯一索引兜底） | 前端取列表中第一条 `is_current=true` 的记录 |

## 5. 接口定义

### GET /api/v1/settings/versions

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": [
    {
      "id": "uuid",
      "version": "v0.2.0",
      "title": "任务手动派发",
      "content": "新增手动派发模式...",
      "release_date": "2026-08-26",
      "is_current": true
    }
  ]
}
```

排序：`release_date` 倒序，同日期按 `created_at` 倒序。

## 6. 前端页面说明

### 6.1 侧边栏底部（AppLayout）

- 位置：`app-layout__sider-footer`。
- 内容：`vX.Y.Z`（小号文字，弱化视觉层级）。
- 版本号可点击，跳转 `/settings/changelog`。

### 6.2 更新日志页（/settings/changelog）

- 二级菜单：系统设置 → 更新日志（key: `settings-changelog`）。
- 布局：Ant Design `Timeline`，每项显示版本号（`Tag`）+ 标题 + 发布日期；展开区域显示更新内容（`white-space: pre-wrap` 纯文本，不渲染 HTML）。
- 当前版本项附加「当前版本」`Tag`（绿色）。
- 空数据：`Empty` 组件。

## 7. 验收标准

1. 执行迁移与初始数据脚本后，`GET /api/v1/settings/versions` 返回含 `v0.1.0`（`is_current=true`）的列表。
2. 侧边栏底部显示 `v0.1.0`。
3. 「系统设置」菜单展开后可见「更新日志」子项，点击进入页面并正确高亮。
4. 更新日志页展示所有版本，当前版本带「当前版本」标记。
5. 插入第二个版本（`is_current=true`）后，侧边栏与更新日志页自动反映新版本，旧版本标记消失。
6. 数据库层：尝试插入第二条 `is_current=true` 记录且未先将旧记录置 false 时，被部分唯一索引拒绝。
7. 停止后端后刷新页面，侧边栏显示 `--`，其余功能不受影响。
