# 多模态附件与自动选模 变更清单（Changes）

> 关联：[ADR-039](../adr/039-multimodal-attachments-model-auto-select.md)。边开发边登记。

## P0 能力标记 + 自动模式

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 1 | alembic/versions/20260921_model_vision.py | 增 | llm_providers.supports_vision 列 | 视觉标记 |
| 2 | app/models/llm_provider.py | 修 | supports_vision 字段 | 模型 |
| 3 | app/schemas/llm_provider.py | 修 | 创建/响应 Schema 带 supports_vision | API |
| 4 | app/services/llm_provider_service.py | 修 | 创建/更新透传 | 服务 |
| 5 | app/schemas/task.py + app/services/scheduler.py | 修 | source="auto" + _build_executor_model_entry 自动选模（UT-3~6） | 自动选模 |
| 6 | src/components/LLMModelsPanel.tsx + 添加模型 Modal | 修 | 视觉勾选 | 前端 |
| 7 | src/components/TaskExecutorModelSelect.tsx 等 | 修 | 「自动」选项 | 前端 |
| 8 | tests/* | 增 | UT-2~6 对应用例 | 回归 |

### P0 实施记录（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 9 | alembic/versions/20260921_model_vision.py | 增 | llm_providers.supports_vision（bool, default false） | 迁移 |
| 10 | app/models/llm_provider.py | 修 | supports_vision 列 | 模型 |
| 11 | app/schemas/llm_provider.py | 修 | LLMModelCreate/LLMModelResponse 加 supports_vision | API |
| 12 | app/services/llm_provider_service.py | 修 | add_model 透传 supports_vision | 服务 |
| 13 | app/schemas/task.py | 修 | ExecutorModelSelection.source 加 "auto" | 自动模式协议 |
| 14 | app/services/scheduler.py | 修 | _build_executor_model_entry：auto → hub（P0 无附件体系恒无图）；P1 在此接入带图选模分支；entry.source 归一 | 自动选模 |
| 15 | src/types/llmProvider.ts、llmProvider 类型 | 修 | supports_vision | 类型 |
| 16 | src/components/LLMModelsPanel.tsx | 修 | 添加模型 Modal「支持图片输入」Checkbox；列表「支持图片」Tag | 前端 |
| 17 | src/types/task.ts + src/components/TaskExecutorModelSelect.tsx | 修 | source 加 'auto'；选择器「智能选择→自动（由中枢选择）」选项；自定义模型条目展示「· 支持图片」 | 前端 |
| 18 | tests/services/test_task_executor_model.py | 增 | auto → hub 归一用例 | 回归 |

验证：hub 26 passed（executor_model 12 + llm_provider 14）；前端 tsc/eslint 通过。

## P1 图片全链路

### Hub 端（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 19 | app/core/config.py | 修 | attachment_storage_dir / attachment_token_ttl_seconds | 附件存储配置 |
| 20 | app/models/session_attachment.py | 增 | SessionAttachment（session_id/task_id/file_name/mime/size/kind/storage_path） | 附件模型 |
| 21 | alembic/versions/20260921_session_attachments.py | 增 | session_attachments 表（down_revision=20260921_model_vision） | 迁移 |
| 22 | app/models/\_\_init\_\_.py | 修 | 导出 SessionAttachment | 模型注册 |
| 23 | app/schemas/attachment.py | 增 | AttachmentResponse / TaskAttachmentInfo | 附件 Schema |
| 24 | app/services/attachment_service.py | 增 | save_upload（白名单 10MB/总量 50MB 校验、分块落盘）、attach_to_task、issue_task_payload（一次性 token）、resolve_download（即焚） | 附件服务 |
| 25 | app/core/errors.py | 修 | ATTACHMENT_INVALID=205001 / ATTACHMENT_NOT_FOUND=205002 | 错误码 |
| 26 | app/api/v1/endpoints/attachments.py | 增 | POST/GET /sessions/{sid}/attachments、GET /sessions/{sid}/attachments/{aid}/content（预览）、GET /tasks/{tid}/attachments/{aid}?token=（Agent 一次性下载） | 附件端点 |
| 27 | app/api/v1/api.py | 修 | 注册 attachments.router / download_router | 路由 |
| 28 | app/services/llm_provider_service.py | 修 | LLMProviderConfig.supports_vision；get_active_provider/update_provider 带出视觉标记 | 中枢对话模型视觉判定 |
| 29 | app/agents/orchestrator.py | 增/修 | \_load_attachment_image_parts / \_inject_attachment_images（末轮 user 消息转 OpenAI image_url 多模态；历史轮占位；无视觉 warning 事件）；chat_stream 接入 | 对话图片注入 |
| 30 | app/schemas/task_building.py | 修 | ChatMessage.attachments 字段 | 对话请求携带附件 |
| 31 | app/services/scheduler.py | 修 | \_pick_vision_model_row；\_build_executor_model_entry 加 has_attachments（auto+带图→视觉行凭据 source=custom，无候选降级+告警）；hub/custom 分支附 supports_vision；\_assign_plan 签发附件 token 进 payload | 派发附件 + 自动选模带图判定 |
| 32 | app/services/task_service.py | 修 | create_task 成功后 attach_to_task 回填附件 task_id | 任务附件关联 |

### Agent 端（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 33 | app/models/schemas.py | 修 | TaskAttachmentInfo；TaskDispatchPayload.attachments + attachment_blobs（运行时字段） | 派发协议 |
| 34 | app/client/hub_client.py | 增 | \_download_task_attachments（token 下载、重试 1 次失败跳过）；\_handle_task_dispatch 接入 | 附件拉取 |
| 35 | app/executor/task_bridge.py | 增/修 | \_persist_attachments（落盘 .hub-attachments/）、\_build_image_parts（supports_vision 才生成 FileParts data URL）、\_build_prompt 附件路径说明、\_run_session 传 image_parts | 附件落盘与带图 prompt |
| 36 | app/executor/tilacode_client.py | 修 | send_prompt attachments 参数（prompt.attachments = FileParts） | tilacode 多模态 |
| 37 | app/executor/file_scanner.py | 修 | EXCLUDED_NAMES 加 .hub-attachments | 附件不入产物清单 |

### 前端（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 38 | src/types/taskBuilding.ts | 修 | ChatMessageAttachment；ChatMessage.attachments；事件类型加 warning | 类型 |
| 39 | src/api/attachments.ts | 增 | uploadSessionAttachment（multipart）/ sessionAttachmentPreviewUrl | 附件 API |
| 40 | src/api/taskBuilding.ts | 修 | 流式 messages 支持 attachments | API |
| 41 | src/hooks/useTaskBuildingChat.ts | 修 | sendMessage 加 attachments 参数（剥离 preview_url）；case 'warning' → antdMessage.warning | 对话附件 + 提示 |
| 42 | src/components/TaskNewChatPanel.tsx | 修 | Upload 按钮 + TextArea onPaste 粘贴图片 + 待发附件预览行 + 消息气泡附件缩略图（Image.PreviewGroup） | 附件 UI |
| 43 | src/pages/TaskNew.tsx | 修 | pendingAttachments 状态 / handleFilesPicked（ensureSession→上传）/ 发送带附件 + 清空；canSend 放宽 | 页面接线 |
| 44 | src/pages/TaskNew.css | 修 | 待发附件/气泡附件/上传按钮样式 | 样式 |

### P1 测试（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 45 | tests/services/test_attachment_service.py | 增 | 白名单/超限/落盘/回填/一次性 token 即焚/错配拒绝（8 例） | 服务回归 |
| 46 | tests/services/test_task_executor_model.py | 修/增 | auto+带图→视觉行；无候选降级；custom 行带 supports_vision（4 例新增/改写） | 自动选模回归 |

验证：附件+执行模型 23 passed；Agent 端 31 passed（task_bridge + hub_client）；前端 tsc 通过。

## P2 文件/压缩包

### Hub 端（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 47 | app/services/attachment_service.py | 修 | 白名单加 application/zip（kind=archive）；MAX_SIZE_BY_KIND 分级（图 10MB/包 200MB）；图片配额 50MB 独立计量（zip 不占配额）；attachment_uploaded 日志补 kind | zip 上传 |
| 48 | app/api/v1/endpoints/attachments.py | 修 | 上传端点描述文案更新；预览端点补 attachment_preview_started 日志 | API |
| 49 | docs/standards/api-standards.md | 修 | 登记错误码 205001/205002、附件域端点、payload.attachments、warning SSE 事件 | 规范登记（ADR 影响项） |
| 50 | app/agents/orchestrator.py | 修 | 补 orchestrator_attachment_messages_detected / orchestrator_attachment_images_injected 日志 | 日志补齐 |
| 51 | tests/services/test_attachment_service.py | 增/修 | zip 上传成功（不占图片配额）、zip 超 200MB 拒绝、图片总量口径修正（10 例） | 回归 |

### Agent 端（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 52 | app/executor/task_bridge.py | 增/修 | \_extract_attachment_archive（zip slip 防护：绝对路径/反斜杠/.. 成员拒绝、单成员 ≤200MB、条目 ≤5000 防炸弹、失败清理半成品）；\_persist_attachments 按 mime 识别 zip 解压到 {id8}\_{stem}/；\_build_image_parts 不带图时 warning 日志；prompt 文案注明压缩包已解压 | zip 安全解压 |
| 53 | tests/test_task_bridge.py | 增 | zip 解压落盘、zip slip（..）拒绝、绝对路径成员拒绝、图片落盘回归（4 例） | 回归 |

### 前端（2026-09-21）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 54 | src/components/TaskNewChatPanel.tsx | 修 | Upload accept 加 zip；非图片附件用 FileZipOutlined 图标 + 文件名展示 | zip 上传 UI |
| 55 | src/pages/TaskNew.tsx + TaskNew.css | 修 | zip 不创建 blob 预览；待发附件文件样式 | 页面接线 |

验证：hub 附件 10 passed；Agent 全量 117 passed；前端 tsc+build 通过；API E2E zip 上传 code=0/kind=archive、txt 拒绝 205001、日志事件（attachment_uploaded 带 kind/中间件 request_completed/slow_request）落盘符合 logging-standards。

## P2 后续修复（2026-09-21 晚，用户反馈）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 56 | tests/conftest.py | 增/修 | 会话级 \_guard_dev_data 数据守卫：测试前备份 AgentNode/Task/DispatchDecision/TaskChainContext/TaskBuildingSession/LLMProvider 六表、结束后原样恢复；支持 TEST_DATABASE_URL 环境变量优先（真隔离）；scripts/create_test_database.py 建库脚本（opc 用户无 CREATEDB 暂不可用） | 修复：此前每个测试 delete(LLMProvider) 等直接清开发库，多次跑测试致用户配置的模型被清空 |
| 57 | src/components/TaskNewChatPanel.tsx | 修 | 消息气泡附件按 mime 分支：image 缩略图 / zip 文件卡片（FileZipOutlined+文件名），修复 zip 被当图片渲染成破损图 | 气泡展示 |
| 58 | src/components/TaskNewChatPanel.tsx + src/pages/TaskNew.css | 修 | 输入区重构为 task-new__input-shell：附件预览移入输入框内顶部、上传按钮改 PlusOutlined 置输入框内左下角、TextArea variant=borderless | UI 按用户反馈调整 |

验证：守卫生效（14/10 个清库测试跑完后 llm_providers 行数与 id 原样保留）；tsc+build 通过。

## 上传进度显示（2026-09-21 晚，用户反馈：大文件上传久无反馈像卡死）

| # | 文件 | 类型 | 明细 | 作用 |
|---|------|------|------|------|
| 59 | src/api/attachments.ts | 修 | uploadSessionAttachment 加 onProgress（axios onUploadProgress）/signal（AbortController 取消）；超时 60s→300s（适配 200MB zip 弱网） | 进度回调 |
| 60 | src/types/taskBuilding.ts | 增 | UploadingAttachment（key/file_name/mime/preview_url/percent） | 类型 |
| 61 | src/pages/TaskNew.tsx | 增/修 | uploadingList 状态 + AbortController ref；handleFilesPicked 上传前先插入占位条目、onProgress 实时更新、成功转正/失败移除；handleCancelUploading 中止；handleSend 上传中禁发提示；canSend 加上传中条件 | 进度状态机 |
| 62 | src/components/TaskNewChatPanel.tsx | 增/修 | props 加 uploadingList/onCancelUploading；上传中条目渲染：缩略图半透明 + 圆形进度（Progress type=circle）遮罩 + 取消按钮 | 进度 UI |
| 63 | src/pages/TaskNew.css | 增 | task-new__uploading-mask 遮罩样式 | 样式 |

验证：tsc + build 通过（期间再次出现 Edit 工具假成功未落盘，已按 Grep 逐处校验补齐）。
