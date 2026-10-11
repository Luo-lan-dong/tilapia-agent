# 多模态附件与自动选模 Spec

> ADR-039 配套。范围 P0（能力标记+自动模式）→ P1（图片全链路）→ P2（文件/压缩包）。

## 1. 用户故事

- 二次开发时上传产品压缩包/文件，任务执行时 AI 能在节点工作目录里读到。
- 遇 bug 贴截图：中枢对话模型能看图澄清需求；执行模型能看图修复。
- 添加模型时勾选「支持图片输入」；系统据此提示/自动选模。
- 选「自动」模式：有图自动派给能看图的模型；无图用使用中模型。

## 2. 核心流程

### P0 能力标记与自动选模

```
模型管理添加/编辑 → 勾选 supports_vision → 落库
任务创建：执行模型选择器 = [中枢配置（使用中） | 自定义模型行 | 自动]
派发时 _build_executor_model_entry：
    source=auto 且会话带图片附件
        → 候选 = supports_vision=true 的模型行
        → 优先「使用中」若支持视觉，否则任一视觉模型
        → 无候选 → 派发失败 reason="当前没有支持图片输入的模型"
    source=auto 无图 → 使用中模型
    手动指定模型 + 任务含图 + 无视觉 → 派发成功但附带 warning 提示
      （任务详情/会话展示「该模型不支持图片输入，图片仅落盘备查」）
```

### P1 图片全链路

```
对话页上传/粘贴图片
    ↓ POST /api/v1/sessions/{session_id}/attachments（multipart）
中枢落盘 data/uploads/{session_id}/{attachment_id}{ext} + session_attachments 行
    ↓ 消息带 attachment_ids；LLM 调用：
       使用中模型 supports_vision → 消息带 image part（base64）
       不支持 → 前端提示「当前模型不支持图片输入」（图片仅保存）
    ↓ 创建任务：附件随任务登记（attachment.task_id 回填）
派发 payload.attachments = [{id, file_name, mime, size, token}]
    ↓ Agent：GET /tasks/{task_id}/attachments?token=（一次性，复用 ADR-034 模式）
写入工作目录 attachments/{file_name}
    ↓ 执行模型支持视觉 → send_prompt 带 image parts；否则仅落盘 + prompt 说明
```

### P2 文件/压缩包

```
kind=file  → 直接写入 attachments/，prompt 列出文件清单
kind=archive(zip) → 校验（zip slip/大小上限）→ 解压到 attachments/{archive_name}/
```

## 3. 接口清单

| 端点/字段 | 类型 | 说明 |
|---|---|---|
| `POST /api/v1/sessions/{session_id}/attachments` | 新增 | multipart 上传，返回附件元数据 |
| `GET /api/v1/tasks/{task_id}/attachments?token=` | 新增 | 一次性 token 打包下载任务附件 |
| `llm_providers.supports_vision` | 迁移 | 视觉能力标记 |
| `session_attachments` 表 | 迁移 | 附件元数据 |
| `executor_model.source="auto"` | 扩展 | 自动选模 |
| 派发 `payload.attachments[]` | 扩展 | {id,file_name,mime,size,token}，无附件缺省 |

## 4. 降级与边界

- 附件单文件 ≤10MB、单任务总量 ≤50MB（图片）；压缩包 ≤200MB。
- 上传类型白名单：image（png/jpg/jpeg/webp/gif）、zip；其余拒绝。
- 旧 Agent（不支持 payload.attachments）忽略字段零影响。
- LLM 调用带图失败（如端点拒绝）→ 降级为纯文本重试一次并告警。
- Agent 拉取附件失败 → 任务继续（prompt 说明附件不可用），不阻断执行。

## 5. 验收标准

- [ ] 模型管理可勾选视觉能力；带图任务自动模式选中视觉模型（集成测试）。
- [ ] 手动指定无视觉模型处理带图任务时出现提示文案。
- [ ] 图片上传 → 对话模型收到 image part（mock 验证）；无视觉模型时前端提示。
- [ ] 派发后附件经 token 下载落盘 `attachments/`；带图任务 send_prompt 含 image part。
- [ ] 压缩包解压落地且防 zip slip。
- [ ] 中枢 pytest / Agent pytest / 前端 tsc+eslint+vitest 全过；浏览器 E2E 验证带图任务全链路。

## 6. 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-21 | 初始创建（ADR-039） | AI助手 |
