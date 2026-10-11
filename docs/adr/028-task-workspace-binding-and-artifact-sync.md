# ADR-028: 任务工作目录绑定与产物资源管理器

## 状态

Accepted（D2 产物回传时机已被 [ADR-059](./059-scheduling-resilience-and-artifact-sync.md) 修订为执行中增量同步，见该 ADR D4）

## 日期

2026-09-09

## 问题背景

新建任务页（Spec Chat）当前存在三个核心缺陷：

1. **任务无法锁定**：工作目录只在 Agent 节点本地决定（`{workspace根}/{base_task_id}`，见 ADR-012），hub 数据库无任何目录字段。用户对同一件事重复新建对话会产生多个任务、多个互不相干的产物目录；从「新建对话」入口也无法关联已有任务继续迭代（迭代链仅能从任务队列「继续修改」进入）。
2. **产物无法交付**：Agent → hub 回传仅有 `task_completed` 中的 `plan_content` / `change_summary` 两个文本字段，产物（源码、文档）全部留在节点本地，中枢无任何查看/下载手段。原计划的对象存储方案（ADR-005）因桌面一体化部署（ADR-021）与部署复杂度被否决。
3. **UI 体验问题**：发送按钮在等待 AI 回复期间持续转圈（易误解为未发送）；右侧面板收起后仍占 48px 布局空间且 resizer 未隐藏。

## 决策

三个子问题分别采用以下方案（2026-09-09 与用户确认）：

### D1 任务锁定：目录绑定 + 关联已有任务

- `tasks` 表新增 `output_dir` 字段（nullable，Agent 节点本地路径语义），hub 只做记录与派发下发，不校验路径在本机存在性。
- 任务创建请求（POST /api/v1/tasks）新增可选 `output_dir` 与 `related_task_id`：
  - `related_task_id` 指向已有任务 → 新任务沿用其 `root_task_id`（进入既有迭代链，复用根任务工作目录与会话，ADR-012 语义）；
  - `output_dir` 显式指定 → 派发 payload 携带 `output_dir`，Agent 侧以该目录为本次任务工作目录（替代默认 `{workspace根}/{base_task_id}` 推导）；
  - 两者均缺省 → 保持现状。
- Agent 侧 `workspace.prepare` 接受显式 `output_dir`，并在执行结果/会话登记簿中回写实际使用的目录。
- 派发 payload（hub.task_dispatch）新增可选 `output_dir` 字段。

### D2 产物回传：WS 实时清单 + 按需拉取

节点与中枢间只有 WS 长连接可反向通信（Agent 主动外连），文件内容分块传输。协议扩展（命名遵循 api-standards §4.2 `{模块}.{动作}`）：

| 方向 | 事件 | 说明 |
|------|------|------|
| Agent → hub | `agent.file_tree` | 文件清单快照（相对路径 + size + mtime，排除 node_modules/.git/.tilacode/__pycache__ 等），任务 started 后周期上报（≥30s 节流）与完成时终态上报 |
| hub → Agent | `agent.file_fetch` | 按需请求：单文件内容或整目录打包（zip），payload 含 request_id、path、mode |
| Agent → hub | `agent.file_chunk` | 分块回传：request_id、seq、total、data（base64）、done |
| hub → Agent | `agent.file_error` | 拉取失败通知（路径越界、文件不存在等） |

- hub 将回传文件落盘至本地缓存 `data/artifacts/{task_id}/`（桌面部署本地磁盘，无对象存储依赖）。
- 新增 REST：`GET /api/v1/tasks/{task_id}/artifacts/tree`（缓存清单）、`GET /api/v1/tasks/{task_id}/artifacts/file?path=`（单文件预览）、`GET /api/v1/tasks/{task_id}/artifacts/download`（zip 打包下载）。
- 前端在任务详情抽屉新增「产物」Tab：资源管理器树 + 文件预览 + 打包下载按钮（离线节点显示不可用态）。

### D3 UI 优化（全部三项）

1. 发送等待期：按钮改静态禁用态（不再转圈），消息区尾部新增「AI 正在输入」三点动画气泡。
2. 右侧面板收起后宽度归零（0px）、resizer 同步隐藏，展开按钮悬浮于聊天区右上角（overlay，不占布局）。
3. 收起后对话区自动占满全宽（已有 flex:1 基础），全宽下可正常浏览与输入。

## 备选方案

### D1 备选
- **仅自定义路径**：不提供已有任务选择器，历史任务按目录匹配归组。实现简单但关联关系弱、无迭代链复用。→ 否决。
- **纯任务关联无路径**：仅接入 parent 迭代链，不支持自定义路径。改动最小但不满足显式指定产物位置诉求。→ 否决。

### D2 备选
- **完成时打包推送**：仅完成时 zip 回传。实现简单但执行中不可见，实时性差。→ 否决。
- **文件变更事件推送（agent.file_changed）**：准实时 IDE 体验，但协议与流量复杂度最高，MVP 不做。→ 留作后续演进。
- 对象存储（MinIO/COS）：桌面部署无法引入额外服务，节点→对象存储上传还需节点侧凭证管理。→ 否决（与 ADR-021 冲突）。

### D3 备选
- 按钮文字态「发送中…」：仍占视觉焦点。→ 否决。
- 保留 48px 折叠条但可完全隐藏：两态并存增加状态复杂度。→ 否决。

## 影响

- 数据库：`tasks` 新增 `output_dir` 列（Alembic 迁移）。
- 协议：api-standards.md §4.2 新增 4 个文件传输事件；hub 与 Agent 模板版本需同步发布（资源热更新机制复用 ADR-022/025）。
- 安全：Agent 侧 `file_fetch` 必须做路径越界校验（仅允许工作目录内）、单文件/打包大小上限（MVP：单文件 ≤ 10MB，打包 ≤ 500MB）。
- 兼容：旧版 Agent 不识别 `output_dir` 与 `agent.file_fetch`，hub 侧对未支持节点降级（产物 Tab 显示离线提示），不阻断任务派发。

## 关联文档

- ADR-012（tilacode 执行层集成：迭代链/工作目录语义）
- ADR-005（文件存储选型，被本决策部分取代）
- ADR-021（Windows 桌面一体化部署：本地磁盘缓存依据）
- 010-task-creation-spec-chat-design.md（Spec Chat 现有交互）

## 实际实施情况

2026-09-09 实施完成（详见 [Changes](../spec/028-task-workspace-and-artifacts-changes.md)），补充说明：

- D1 任务锁定按用户后续澄清简化为「工作目录选择器」语义：绑定栏为单一路径
  AutoComplete（历史目录下拉 + 自由输入），迭代链复用走 `parent_task_id` 既有机制，
  未实现独立关联任务 Select；桌面版支持系统原生文件夹对话框（浏览器隐藏）。
- D2 产物回传按决策实现 4 个 WS 事件与 3 个 REST 接口，hub 落盘
  `data/artifacts/{task_id}/`；前端任务详情抽屉新增「产物」Tab。
- D3 三项 UI 修复均已完成（发送按钮静态禁用 + 三点动画气泡、收起归零 + 悬浮按钮、全宽浏览）。
- 测试遗留修复（2026-09-10）：agent 侧 15 个桥接测试未同步 `BridgeCallbacks`
  3 个产物回调参数导致失败，已同步修复并使 `_no_sleep` 让出事件循环避免周期
  清单上报任务空转。

## 修订记录

- **2026-09-16（ADR-036）**：`output_dir` 缺省行为由"保持现状（Agent 端 `{workspace}/{task_id}` 推导）"修订为：新链根任务由中枢自动生成 `中文标题-任务ID前8位` 可读目录名并随派发下发；链上子任务继承链根 `output_dir`。用户显式指定仍最高优先。详见 [ADR-036](./036-session-task-chain-and-spec-command.md)。
- **2026-09-28（ADR-059 D4）**：本 ADR「D2 产物回传」的**内容回传时机**被修订——原为「清单准实时（≥30s 节流）+ 内容**按需拉取** + **完成时**全量 zip」，导致**执行途中节点离线即丢失全部未拉取产物**（全量 zip 不会生成，跨节点改派无现场可用）。修订后新增**执行中增量同步**：中枢每 30s 按 `agent.file_tree` 的 `size`/`mtime` 计算差集，用既有 `hub.file_fetch`（`mode=file`）逐个拉取变更文件落 `{artifact_cache_dir}/{task_id}/`，离线丢失窗口收敛到 ≤30s；完成时仍执行全量 zip 作为链上现场基准。
  - **不改协议**：复用既有 `agent.file_tree` / `hub.file_fetch` / `agent.file_chunk` / `agent.file_error`，**不新增事件**。
  - **备选 D2「文件变更事件推送（`agent.file_changed`）」仍不做**，状态由「留作后续演进」细化为**登记为 ADR-059 的后续演进项 S3**（写即推送，协议与流量复杂度最高）。
