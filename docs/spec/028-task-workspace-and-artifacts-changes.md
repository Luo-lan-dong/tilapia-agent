# 任务工作目录绑定与产物资源管理器 Changes 变更清单

关联决策：[ADR-028](../adr/028-task-workspace-binding-and-artifact-sync.md) / [Spec](./028-task-workspace-and-artifacts-spec.md)

> 按规则：每修改/新增/删除一个文件立即登记本清单（修/增/减 + 函数级明细），禁止开发完成后一次性补记。

## 变更清单

| # | 文件 | 类型 | 函数/类级明细 | 作用说明 | 日期 |
|---|------|------|--------------|---------|------|
| 1 | docs/adr/028-task-workspace-binding-and-artifact-sync.md | 增 | — | 决策记录：目录绑定/WS 产物回传/UI 修复 | 2026-09-09 |
| 2 | docs/spec/028-task-workspace-and-artifacts-spec.md | 增 | — | 功能规格：用户故事/流程/降级/验收 | 2026-09-09 |
| 3 | docs/spec/028-task-workspace-and-artifacts-harness.md | 增 | — | 测试清单与 CI 门禁 | 2026-09-09 |
| 4 | docs/spec/028-task-workspace-and-artifacts-changes.md | 增 | — | 本清单骨架 | 2026-09-09 |
| 5 | docs/INDEX.md | 修 | 1.1/1.2/1.3 表新增行；六、更新记录 | 登记决策文档 | 2026-09-09 |
| 6 | hub/app/models/task.py | 修 | Task +output_dir 列 | 任务产物保存路径落库（ADR-028） | 2026-09-09 |
| 7 | hub/alembic/versions/20260909_task_output_dir.py | 增 | upgrade/downgrade | tasks.output_dir 迁移（已执行） | 2026-09-09 |
| 8 | hub/app/schemas/task.py | 修 | TaskCreateRequest/TaskResponse +output_dir | 创建请求与响应携带产物路径 | 2026-09-09 |
| 9 | hub/app/core/errors.py | 修 | ErrorCode +OUTPUT_DIR_INVALID(202012)/AGENT_NODE_OFFLINE(202002) | 路径非法与节点离线错误码（对齐 api-standards §3） | 2026-09-09 |
| 10 | hub/app/core/config.py | 修 | Settings +artifact_cache_dir | 产物本地缓存目录（默认 ./data/artifacts） | 2026-09-09 |
| 11 | hub/app/services/task_service.py | 修 | +_validate_output_dir；create_task/_create_task_from_spec/_create_task_from_planning | 路径校验（422+202012）与落库 | 2026-09-09 |
| 12 | hub/app/services/scheduler.py | 修 | _build_dispatch_message +output_dir | 派发 payload 下发产物路径 | 2026-09-09 |
| 13 | hub/app/schemas/ws_messages.py | 修 | +AGENT_FILE_TREE/FILE_CHUNK/FILE_ERROR、HUB_FILE_FETCH | 产物回传 WS 协议事件（ADR-028） | 2026-09-09 |
| 14 | hub/app/services/artifact_service.py | 增 | ArtifactService（save_file_tree/get_tree/fetch_file/fetch_zip/_request/handle_file_chunk/handle_file_error/store_payload/get_cached_file_path/get_cached_zip_path）+_sanitize_relative_path | 清单存储、按需拉取编排、分块组装、路径安全落盘 | 2026-09-09 |
| 15 | hub/app/services/task_event_handler.py | 修 | +_handle_file_tree/_handle_file_chunk/_handle_file_error；_EVENT_HANDLERS 注册 | WS 消息接入产物事件 | 2026-09-09 |
| 16 | hub/app/api/v1/endpoints/task_artifacts.py | 增 | get_artifacts_tree/get_artifact_file/download_artifacts +_artifact_runtime_error_code | artifacts tree/file/download 三接口 | 2026-09-09 |
| 17 | hub/app/api/v1/api.py | 修 | +task_artifacts 路由注册（prefix=/tasks/{task_id}/artifacts） | 路由聚合 | 2026-09-09 |
| 18 | agent/app/executor/workspace.py | 修 | WorkspaceManager.prepare +output_dir 参数（相对/绝对路径解析与防越界） | Agent 按显式路径执行 | 2026-09-09 |
| 19 | agent/app/executor/file_scanner.py | 增 | scan_file_tree +EXCLUDED_NAMES/MAX_ENTRIES | 工作目录清单扫描（排除依赖目录、5000 条上限） | 2026-09-09 |
| 20 | agent/app/executor/task_bridge.py | 修 | BridgeCallbacks +on_file_tree/on_file_chunk/on_file_error；execute_task 记录映射+周期上报任务；+_file_tree_reporter/_report_file_tree_once/handle_file_fetch/_pack_workspace/_sanitize_fetch_path | 产物清单周期上报与 file_fetch 分块回传 | 2026-09-09 |
| 21 | agent/app/client/hub_client.py | 修 | +4 事件常量；_handle_message file_fetch 分支；+_handle_file_fetch；+report_file_tree/send_file_chunk/report_file_error | Agent 侧协议实现 | 2026-09-09 |
| 22 | agent/app/models/schemas.py | 修 | TaskDispatchPayload +output_dir | 派发 payload 接收路径 | 2026-09-09 |
| 23 | agent/app/main.py | 修 | _build_task_bridge callbacks 装配 +3 回调 | 回调接线 | 2026-09-09 |
| 24 | web/src/types/task.ts | 修 | Task/TaskCreateRequest +output_dir | 前端类型 | 2026-09-09 |
| 25 | web/src/api/tasks.ts | 修 | +ArtifactTreeNode/ArtifactTreeResponse/ArtifactFileResponse 类型与 fetchArtifactTree/fetchArtifactFile/downloadArtifactsUrl | 产物 API 封装 | 2026-09-09 |
| 26 | web/src/components/TaskNewToolbar.tsx | 修 | +RelatedTaskOption；+绑定栏（关联任务 Select + 路径 Input） | 新建页任务锁定入口（US-1/US-2） | 2026-09-09 |
| 27 | web/src/pages/TaskNew.tsx | 修 | +relatedTaskId/relatedTaskOptions/outputDir 状态与选项加载 effect；handleCreate 传 parent_task_id/output_dir | 关联与路径接线 | 2026-09-09 |
| 28 | web/src/components/TaskNewChatPanel.tsx | 修 | 发送按钮移除 loading（静态禁用）；+AI 正在输入三点动画气泡（ai-thinking-bubble） | UI 修复：发送等待反馈（US-4） | 2026-09-09 |
| 29 | web/src/pages/TaskNew.css | 修 | .task-new__content relative；收起态 resizer display:none；.task-new__right--collapsed 归零悬浮；+.task-new__binding；+.task-new__typing-dots 动画 | UI 修复：收起归零/悬浮按钮/动画（US-5） | 2026-09-09 |
| 30 | web/src/components/TaskDetailArtifacts.tsx | 增 | TaskDetailArtifacts（buildTreeData/loadTree 轮询/handleSelectFile/handleDownload） | 任务详情「产物」资源管理器（US-3） | 2026-09-09 |
| 31 | web/src/components/TaskDetailDrawer.tsx | 修 | 详情区改 Tabs（详情/产物），产物 Tab 挂 TaskDetailArtifacts | 抽屉接入产物 Tab | 2026-09-09 |
| 32 | web/src/components/TaskNewToolbar.tsx | 修 | 绑定栏合并为单一 AutoComplete 工作目录选择器（workspaceDir/workspaceDirOptions，下拉展示目录+最近任务名+任务数，可自由输入）；移除关联任务 Select | 按用户澄清：任务锁定=选择本地工作目录（US-1/US-2 合并） | 2026-09-09 |
| 33 | web/src/pages/TaskNew.tsx | 修 | 移除 relatedTaskId；+workspaceTasks/workspaceDirOptions（output_dir 去重）/currentDirTaskIds；handleCreate 仅传 output_dir；TaskSessionDrawer 传 filterTaskIds | 目录锁定与历史会话过滤接线 | 2026-09-09 |
| 34 | web/src/components/TaskSessionDrawer.tsx | 修 | +filterTaskIds prop；visibleSessions 按目录过滤；空态文案区分；过滤提示 Tag | 历史会话只看当前工作目录相关会话 | 2026-09-09 |
| 35 | web/src/pages/TaskNew.css | 修 | .task-new__right relative + right-header absolute 浮层；docs-controls 预留 40px；移动端折叠态同步悬浮化 | 右侧面板与左侧顶对齐，收起按钮不占布局行 | 2026-09-09 |
| 36 | hub/launcher/desktop.py | 修 | +FolderPickerApi（select_folder：webview.FOLDER_DIALOG 原生对话框）；_open_window js_api 装配 | 桌面壳暴露原生文件夹选择桥（浏览器无法取得绝对路径） | 2026-09-09 |
| 37 | web/src/utils/desktopShell.ts | 增 | isDesktopShell/pickWorkspaceFolder | pywebview 桥检测与调用封装 | 2026-09-09 |
| 38 | web/src/components/TaskNewToolbar.tsx | 修 | +desktop 检测（渲染期一次）；+「浏览…」按钮（仅桌面版显示）→ pickWorkspaceFolder 回填目录 | 点击打开系统文件夹对话框选择工作目录 | 2026-09-09 |
| 39 | hub/app/schemas/task_building.py | 修 | ChatMessage.content 改 `Field(default="")` | 修复思考模式空正文回传被 422 拒绝（100001）导致的对话失败（问题1） | 2026-09-10 |
| 40 | web/src/hooks/useTaskBuildingChat.ts | 修 | SSE 结束分支 +空回复兜底提示 | 思考结束无正文时给出可见提示，消除「假卡死」观感（问题1） | 2026-09-10 |
| 41 | hub/app/schemas/task_dispatch.py | 修 | TaskDispatchRequest +node_id | 手动派发请求支持指定具体节点（问题2） | 2026-09-10 |
| 42 | hub/app/services/task_service.py | 修 | dispatch_task +node_id 参数（节点不存在/类型不匹配/离线校验，assigned+busy+立即下发）；+delete_task（running/assigned 禁删，解绑构建会话） | 指派节点派发与任务删除核心逻辑（问题2） | 2026-09-10 |
| 43 | hub/app/api/v1/endpoints/tasks.py | 修 | dispatch 透传 node_id；+DELETE /{task_id}（204） | 删除接口与派发参数接线（问题2） | 2026-09-10 |
| 44 | web/src/api/tasks.ts | 修 | dispatchTask +nodeId 参数；+deleteTask | 前端 API 封装（问题2） | 2026-09-10 |
| 45 | web/src/components/TaskDetailModals.tsx | 修 | +agentType prop；manualOpen 时 fetchAgentNodes 按类型拉节点，目标子系统 Select 改节点 Select | 手动派发可选择具体节点（问题2） | 2026-09-10 |
| 46 | web/src/components/TaskDetailDrawer.tsx | 修 | +dispatchTarget/dispatching 状态接线；初始值 'code'→''（防未选节点可确认） | 派发弹窗状态修复（问题2） | 2026-09-10 |
| 47 | web/src/components/TaskTable.tsx | 修 | +onChanged prop；+删除按钮（Popconfirm）与 handleDelete | 任务队列删除功能（问题2） | 2026-09-10 |
| 48 | hub/tests/api/v1/test_tasks.py | 修 | +_make_task_for_delete；+5 个 delete_task 测试（pending 解绑会话/completed/running 拒/assigned 拒/not found） | 删除功能测试补齐 | 2026-09-10 |
| 49 | hub/tests/api/v1/test_task_dispatch_type_and_window.py | 修 | +4 个 node_id 派发测试（指派成功+WS 下发 mock/类型不匹配/离线/节点不存在） | 指派节点测试补齐 | 2026-09-10 |
| 50 | hub/app/agents/planning.py | 修 | VALID_AGENT_TYPES 改 {"code","design","copywriting","video"}；+_AGENT_TYPE_ALIASES；_build_result 归一化 programming→code | 任务规划链路切换 code 类型，LLM 旧输出自动归一（ADR-016） | 2026-09-10 |
| 51 | hub/app/services/task_service.py | 修 | _create_task_from_spec 硬编码 agent_type programming→code | spec_chat 创建任务使用 code 类型（问题3 根因） | 2026-09-10 |
| 52 | hub/scripts/migrate_agent_type_to_code.py | 增 | main/_count（幂等 UPDATE tasks+agent_nodes） | 存量数据迁移：45 任务 + 17 节点 programming→code | 2026-09-10 |
| 53 | hub/tests/agents/test_planning.py | 修 | test_plan_success 改断言归一化为 code；+test_plan_success_with_code_type | 规划链路类型归一化测试 | 2026-09-10 |
| 54 | hub/tests/api/v1/test_tasks.py、test_task_dispatch_type_and_window.py | 修 | mock/断言 agent_type programming→code；类型不匹配用例改 design vs code | 测试与新类型体系对齐 | 2026-09-10 |
| 55 | hub/app/services/task_service.py | 修 | dispatch_task +is_redispatch：failed 任务允许重派（带 node_id 直接指派归位 manual；不带则重新入队恢复 auto+窗口） | 修复失败任务重派 409（TASK_STATE_NOT_ALLOWED） | 2026-09-10 |
| 56 | hub/app/clients/deepseek.py | 修 | +MAX_OUTPUT_TOKENS=8192；_build_payload 显式 max_tokens | 三件套长 JSON 不再被默认 4K 截断 | 2026-09-10 |
| 57 | hub/app/agents/spec_builder_parser.py | 修 | json.loads → JSONDecoder().raw_decode | 容错模型在 JSON 后追加的 Extra data | 2026-09-10 |
| 58 | hub/app/agents/spec_builder.py | 修 | chat_stream 解析失败静默重试一轮（+emit_thinking 参数，重试轮不透传思考） | 流式路径对齐非流式重试，空回复显著减少 | 2026-09-10 |
| 59 | web/src/hooks/useTaskBuildingChat.ts、web/src/components/TaskDetailDrawer.tsx | 修 | error 事件写入占位消息（+streamError 防兜底覆盖）；canManualDispatch 放行 failed | 生成失败原因可见化；失败任务显示手动派发按钮 | 2026-09-10 |
| 60 | hub/tests/api/v1/test_task_dispatch_type_and_window.py | 修 | _create_task +progress 参数；+2 个 failed 重派用例（带节点 assigned/不带节点重新入队） | 失败重派测试 | 2026-09-10 |
| 61 | hub/app/services/task_service.py | 修 | dispatch_task 删除"manual 一刀切拒绝"，改为拒绝 assigned 在途态；docstring 同步 | 修复 plan 批准后 pending+manual 死锁无法手动派发（ADR-017 修正，问题4） | 2026-09-11 |
| 62 | web/src/components/TaskDetailDrawer.tsx | 修 | canManualDispatch 改 `pending \|\| failed`（不再要求 dispatch_type=auto） | 手动派发按钮覆盖批准方案后的 pending+manual 任务（问题4） | 2026-09-11 |
| 63 | hub/tests/api/v1/test_task_dispatch_type_and_window.py | 修 | test_manual_dispatch_rejects_already_manual 改造为 test_manual_dispatch_pending_manual_allowed；+test_manual_dispatch_rejects_assigned_task、+test_plan_completed_task_dispatch_after_approval | 修正后行为测试：pending+manual 放行 / assigned 拒绝 / 批准链路放行 | 2026-09-11 |
| 64 | docs/adr/017-task-dispatch-type-and-window.md | 修 | 变更记录 +2026-09-11 修正条目 | 登记 ADR-017 规则修正（问题4） | 2026-09-11 |

（验证记录 2026-09-11：dispatch 套件 + executor_model 套件 33 passed；前端 lint/build 通过并 copy_web_dist 同步；API 实测完整链路——创建任务 → 第一次派发（入队 pending+manual）→ 第二次派发选节点（旧代码此处 409，现返回 assigned）→ 节点接收执行 running；期间排查测试挂起根因为系统重启后被强杀 pytest 残留的 idle in transaction 僵尸连接持有 tasks 表锁，pg_terminate_backend 清理 7 连接后恢复）

## 问题1-4 UI/UX 修复（2026-09-11 第二批）

| # | 文件 | 类型 | 函数/类级明细 | 作用说明 | 日期 |
|---|------|------|--------------|---------|------|
| 65 | web/src/components/TaskNewToolbar.tsx | 修 | +handleFolderIconClick；prefix 图标 onClick + cursor/title | 问题1：点工作目录图标即可打开系统选择器；浏览器环境给出明确提示（不再「点了没反应」） | 2026-09-11 |
| 66 | hub/app/agents/spec_builder.py | 修 | _yield_parsed_events 改回复与三件套交替分片（+specification_delta 事件）；_receive_stream_chunks 首个 content 增量下发 status.message=「正在编写规范文档…」；删 _stream_ai_reply | 问题2：三件套文档实时流式出现 + 接收 JSON 阶段不再像卡死 | 2026-09-11 |
| 67 | web/src/hooks/useTaskBuildingChat.ts、web/src/types/taskBuilding.ts | 修 | +specification_delta case（按字段增量拼接）；status +message → +stageText state | 问题2 前端：文档逐字实时渲染 + 阶段提示 | 2026-09-11 |
| 68 | web/src/components/TaskNewChatPanel.tsx、web/src/pages/TaskNew.css | 修 | TextArea 去 showCount；+task-new__input-footer（字数居左按钮居右）；sending 按钮变正方形「回答中」禁用态（task-new__stop-square）；气泡 +阶段文案 | 问题3：回答中正方形态 + 字数与按钮不再重叠 | 2026-09-11 |
| 69 | web/src/pages/TaskNew.tsx | 修 | +挂载 effect 自动恢复最近构建会话（updated_at 最新且有消息；无 parent/task/currentSessionId 时），关联任务恢复方案跟踪态 | 问题4：切页返回保持上次对话/任务 | 2026-09-11 |
| 70 | hub/tests/agents/test_spec_builder.py、web 各测试 | 修 | +test_chat_stream_yields_specification_delta_and_writing_hint | 流式增量与编写提示测试 | 2026-09-11 |
| 71 | docs/standards/api-standards.md | 修 | §4.3 +specification_delta 事件与 status.message 字段说明 | SSE 协议登记 | 2026-09-11 |

（验证记录 2026-09-11 二：前端 lint/tsc/vitest 31 passed/build 通过并 copy_web_dist 同步；hub spec_builder + task_event_handler 35 passed；ruff 全部通过）

（验证记录 2026-09-10 三：前端 lint/typecheck/build+copy_web_dist 通过（新 hash index-DmlkH_PJ.js）；hub pytest 21 passed（dispatch 套件）；真实失败任务 dff4c4de 重派实测 200+queued→调度器自动派发→节点执行；节点侧执行失败根因为 tilacode 使用 opencode 免费通道被拒（MissingSessionID），属 Agent 节点 LLM 凭据配置问题，另行处理）

（验证记录 2026-09-10 补充：49 passed；ruff 通过；迁移脚本实测 45 任务+17 节点归零残留；hub 重启后任务列表 agent_type=code、agent-nodes?agent_type=code 返回 23 条（含在线 code/笔记本）、调度器无 no_available_agent 告警）

（验证记录 2026-09-10：hub pytest 39 passed（含新增 9 例）；前端 lint/typecheck/build 通过并经 copy_web_dist.py 同步至 hub/web；API 实测：不存在节点派发 404+202001、DELETE 204 且任务从列表消失；桌面端需刷新页面加载新前端 hash index-BsQmGGo5.js）

（补充：修复任务/节点类型错位问题——任务创建链路仍产出 programming 导致调度与手动派发匹配不到在线 code 节点，见 #50-#54）

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-09 | 初始创建（ADR-028） | AI助手 |
| 2026-09-10 | 登记问题1/问题2 修复：空回复 100001 修复、手动派发选节点、任务删除、测试补齐与前端重新构建 | AI助手 |
| 2026-09-10 | 登记类型错位修复：任务链路 programming→code（ADR-016 收尾）+ 存量数据迁移 | AI助手 |
| 2026-09-11 | 登记问题4 修复：plan 批准后任务卡死待分配（pending+manual 双重堵死），dispatch_task 拒绝条件改为在途态 + 前端按钮条件放宽 + 测试与 ADR-017 修正登记 | AI助手 |
