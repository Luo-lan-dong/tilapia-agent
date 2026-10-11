# 多模态附件与自动选模 Harness

## 测试清单

| 编号 | 层级 | 场景 | 断言 |
|---|---|---|---|
| UT-1 | 中枢迁移 | supports_vision 列 + session_attachments 表 | upgrade head 成功 |
| UT-2 | 中枢 API | 模型管理创建/编辑 supports_vision | 落库与响应含标记 |
| UT-3 | 中枢服务 | source=auto 且会话带图 → 选中视觉模型行（优先使用中） | executor_model.entry 指向视觉模型 |
| UT-4 | 中枢服务 | source=auto 带图但无视觉模型 | 派发失败 reason 含「支持图片输入」 |
| UT-5 | 中枢服务 | source=auto 无图 → 使用中模型 | 与现状一致 |
| UT-6 | 中枢服务 | 手动指定无视觉模型 + 带图 | 派发成功且产生 warning 提示 |
| UT-7 | 中枢 API | 附件上传（multipart）| 落盘 + session_attachments 行；超限/类型拒绝 |
| UT-8 | 中枢 API | 附件 token 下载 | 一次性有效、zip 打包含全部附件 |
| UT-9 | 中枢对话 | 会话消息带图 + 视觉模型 | LLM 请求含 image part；无视觉模型 → 提示且不带图 |
| UT-10 | Agent | 附件拉取写入 attachments/ + zip 解压防 slip | 文件落地、越界路径拒绝 |
| UT-11 | Agent | send_prompt 带图（执行模型视觉） | body 含 image parts；无视觉 → 纯文本+落盘说明 |
| UT-12 | 前端 | 模型管理勾选视觉；选择器「自动」选项；上传控件；提示文案 | 组件测试 |
| E2E | 浏览器 | 带截图任务全链路：上传→对话看图→创建（自动）→执行→产物 | 全链路通过 |

## CI 门禁

中枢 pytest、Agent pytest、前端 tsc/eslint/vitest、ruff 全过。
