# 任务工作目录绑定与产物资源管理器 Harness

关联 ADR：[ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md) / Spec：[028-task-workspace-and-artifacts-spec.md](./028-task-workspace-and-artifacts-spec.md)

## 1. 测试金字塔

```
        E2E（Playwright）
        ├─ 新建任务关联已有任务 → 派发 payload 含迭代链
        ├─ 产物 Tab 浏览/预览/下载（mock Agent 事件）
        └─ 发送等待气泡 + 右侧收起展开
       集成（pytest API / vitest）
        ├─ tasks.output_dir 迁移与 API 校验（202012）
        ├─ artifacts tree/file/download 三接口
        ├─ agent.file_tree / file_chunk WS 消息处理
        ├─ useTaskSessionActions 关联任务参数
        └─ AIThinkingBubble / TaskNewRightPanel 折叠态
       单元（pytest / vitest）
        ├─ 路径合法性校验函数
        ├─ 清单 manifest 读写与 truncated 标记
        ├─ 分块组装器（乱序/重复/超时）
        └─ 排除规则 glob 匹配
```

## 2. 单元测试清单

### hub（tests/services/、tests/api/v1/）

| 用例 | 断言 |
|------|------|
| test_task_output_dir_validation | 空串/含 `..`/反斜杠规范化 → 422 + 202012；合法相对/绝对路径通过 |
| test_task_related_task_binding | related_task_id 存在 → root_task_id 沿用；不存在 → 404 + 201001 |
| test_dispatch_payload_output_dir | scheduler._build_dispatch_message 含 output_dir（仅显式指定时） |
| test_file_manifest_store | file_tree 事件写 .manifest.json；truncated 标记保留 |
| test_file_chunk_assembler | 乱序/重复分块按 seq 组装；done 落盘；超时清理 request_id |
| test_artifacts_tree_api | 返回缓存清单；无缓存返回空数组而非 404 |
| test_artifacts_file_api | 文本文件返回内容；越界 path → 404 + 401001 |
| test_artifacts_download_api | zip 存在时 FileResponse；未打包 → 409 提示；Agent 离线 → 409 + 202002 |
| test_agent_file_error_handling | file_error 事件清除 pending request 并可查询失败原因 |

### Agent（opc-agent-programming tests/）

| 用例 | 断言 |
|------|------|
| test_workspace_explicit_output_dir | payload.output_dir → prepare 使用该目录；缺省走默认推导 |
| test_file_scanner_excludes | node_modules/.git/.tilacode/__pycache__/.venv 不出现在清单 |
| test_file_scanner_limit | >5000 条 truncated=true |
| test_file_fetch_path_guard | 工作目录外路径 → file_error（越界）；`..` 片段拒绝 |
| test_zip_packaging_size_limit | 超 500MB 拒绝并 file_error |

### 前端（src/__tests__/）

| 用例 | 断言 |
|------|------|
| TaskNewToolbar.test | 关联任务选择器变更触发回调；路径输入透传 |
| TaskDetailArtifacts.test | 树渲染清单；离线/unsupported 提示态 |
| TaskNewChatPanel.test | sending 时发送按钮无 loading 且禁用；气泡存在 |
| TaskNewRightPanel.test | 折叠态 resizer 不渲染；悬浮按钮触发 onExpand |

## 3. E2E 测试清单（Playwright）

| 场景 | 步骤 | 断言 |
|------|------|------|
| E2E-1 关联任务创建 | 新建页选关联任务 → 填需求 → 创建 | URL ?task=；任务详情 root_task_id 继承（经 API 查询） |
| E2E-2 产物浏览下载 | 模拟 WS file_tree 事件 → 打开详情产物 Tab | 树节点可见；点击文件出现预览；下载触发 |
| E2E-3 UI 交互 | 发送消息 → 观察按钮与气泡；收起右侧面板 | 气泡出现后消失；resizer 隐藏；悬浮按钮恢复面板 |

## 4. CI 门禁

- 后端：`pytest`（新增用例）+ lint + 文件长度检查照旧。
- 前端：`npm run lint` + `npm run typecheck` + `vitest run` 照旧。
- 新增协议事件必须有 ws_messages 常量与 api-standards §4.2 同步登记（检查脚本可 grep 事件名）。

## 5. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-09 | 初始创建（ADR-028） | AI助手 |
