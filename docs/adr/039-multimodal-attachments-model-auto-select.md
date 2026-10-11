# ADR-039: 多模态附件与执行模型自动选择（图片/文件上传 + 模型视觉能力 + 自动选模）

## 状态

Accepted

## 日期

2026-09-21

## 问题背景

二次开发场景存在三个断层（用户反馈）：
1. 无法在中枢上传文件/压缩包给任务（Agent 在节点上拿不到参考资料）。
2. 遇到 bug 无法上传截图——图片无法进入对话与执行链路。
3. 模型能力无元数据：不知道某模型是否支持图片输入；执行模型由用户指定，
   指定了不支持视觉的模型处理带图任务时无任何提示。

技术前提（已探查）：tilacode（opencode）SDK 原生支持多模态——消息 Part
含 ImagePart/FilePart、prompt 可带 attachments、ProviderInfo 声明
`input: ["text","image",...]` 能力；Agent 端 `send_prompt` 目前仅发纯文本
（`{"prompt":{"text":...}}`），扩展即可。

## 决策（用户已确认范围 P0+P1+P2 全量、自动选模规则）

### P0 模型能力标记 + 执行模型自动模式

1. `llm_providers` 表新增 `supports_vision` 布尔列（默认 false）；模型管理
   API/前端支持勾选「支持图片输入」。
2. 任务执行模型选择（TaskExecutorModelSelect）新增「自动」选项 =
   `executor_model.source = "auto"`（新建枚举值）。
3. **自动选模规则**（中枢派发时解析，`_build_executor_model_entry` 增强）：
   - 会话/任务携带图片附件 → 从已配置且 `supports_vision=true` 的模型行中
     选取（优先「使用中」模型若支持视觉，否则任一视觉模型）；一个都没有 →
     派发失败并提示「当前没有支持图片输入的模型」。
   - 无图片附件 → 沿用「使用中」模型（与现状一致）。
   - 用户手动指定模型：不强制拦截；任务含图片且该模型无视觉能力 → 中枢在
     任务/会话处给出**提示**（告警文案 + 日志），由用户自行决定。
4. 视觉能力提示需贯穿两处消费者：中枢对话模型（Spec Chat 理解需求）与
   节点执行模型（ADR-029 凭据下发链）。

### P1 图片附件全链路

1. **上传**：新建对话页支持选择/粘贴图片；中枢存
   `data/uploads/{session_id}/{attachment_id}{ext}`，DB 记录附件元数据
   （新表 `session_attachments`：id/session_id/task_id/file_name/mime/size/
   storage_path/kind(image|file|archive)/created_at）。
2. **对话消费**：会话消息结构带附件引用；中枢侧 LLM 调用（Orchestrator/
   SpecBuilder）对支持视觉的模型把图片编码为 image part 发送；模型不支持
   视觉时前端提示（图片不发送，不报错中断）。
3. **执行消费**：任务创建/派发时携带附件清单；Agent 收到派发后经中枢
   **一次性 token 下载端点**（复用 ADR-034 handoff bundle 模式
   `GET /tasks/{task_id}/attachments?token=`）拉取文件写入工作目录
   `attachments/`；`send_prompt` 扩展支持 image parts（tilacode 原生
   attachments），仅当执行模型支持视觉时随消息带图，否则仅落盘并在
   prompt 文本中说明附件位置。
4. WS 协议：派发 payload 新增 `attachments: [{id, file_name, mime, size,
   token}]`；无附件时字段缺省（兼容旧 Agent）。

### P2 文件/压缩包附件

1. 上传控件扩展普通文件与 zip；kind=archive 的附件 Agent 端解压到工作
   目录（zip 安全校验：防 zip slip、单文件大小与总量上限）。
2. 普通文件直接写入 `attachments/`，prompt 文本中列出清单供 AI 引用。

## 备选方案

| 方案 | 结论 |
|------|------|
| 图片 base64 直接塞 WS 派发 payload | 否决。大图撑爆 WS 帧，复用一次性 token HTTP 下载更稳（ADR-034 先例） |
| 视觉模型不可用时静默剥离图片继续执行 | 否决（用户确认提示优先）。静默丢图会误导执行结果 |
| 自动模式在会话期动态换模型 | 否决。执行模型在派发时一次性解析，运行中不切换 |
| 服务商名映射 tilacode 内置集成清单决定注入通道 | 已被 ADR-030 修复取代（配置文件注入，与能力标记无关） |

## 影响

- **数据库**：`llm_providers.supports_vision` 列 + `session_attachments` 表（1 个迁移）。
- **中枢**：模型管理 API/Schema、派发 payload、附件上传/下载端点、自动选模解析、对话 LLM 图片 part。
- **Agent**：附件拉取写入工作目录、`send_prompt` 多模态扩展、zip 安全解压。
- **前端**：模型管理勾选、执行模型选择器「自动」选项与能力提示、对话附件上传控件、附件引用展示。
- **api-standards**：登记附件端点与 payload 字段。
- tilacode 内置多模态能力零改动（SDK 已支持）。

## 关联文档

- [039-multimodal-attachments-spec.md](../spec/039-multimodal-attachments-spec.md)
- [039-multimodal-attachments-harness.md](../spec/039-multimodal-attachments-harness.md)
- [039-multimodal-attachments-changes.md](../spec/039-multimodal-attachments-changes.md)
- ADR-029/030（执行模型与凭据下发、多模型管理）、ADR-034（一次性 token 下载先例）、ADR-033（技能对话）
