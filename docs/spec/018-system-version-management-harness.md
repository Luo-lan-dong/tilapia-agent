# 中枢系统版本管理与更新日志 Harness

关联 ADR：[ADR-018](../adr/018-system-version-management.md)
关联 Spec：[018-system-version-management-spec.md](./018-system-version-management-spec.md)

## 1. 测试金字塔

```
        E2E（浏览器）          1 个场景：侧边栏版本号 + 更新日志页
       ─────────────────
      集成 / API 契约测试       后端 pytest：GET /settings/versions 契约 + 服务层
     ───────────────────────
    单元测试                  前端 Vitest：版本选择逻辑（可选，MVP 可由 E2E 覆盖）
   ─────────────────────────
```

## 2. 后端测试清单（pytest）

文件：`opc-central-dispatch-hub/tests/api/v1/test_system_versions.py`

| 编号 | 用例 | 断言 |
|------|------|------|
| T-01 | 空表返回空列表 | `code=0`，`data=[]` |
| T-02 | 插入 2 个版本后返回倒序列表 | 按 `release_date` 倒序；字段完整（id/version/title/content/release_date/is_current） |
| T-03 | `is_current` 标记正确 | 有且仅有一条 `is_current=true` |
| T-04 | 排序稳定性 | 同 `release_date` 按 `created_at` 倒序 |
| T-05 | 数据库约束：部分唯一索引 | 直接插入第二条 `is_current=true` 时 IntegrityError |
| T-06 | 数据库约束：version 唯一 | 重复 version 插入时 IntegrityError |

## 3. 前端测试清单

### 3.1 Vitest 单元测试（可选，MVP 由 E2E 兜底）

| 编号 | 用例 |
|------|------|
| F-01 | 版本列表中提取 `is_current=true` 项的纯函数逻辑 |
| F-02 | 列表为空时提取结果为 null（显示 `--`） |

### 3.2 E2E（浏览器手工/Playwright 验证）

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E-01 | 侧边栏版本号 + 更新日志页 | 1. 启动后端（已执行迁移与初始脚本）<br>2. 打开控制台<br>3. 展开系统设置菜单<br>4. 点击「更新日志」 | 1. 侧边栏底部显示 `v0.1.0`<br>2. 菜单含「更新日志」子项<br>3. 页面展示版本时间轴，`v0.1.0` 带「当前版本」标记<br>4. 菜单高亮在「更新日志」 |
| E-02 | 优雅降级 | 停止后端，刷新页面 | 侧边栏显示 `--`，无报错弹窗，其他菜单可用 |

## 4. CI 门禁

| 检查项 | 命令 | 标准 |
|--------|------|------|
| 后端 lint | `ruff check`（改动文件） | 0 error |
| 后端测试 | `pytest tests/api/v1/test_system_versions.py` | 全部通过 |
| 全量回归 | `pytest` | 全部通过，无既有用例回归 |
| 前端类型 | `tsc -b` | 0 errors |
| 前端 lint | `eslint`（改动文件） | 0 error |
| 文件长度 | `scripts/check_file_size.py` | 业务文件 ≤300 行 |

## 5. 数据验证（真实 PG）

| 编号 | 验证项 |
|------|--------|
| D-01 | 迁移执行后 `\d system_versions`：字段/索引/约束与 ADR-018 §决策 1 一致 |
| D-02 | 部分唯一索引存在：`pg_indexes` 中可查 `uk_system_versions_current`（含 WHERE 子句） |
| D-03 | 初始脚本执行后：1 条 `v0.1.0` 记录，`is_current=true`；重复执行不新增记录 |
