# ADR-018：中枢系统版本管理与更新日志

## 状态

Accepted

## 日期

2026-09-02

## 问题背景

中枢系统（opc-central-dispatch-hub）自 2026-08 起持续迭代（任务调度、节点管理、LLM 配置、任务派发模式等），但存在以下问题：

1. **用户无法感知当前版本**：前端侧边栏没有任何版本标识，用户不知道自己部署的中枢是哪个版本。
2. **功能演进无历史可查**：各版本新增了哪些功能、修复了哪些问题，只能在 git 提交记录和 `docs/INDEX.md` 文档更新记录中翻找，普通使用者无法查看。
3. **无版本数据载体**：项目当前没有版本号常量、没有版本表，`pyproject.toml` 中也无 version 字段。

### 需求

1. 版本数据保存在数据库中（用户明确要求），不硬编码在前端。
2. 前端侧边栏显示当前版本号。
3. 系统设置中新增「更新日志」二级菜单，页面展示所有版本记录，并标记当前版本。
4. MVP 阶段版本记录为只读展示，数据通过 Python 脚本维护（符合项目「数据库操作用 Python 文件」规则）。

## 决策

### 1. 新建 `system_versions` 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| version | varchar(32) NOT NULL | 版本号，如 `v0.1.0`，全局唯一 |
| title | varchar(255) NOT NULL | 版本标题，如「首个版本」 |
| content | text NOT NULL | 更新内容（Markdown 文本） |
| release_date | date NOT NULL | 发布日期 |
| is_current | boolean NOT NULL DEFAULT false | 是否当前版本 |
| created_at / updated_at | timestamptz | 通用时间字段 |

约束与索引：

- `uk_system_versions_version`：`version` 唯一索引。
- `uk_system_versions_current`：**部分唯一索引** `CREATE UNIQUE INDEX ... ON system_versions (is_current) WHERE is_current = true`，数据库层保证全局只有一条当前版本记录（与 ADR-016 中 `(agent_type, name)` partial unique index 同一模式）。

### 2. 只读查询接口

`GET /api/v1/settings/versions`：返回版本列表，按 `release_date` 倒序、`created_at` 倒序排列。响应每项含 `id / version / title / content / release_date / is_current`。

- 无分页（版本数量预期为个位数到两位数）。
- 无写接口（POST/PATCH/DELETE 不提供，见备选方案 C）。
- 按 `logging-standards.md` 落结构化日志：`version_list_get_started` / `version_list_get_completed`。

### 3. 版本数据通过 Python 脚本维护

- 初始数据脚本：`opc-central-dispatch-hub/scripts/insert_initial_version.py`，插入首个版本 `v0.1.0`（`is_current=true`），幂等（先查后插，重复执行不报错、不重复插入）。
- 后续发新版本时：运维执行脚本（或 SQL）插入新版本记录并在同一事务中将旧版本 `is_current` 置为 false。脚本模式沿用初始脚本。

### 4. 前端改造

- **侧边栏底部**（`AppLayout.tsx` 的 `app-layout__sider-footer` 区域）：显示当前版本号（如 `v0.1.0`）。数据来自 `GET /settings/versions` 中 `is_current=true` 的记录；接口失败或无当前版本时显示 `--`，不阻塞布局。
- **系统设置二级菜单**：`menuItems` 中「系统设置」的 `children` 新增 `{ key: 'settings-changelog', label: '更新日志' }`。
- **更新日志页面**：新路由 `/settings/changelog` → `src/pages/Changelog.tsx`，使用 Ant Design `Timeline` 按时间倒序展示所有版本（版本号 + 标题 + 日期 + 更新内容），当前版本显示「当前版本」`Tag`。
- **菜单高亮修正**：`getSelectedKey` 对 `/settings/changelog` 前缀返回 `settings-changelog`，其余 `/settings` 路径返回 `settings-llm`（修复现有「统一高亮为大模型配置」的粗糙匹配）。

### 5. 版本号来源约定

数据库 `system_versions.is_current=true` 的记录是当前版本的唯一权威来源（Single Source of Truth）。前端不硬编码版本号，后端也不从 `pyproject.toml` 读取（避免双源不一致）。

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| A（本决策）：`system_versions` 表 + `is_current` 部分唯一索引 | 单表自洽，当前版本 DB 层强一致；查询简单；脚本可维护 | 新增一张表 | ✅ 选中（用户已确认） |
| B：`system_versions` 表 + 当前版本号存入现有 `system_settings` 键值 | 复用现有表 | 两处数据需同步，一致性维护复杂；`system_settings` 语义是"网络配置" | ❌ 否决（用户已确认否决） |
| C：提供完整管理界面（新增/编辑/发布版本） | 运维友好 | MVP 过度设计；发布版本是低频操作，脚本足够 | ❌ 否决（用户已确认） |
| D：版本号硬编码在前端常量/环境变量 | 实现最快 | 用户明确要求数据库存储；更新日志无载体；改版本要重新构建前端 | ❌ 否决 |
| E：当前版本从 `pyproject.toml` 读取，更新日志存表 | 版本号与包版本对齐 | 项目当前无 version 字段；双源（文件 + 表）仍需人工同步 | ❌ 否决 |

## 决策原因

1. **用户明确要求数据库存储**：版本与更新日志都是运营数据，不应硬编码。
2. **部分唯一索引保证一致性**：`is_current` 全局唯一由数据库兜底，应用层出错也不会出现两个"当前版本"。
3. **只读 MVP**：发布版本是低频操作（预计数周一次），脚本维护成本可控，避免为低频场景构建完整 CRUD 界面。
4. **侧边栏底部展示**：不占用菜单空间，符合现有布局（`app-layout__sider-footer` 已存在）。

## 影响

### 对系统架构的影响

- 新增 1 张表 + 2 个索引（含 1 个部分唯一索引）。
- 新增 1 个只读 GET 端点，挂在现有 `/settings` 路由下，无新路由文件（追加到 `endpoints/settings.py`）。
- 无 WebSocket、无调度器改动。

### 对开发的影响

**后端**：

- `app/models/system_version.py`：`SystemVersion` 模型。
- `alembic/versions/20260902000000_create_system_versions_table.py`：建表迁移。
- `app/schemas/system_version.py`：`VersionResponse`。
- `app/services/system_version_service.py`：`SystemVersionService.list_versions()`。
- `app/api/deps.py`：`get_system_version_service` 依赖。
- `app/api/v1/endpoints/settings.py`：追加 `GET /versions` 端点。
- `tests/api/v1/test_system_versions.py`：API 测试。

**前端**：

- `src/api/systemVersion.ts`：API 封装。
- `src/components/AppLayout.tsx`：sider-footer 版本号 + 二级菜单项 + 高亮逻辑修正。
- `src/pages/Changelog.tsx`：更新日志页面。
- `src/App.tsx`：注册 `/settings/changelog` 路由。

**脚本**：

- `scripts/insert_initial_version.py`：初始版本数据插入（幂等）。

### 对安全的影响

- 只读接口，无鉴权改动（与现有 `/settings` 接口一致，MVP 阶段不引入 RBAC）。
- `content` 字段前端渲染为纯文本（`whiteSpace: pre-wrap`），不渲染 HTML/Markdown，避免 XSS 注入面。

### 对运维的影响

- 升级后需执行迁移 + 初始数据脚本，否则侧边栏显示 `--`、更新日志页为空（均为优雅降级，不影响其他功能）。
- 发布新版本流程：插入新记录（`is_current=true`）+ 同事务将旧当前版本置 false → 前端侧边栏自动更新。

## 关联文档

- [ADR-016 节点显示名自定义 + Agent 类型更名为 code](./016-agent-node-display-name-and-code-type.md)（partial unique index 先例）
- [018-system-version-management-spec.md](../spec/018-system-version-management-spec.md)
- [018-system-version-management-harness.md](../spec/018-system-version-management-harness.md)
- 相关规范：[api-standards.md](../standards/api-standards.md) / [database-standards.md](../standards/database-standards.md) / [backend-component-standards.md](../standards/backend-component-standards.md) / [frontend-component-standards.md](../standards/frontend-component-standards.md) / [logging-standards.md](../standards/logging-standards.md)

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-02 | 初始创建：system_versions 表 + is_current 部分唯一索引 + 只读版本接口 + 侧边栏版本号 + 更新日志页面 | AI助手 |
| 2026-09-09 | 修订（由 ADR-026 驱动）：`system_versions` 从「版本+更新日志存储」降级为「当前运行版本号」单一载体（仅一条 is_current 记录，基线 v0.5.0）；更新日志历史数据源迁移至 RuoYi `hub_releases`（完全云端化）；`GET /settings/versions` 保留作为侧边栏当前版本号来源。起因：2026-09-09 发现 system_versions 数据丢失（迁移仅建结构、初始数据靠手工脚本）且内容过时 | AI助手 |
