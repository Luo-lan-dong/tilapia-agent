# OPC 项目 Git 提交规范

## 1. 分支模型

采用简化版 Git Flow，适合 MVP 阶段：

```
main
  ↑
develop
  ↑
feature/xxx
  ↑
hotfix/xxx
```

| 分支 | 用途 | 生命周期 |
|------|------|---------|
| `main` | 生产分支，保持稳定可发布 | 长期 |
| `develop` | 开发集成分支 | 长期 |
| `feature/<模块名>` | 新功能开发 | 临时，合并后删除 |
| `hotfix/<问题描述>` | 生产紧急修复 | 临时，合并后删除 |
| `docs/<描述>` | 文档变更 | 临时，合并后删除 |

## 2. Conventional Commits 规范

所有提交信息必须遵循 [Conventional Commits](https://www.conventionalcommits.org/)。

```
<type>(<scope>): <subject>

<body>

<footer>
```

### 2.1 type 类型

| 类型 | 含义 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(scheduler): 添加任务超时检测` |
| `fix` | 修复 Bug | `fix(agent): 修复心跳丢失后状态未更新` |
| `docs` | 文档变更 | `docs: 更新 API 接口规范` |
| `style` | 代码格式（不影响功能） | `style(web): 统一缩进` |
| `refactor` | 重构 | `refactor(hub): 拆分调度器职责` |
| `test` | 测试相关 | `test(scheduler): 添加任务分配单元测试` |
| `chore` | 构建/工具/依赖 | `chore(deps): 升级 FastAPI 版本` |
| `perf` | 性能优化 | `perf(db): 优化任务列表查询索引` |

### 2.2 scope 范围

| scope | 说明 |
|-------|------|
| `hub` | 中央调度中枢 |
| `web` | 前端控制台 |
| `agent` | Agent 节点通用 |
| `agent-programming` | 编程 Agent 节点 |
| `agent-video` | 视频 Agent 节点 |
| `agent-copywriting` | 文案 Agent 节点 |
| `db` | 数据库/模型 |
| `docs` | 文档 |
| `ci` | CI/CD |

### 2.3 subject 规范

- 不超过 50 个字符。
- 使用中文或英文（团队统一），本项目统一使用中文描述。
- 使用祈使句，不要加句号。
- 首字母不大写（英文场景）。

### 2.4 完整示例

```
feat(scheduler): 支持任务失败自动迁移

- 当节点离线或任务执行失败时，自动将任务迁移到同类型空闲节点
- 迁移前校验任务状态，避免重复执行
- 添加迁移日志和告警

Closes #12
```

## 3. 提交频率

- 一次提交只完成一个完整逻辑变更。
- 禁止把多个不相关改动塞进一个提交。
- 开发过程中可临时提交，合并前使用 `git rebase -i` 整理。

## 4. PR / MR 规范

- 每个功能必须通过 PR 合并到 `develop`。
- PR 标题与提交规范一致。
- PR 描述必须包含：变更目的、主要改动、测试方式、关联 ADR/Spec。
- 合并前必须通过 CI 检查（lint + test + 文件长度检查）。

## 5. 版本标签

- 发布版本使用 `v{主版本}.{次版本}.{补丁版本}`，如 `v0.1.0`。
- 重大版本发布前必须更新 CHANGELOG.md。

## 6. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-08-03 | 初始创建 | AI助手 |
| 2026-08-03 | PR 规范中明确 CI 检查包含文件长度检查 | AI助手 |
