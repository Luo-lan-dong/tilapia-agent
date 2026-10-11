# 宝塔工具治理三层防线与经验记忆 Spec（ADR-050）

> 事前圈定 → 事中校验 → 事后学习：把 98 工具治理失能的已知缺陷用确定性防线拦截，未知新坑用记忆系统兜住。

## 1. 功能描述

Orchestrator 调用宝塔 MCP 工具时，三层防线协同：

1. **事前圈定**：`bt_deploy_guide` 返回 SOP 时附带本场景工具白名单与禁族警告，AI 在圈定范围内选工具；
2. **事中校验**：`bt_mcp_call` 调用前对照工具 inputSchema 硬校验参数名（未知参数/必填缺失直接拒绝并回显正确清单），场景外工具软提示；
3. **事后学习**：失败自动落经验条目 → 会话收尾 AI 提炼总结（用户确认）或用户自然语言触发回填 → 后续同签名失败命中注入历史经验。

用户可在「记忆」页面管理经验条目（查看/搜索/编辑/删除）。

## 2. 用户故事

- **US-1 参数拦截**：作为用户，当 AI 传错参数名时，系统立即返回失败并给出正确参数名清单，AI 能当场自我纠正，而不是把错误打到宝塔服务端才失败。
- **US-2 白名单引导**：作为用户，AI 部署时读取的 SOP 明确告诉我（与 AI）本场景该用哪族工具、不该用哪族，减少串族试错。
- **US-3 失败留痕**：作为用户，AI 踩坑（工具调用失败）后系统自动留下记录，我不需要手动描述现场。
- **US-4 总结确认**：作为用户，会话收尾时 AI 对本次踩坑提出「根因+解法」草案，我确认后才入库——AI 不能擅自记结论。
- **US-5 随时总结**：作为用户，我可以在对话中随时说"把这个坑总结一下"，不必等收尾。
- **US-6 经验复用**：作为用户，下次遇到同类错误时，AI 能直接引用历史经验（如"静态项目要用 HtmlProjectInfo、参数是 site_name"），不重复踩坑。
- **US-7 记忆管理**：作为用户，我在记忆页面浏览/搜索经验条目、纠正 AI 记错的结论、删除无价值条目，pending 待总结条目高亮提醒。

## 3. 核心流程

### 3.1 三层防线流（bt_mcp_call 视角）

```text
AI 发起 bt_mcp_call(server_id, tool, arguments)
   │
   ▼
[L2] 参数名 schema 硬校验（会话内缓存 schema）
   ├─ 未知参数名 ──► 拒绝（不发 MCP 请求），回显正确参数名清单 ──► AI 纠正重调
   ├─ 必填缺失 ───► 拒绝，回显必填清单 ──► AI 补参重调
   ▼
[L1] 场景白名单软提示（本会话已读 SOP 才生效）
   └─ tool ∉ 白名单 ──► 结果摘要附「注意工具族」提示（不阻断）
   ▼
调用宝塔 MCP tools/call
   ├─ 成功 ──► 返回结果
   └─ 失败（status=false / tool_error / 连接异常）
        ├─ [L3] 写 pending 经验条目（tool + error_signature + problem，同签名去重）
        ├─ [L3] 按 error_signature 查历史经验 ── 命中 ──► 摘要注入「同类问题历史经验」+ hit_count+1
        └─ 如实回传 AI（含错误翻译指引）
```

### 3.2 经验总结回填流（双通道，已定）

```text
通道 a（收尾自动提议）：
Orchestrator 自然收尾 ──► 检查本会话 pending 条目
   ├─ 无 ──► 正常收尾
   └─ 有 ──► AI 提炼「根因 + 解法」草案随收尾回复展示
        └─ 用户回复确认 ──► 回填 root_cause/solution，status=resolved
             （用户否认/忽略 ──► 保持 pending，/memory 页面可后续处理）

通道 b（自然语言触发）：
用户："把这个坑总结一下 / 记住这个经验"
   └─ AI 识别 ──► 提炼草案 ──► 用户确认 ──► 回填（同上）
```

### 3.3 降级链路

```text
experience_service 任何异常（表缺失/查询失败）
   └─ 技能调用不受阻：跳过记录与注入，仅 WARNING 日志（经验是增益不是依赖）

schema 缓存获取失败（tools/list 异常）
   └─ 跳过硬校验直接放行调用（与 ADR-049 现状一致），WARNING 日志
```

## 4. 接口设计（`/api/v1/experience`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `` | 列表：page/page_size/keyword（模糊 tool+signature+solution）/status 过滤；按 created_at 倒序 |
| GET | `/{id}` | 详情 |
| PATCH | `/{id}` | 编辑 tool/error_signature/problem/root_cause/solution/status（纠正或回填） |
| DELETE | `/{id}` | 删除 |

错误码（新增 208xxx）：`EXPERIENCE_NOT_FOUND=208001`。

技能内部结果（非 REST）：`bt_mcp_call` 失败摘要附加 `历史经验：…` 文本与命中计数。

## 5. 数据模型（`experience_records`）

见 ADR-050 §2.3。索引：`idx_experience_signature`（error_signature）、`idx_experience_status`（status）、`idx_experience_session`（session_id）。

## 6. 组件变更

| 组件 | 变更 |
| --- | --- |
| `bt_deploy_prompts.py` | 新增 `SCENE_TOOL_WHITELISTS` 四场景白名单常量；`get_deploy_sop` 返回文本附白名单段落 |
| `BtMcpCallSkill` | 参数名硬校验（schema 会话内缓存）+ 必填校验 + 场景软提示 + 失败记录 + 命中注入 |
| `experience_service`（新） | record_failure / find_matches / resolve / list / delete |
| `orchestrator.py` | 自然收尾分支挂总结钩子：检查会话 pending → 生成草案（随收尾文本）→ 确认后回填 |
| `app/api/v1/endpoints/experience.py`（新） | CRUD 端点 |
| 前端 `Memory.tsx`（新）+ AppLayout/App 路由 | 「记忆」页面与导航 |
| 技能 `experience_recall`（可选） | 对话中主动查经验 |

## 7. 验收标准

1. US-1：mock 服务端场景下，`bt_mcp_call(tool=HtmlProjectInfo, arguments={project_name:...})` 返回失败且 summary 含 `site_name`；真实场景该错误不再到达宝塔服务端。
2. US-2：四类 SOP 文本均含「本场景工具」与禁族内容；`bt_deploy_guide` 单测覆盖。
3. US-3：失败调用后库中出现 pending 条目，字段齐备；同签名重复失败不新增条目。
4. US-4/US-5：收尾草案经用户确认才 resolved；自然语言触发同样生效；未经确认条目保持 pending。
5. US-6：同签名再次失败时摘要含历史经验文本且 hit_count 递增。
6. US-7：/memory 页面列表/搜索/过滤/编辑/删除可用；pending 高亮。
7. 降级：经验表不可用时 bt_mcp_call 正常执行（仅日志告警）。
8. 迁移可升级可回滚。
