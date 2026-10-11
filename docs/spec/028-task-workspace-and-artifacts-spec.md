# 任务工作目录绑定与产物资源管理器 Spec

关联 ADR：[ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md)

## 1. 功能描述

围绕「新建任务」页与任务详情抽屉的三个能力升级：

1. **任务锁定**：新建任务时可选择「关联已有任务」或指定「产物保存路径」，使多次对话的产物收敛到同一工作目录。
2. **产物交付**：Agent 执行期间实时上报产物文件清单，用户在任务详情抽屉中以资源管理器形式浏览、预览文件，并可打包下载到中枢所在机器。
3. **UI 优化**：修复发送等待反馈、右侧面板收起占位与调宽失效问题。

## 2. 用户故事

### US-1 关联已有任务锁定目录

> 作为用户，我在新建对话时选择了一个已有任务，希望本次对话创建的任务在原任务的工作目录中继续开发，产物不散落。

- 入口：新建任务页工具栏新增「关联任务」选择器（可搜索任务标题，仅列已完成/进行中的 code 类任务）。
- 选择后：会话创建请求携带 `related_task_id`；创建任务时 hub 沿用该任务的 `root_task_id`，派发不传 `output_dir`（Agent 复用根任务目录）。
- 页面进入迭代提示态（复用现有 `?parent=` 上下文条样式），显示关联任务名与退出关联按钮。
- 支持随时取消关联，恢复独立新任务。

### US-2 指定产物保存路径

> 作为用户，我在新建任务时指定产物保存路径（Agent 节点本地目录，如 `/home/user/projects/login-api`），希望 Agent 在该目录中执行并交付。

- 入口：新建任务页工具栏「保存路径」输入框（默认占位：留空则由 Agent 按默认规则分配）。
- 创建任务时路径随任务落库（`tasks.output_dir`），派发 payload 携带该路径；Agent 校验/创建目录后在其中执行。
- 路径为 Agent 节点本地语义，hub 不做本机校验；格式非法（空串/含 `..` 越界片段）在 hub 侧拒绝（错误码 202012）。

### US-3 浏览与下载任务产物

> 作为用户，我在任务详情抽屉打开「产物」Tab，看到该任务工作目录的实时文件树；点开文件可预览内容，点击「打包下载」将全部产物以 zip 保存到中枢所在机器。

- 执行中：文件树随 Agent 周期上报自动刷新（保留上次清单直到新清单到达）。
- 文件预览：单文件按需拉取，文本文件展示内容（≤ 10MB）；二进制显示不可预览提示。
- 打包下载：hub 请求 Agent 打包工作目录，zip 落盘后提供浏览器下载；Agent 离线或任务未开始时按钮禁用并提示。
- 排除项：`node_modules/`、`.git/`、`.tilacode/`、`__pycache__/`、`.venv/` 等不在清单中。

### US-4 发送等待反馈

> 作为用户，我点击发送后立即看到「AI 正在输入」气泡，明确消息已送达。

- 发送按钮在等待期变为静态禁用（图标不旋转）。
- 消息列表尾部出现三点跳动动画气泡，AI 首个回复块到达后消失。

### US-5 右侧面板收起交互

> 作为用户，我收起右侧面板后对话区占满全宽，右上角有悬浮展开按钮；展开后恢复拖拽调宽。

- 收起：面板宽度 0、resizer 隐藏、悬浮按钮显示。
- 展开：恢复上次宽度，resizer 恢复。

## 3. 核心流程

### 3.1 任务创建与目录绑定

```
用户在新建页选择关联任务或填写路径
        │
        ▼
POST /api/v1/tasks
  { requirement, specification, session_id,
    related_task_id?, output_dir? }
        │
        ├─ related_task_id 有值 → root_task_id = related.root_task_id
        ├─ output_dir 有值     → tasks.output_dir = output_dir
        ▼
调度器派发 hub.task_dispatch
  payload += { output_dir? }（base_task_id 语义不变）
        │
        ▼
Agent workspace.prepare
  ├─ payload.output_dir → 使用该目录（不存在则创建）
  └─ 缺省 → {workspace根}/{base_task_id}（现状）
        │
        ▼
agent.task_started（payload 含实际 working_dir）
        │
        ▼
hub 记录任务实际工作目录（日志 + task_event_handler 存 tasks.output_dir 回写）
```

### 3.2 产物实时清单与按需拉取

```
Agent 执行中（节流 ≥30s）/ 完成时
        │ scan(排除规则)
        ▼
agent.file_tree { task_id, files:[{path,size,mtime}], truncated }
        │
        ▼
hub: 更新 data/artifacts/{task_id}/.manifest.json
     （WS 消息处理 task_event_handler 扩展）
        │
        ▼
前端任务详情抽屉「产物」Tab
  GET /api/v1/tasks/{task_id}/artifacts/tree
        │
  用户点击文件 / 点击打包下载
        │
        ▼
hub → agent.file_fetch { request_id, path | mode=zip }
        │
Agent 读取/打包 → 分块 base64
        ▼
agent.file_chunk { request_id, seq, total, data, done }
        │
        ▼
hub: 组装落盘 data/artifacts/{task_id}/<path>
     或 data/artifacts/{task_id}/export.zip
        │
        ▼
GET /api/v1/tasks/{task_id}/artifacts/file?path=  → 单文件预览
GET /api/v1/tasks/{task_id}/artifacts/download    → zip 下载（FileResponse）
```

### 3.3 UI 修复

- 发送等待：`sending=true` 时按钮 `loading=false` + `disabled`，消息尾部渲染 `<AIThinkingBubble />`。
- 右侧收起：`--collapsed` 类宽度 0、`display:none` 内容；resizer `display:none`；展开按钮 absolute 悬浮于聊天区右上角。

## 4. 降级链路

| 场景 | 行为 |
|------|------|
| Agent 不支持 agent.file_fetch（旧版本） | hub 返回 `{ status: "unsupported" }`，前端产物 Tab 提示「需升级 Agent」 |
| Agent 离线时请求文件/打包 | REST 返回 409 + 202002（节点离线），前端按钮禁用态提示 |
| Agent 上报清单超上限（>5000 条） | payload `truncated=true`，hub 存部分清单并标记 |
| file_fetch 超时（30s 无分块） | hub 侧放弃等待，返回 504 + 401003 |
| 路径越界/读取失败 | Agent 回 agent.file_error，hub 返回 404 + 401001 |
| output_dir 与 Agent 工作区根冲突（越界） | Agent 拒绝并 task_failed（reason 说明），hub 201002 |

## 5. 验收标准

1. 新建对话选择关联任务后创建的任务，其 `root_task_id` 等于关联任务的根；Agent 在根任务目录执行（可通过 agent.task_started 上报的 working_dir 验证）。
2. 新建任务指定 `output_dir` 后，Agent 在该目录执行；非法路径返回 422 + 202012。
3. 任务执行中，产物 Tab 的文件树随上报自动刷新；点击文本文件可见内容；打包下载得到可用 zip 且与清单一致（排除项不存在）。
4. 发送消息后按钮静止禁用 + 「AI 正在输入」气泡出现，回复到达后消失。
5. 右侧收起后对话区占满全宽、resizer 不可见、悬浮展开按钮可恢复面板与拖拽。
6. 现有回归：任务创建/派发/迭代链/构建会话相关测试全部通过。

## 6. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-09 | 初始创建（ADR-028） | AI助手 |
