# ADR-050：宝塔工具治理三层防线与经验记忆系统（事前圈定 + 事中校验 + 事后学习）

- **状态**：Accepted（2026-09-26 用户评审通过并同日全量实施）
- **日期**：2026-09-26
- **关联**：ADR-049（宝塔 MCP 部署，本决策是其运行期缺陷的系统性治理）、ADR-047（tool_call_logs 审计，记忆条目的证据源）、ADR-033（技能框架）、ADR-040（Orchestrator 工具循环，收尾总结钩子挂载点）、[排查记录](../issues/2026-09-26-思考工具堆叠与宝塔静态站不可见-排查记录.md)（问题二定性 + 记忆系统 MVP 讨论）

## 1. 问题背景

1. **问题二实证**（2026-09-26 排查记录）：AI 部署 HTML 静态站时用错工具族（`SiteList`/`SiteGetConfig` 查静态站）、传错参数名（`project_name` vs `site_name`）、被 SOP 错误写法误导（`IP:8080`）。表面是单次建站失误，**本质是 98 工具规模化后的「工具治理」失能**：
   - **选不准**：Site 族 / HtmlProject 族 / PythonProject 族语义相近，98 条平铺目录下模型分辨不出互斥关系；
   - **参数错**：宝塔工具参数名不统一，靠记忆调用必错，查了 schema 仍可能不逐字对照；
   - **编排散**：工具间配合（建站→绑域名→SSL→轮询）只存在于 SOP 文本，SOP 没写的靠猜；
   - **SOP 有错漏**：静态预写的 SOP 本身有矛盾，错误知识反而误导。
2. **用户定性确认**：问题二背后是一整套执行逻辑问题；记忆系统（原 MVP 讨论）是解法之一，但需要评估其他手段并组合。
3. **方案讨论结论**（2026-09-26，用户已确认）：采用**三层防线**组合——事前圈定（SOP 工具白名单）、事中校验（参数名 schema 硬校验）、事后学习（经验记忆系统）。三者互补而非替代：**已知结构性缺陷用确定性防线事前/事中拦截，记忆系统专职兜住未知新坑**。

## 2. 决策（Decision）

### 2.1 第一层：事前圈定——SOP 场景工具白名单

- `bt_deploy_prompts.py` 每个 SOP 附带**场景工具清单常量**：允许工具白名单 + 禁用族警告（HTML SOP 已含工具族条款，推广到 python/nodejs/go 三类：明确各自 Project 族工具 + 通用基础设施工具边界）。
- 新增代码级常量 `SCENE_TOOL_WHITELISTS: dict[str, set[str]]`（python/nodejs/go/html 四场景），供第二层校验与 `bt_deploy_guide` 返回文本使用。
- `bt_deploy_guide` 返回 SOP 时**同时返回本场景白名单**（"本场景应使用：…；不应使用：…"），AI 无需自行归纳。

### 2.2 第二层：事中校验——`bt_mcp_call` 参数名 schema 硬校验（确定性 100%）

- `BtMcpCallSkill.execute` 调用前新增**参数名校验**：
  1. 获取该工具 `inputSchema.properties` 的参数名集合（本次会话内缓存，避免重复 tools/list）；
  2. `arguments` 中出现 schema 不存在的参数名 → **不调用 MCP，直接返回失败结果**，summary 回显 schema 正确参数名列表 + "参数名必须与 schema 逐字一致" 指引；
  3. `confirm=true` 重调路径同样校验（确认交互不改参数结构，校验无副作用）。
- 校验是纯代码判定，零 LLM 成本，可直接拦截本次 `project_name` 类错误。
- `schema_required`（必填参数）缺失时同样前置拦截并回显必填清单。
- 白名单软提示（2.1）：调用的工具 ∉ 当前场景白名单时（会话内 AI 已读 SOP 才生效），结果摘要附加"该工具不在本场景白名单，注意工具族"提示；**只提示不阻断**（SOP 未覆盖的场景不能被白名单卡死）。

### 2.3 第三层：事后学习——经验记忆系统 MVP

**闭环 4 环节**（沿用排查记录 MVP 设计，含用户已定决策）：

| 环节 | 机制 | 自动化程度 |
| --- | --- | --- |
| ① 失败自动记录 | `BtMcpCallSkill` 失败分支（status=false/tool_error/连接异常）写 pending 条目：tool + error_signature（msg 归一化截断）+ problem 快照（工具/参数摘要/耗时） | 全自动 |
| ② 总结回填 | 双通道（已定）：a) Orchestrator 自然收尾时检查本会话 pending 条目，AI 提炼「根因+解法」草案**经用户确认后**回填 resolved；b) 用户对话中自然语言触发（如"把这个坑总结一下"），由对话理解 | 半自动 |
| ③ 存储检索 | `experience_records` 表，`error_signature` 归一化签名可检索 | —— |
| ④ 命中注入 | `bt_mcp_call` 失败后按 error_signature 查历史经验，命中则把「同类问题历史经验」注入结果摘要 + hit_count+1 | 全自动 |

**数据表**（`experience_records`，遵循 database-standards）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID PK | |
| tool | VARCHAR(64) NOT NULL | 工具名（如 HtmlProjectInfo）；空串表示非工具类经验 |
| error_signature | VARCHAR(255) NOT NULL | 归一化错误签名（msg 去数字/去空白后截断），可检索 |
| problem | TEXT | 现象快照（自动） |
| root_cause | TEXT | 根因（总结回填） |
| solution | TEXT | 解法（总结回填） |
| status | VARCHAR(16) DEFAULT 'pending' | pending / resolved |
| session_id | VARCHAR(64) | 来源会话 |
| hit_count | INTEGER DEFAULT 0 | 命中次数（衡量经验价值） |
| created_at / resolved_at | 时间戳 | resolved_at 回填时写入 |

**总结时机（已定双通道）**：收尾自动提议（AI 草案 + 用户确认，防记错结论反噬记忆质量）+ 自然语言随时触发。

**范围控制**：v1 仅宝塔 MCP 技能接入（痛点最集中）；表结构与 service 天然通用，后续按 hit_count 决定推广到其他技能。

**经验条目去重**：同 session 同 tool 同 signature 已存在 pending 条目时不重复写入（hit 场景除外）。

**管理 API**（`/api/v1/experience`，新错误码 208xxx）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `` | 列表（分页 + keyword 搜索 tool/signature/solution + status 过滤） |
| GET | `/{id}` | 详情 |
| PATCH | `/{id}` | 编辑（纠正 AI 记错的结论）/ 回填 / 删除前确认用 |
| DELETE | `/{id}` | 删除 |

**前端**：左侧主侧边栏新增「记忆」导航项（与日志同级，全局态知识库），页面 `/memory`：列表（status/hit_count/时间/搜索）+ 详情（problem/root_cause/solution 三栏可编辑）+ 删除；待总结条目高亮。

**技能（可选）**：`experience_recall` 供模型对话中主动查经验；v1 优先做命中注入（被动），主动查询技能列为可选项。

## 3. 备选方案（Considered Options）

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 仅记忆系统（不做事前/事中防线） | ❌ 否决 | 已知结构性缺陷（参数名混乱、工具族互斥）事后学习成本高、不稳定；确定性防线代码级 100% 拦截 |
| 复合技能编排（高频场景代码化，AI 只传业务参数） | ❌ 否决（v1） | 每场景开发+维护成本高、灵活性下降，违背 ADR-049「AI 按 SOP 自主编排」；列后续按场景评估 |
| 98 工具 schema 全量注入 | ❌ 否决 | ADR-049 已否决（30K+ token 常驻） |
| 场景化 schema 注入（bt_deploy_guide 附带 8~15 个工具完整 schema） | ⏸ 缓议 | 参数正确性最高，但每次部署会话 +3~5K token；第二层硬校验已拦截参数名错误，先观察命中数据再决定 |
| 中枢本地工具权限管控 | ❌ 否决 | ADR-049 已定：权限管控放服务器端插件 |
| 记忆全自动总结（不经用户确认） | ❌ 否决 | AI 自动总结易记错结论，反噬记忆质量；收尾 AI 提议 + 用户确认（排查记录已定半自动） |
| 与 history_recall / query_trace 合并入口 | ❌ 否决 | 三者层级不同（原始层/对话层/结论层）互补；保留独立 |

## 4. 影响分析

- **中枢**：`experience_records` 表 + Alembic 迁移、`experience_service`（record/find_matches/resolve/list）、`bt_deploy_prompts.py` 白名单常量 + `bt_deploy_guide` 返回增强、`BtMcpCallSkill` 校验钩子（参数名硬校验 + 失败记录 + 命中注入）、orchestrator 收尾总结钩子、`/experience` CRUD 端点、错误码 208xxx、新增技能注册（若含 experience_recall）。
- **前端**：AppLayout 菜单项 + 路由 + `/memory` 页面（列表/详情/编辑/删除）。
- **Agent 节点**：零改动。
- **token 成本**：白名单随 bt_deploy_guide 按需返回（数百 token）；命中注入仅在失败时附加（数百 token）；无常驻成本。
- **风险**：收尾总结依赖用户确认意愿——pending 条目积压由 /memory 页面高亮引导；error_signature 归一化过粗会导致误命中——v1 用"去数字+截断"保守策略，误命中仅多注入一段提示文本，无副作用。

## 5. 验收要点

1. `bt_mcp_call` 传 schema 外参数名（如 HtmlProjectInfo 传 project_name）→ 不发 MCP 请求，返回失败 + 正确参数名清单；必填缺失同理。
2. 部署对话中 bt_deploy_guide 返回的 SOP 含本场景工具白名单与禁族警告；python/nodejs/go SOP 同样携带。
3. `bt_mcp_call` 失败 → experience_records 自动落 pending 条目（tool/signature/problem 齐备）；同签名去重。
4. 再次触发同签名失败 → 结果摘要含「同类问题历史经验」+ hit_count 递增。
5. 会话收尾时 AI 对本会话 pending 条目提出总结草案，用户确认后条目变 resolved（root_cause/solution 回填）；用户说"把这个坑总结一下"同样可触发。
6. /memory 页面：列表/搜索/状态过滤/编辑/删除可用；pending 条目高亮。
7. 迁移可升级可回滚；经验表为空或 service 异常时技能调用不受阻（降级为无记忆注入）。

## 6. 实际实施情况

1. **开发范围**：三层防线全量落地——L1（`SCENE_TOOL_WHITELISTS` 四场景白名单 + `whitelist_section` 随 SOP 返回 + `PHP_SITE_MGMT_TOOLS` 软提示范围）；L2（`bt_call_guard.py`：`fetch_tool_schemas`/`validate_arguments`/`scene_soft_hint`，BtMcpCallSkill 调用前硬校验）；L3（`experience_records` 表 + 迁移 20260926_experience + `experience_service` + `record_and_match` 失败闭环 + orchestrator 收尾提示 `_experience_pending_hint` + `experience_recall`/`experience_resolve` 两技能 + `/experience` CRUD + 前端 /memory 页面）。
2. **与决策的偏差增补**：
   - `experience_resolve` 回填技能为实施增补（ADR §2.3 表格未列出）——收尾提示 → AI 查明细（recall）→ 用户确认 → 回填（resolve）的闭环必须有 function calling 执行通道，属决策机制的自然实现；
   - `experience_recall` 从"可选"转为实施（同上，双通道链路的中间环节）；
   - 软提示范围增补 `PHP_SITE_MGMT_TOOLS`：SiteList 等 PHP 网站管理工具不在任何场景白名单内，若仅按"出白名单"判断则反而不会提示——修正为"族工具（各场景并集 ∪ PHP 网站管理族）且不在当前场景白名单"才提示。
3. **验证结果**：
   - 新增测试 34 项全过（UT-W×6、UT-V×10、UT-E×9、UT-EP×4、experience 技能×7 含收尾提示函数）；bt_mcp 存量回归适配后技能+API 全量 98/98 通过；
   - Alembic 迁移应用到开发库；`/experience` 返回空列表 200；`/skills` 含 8 个 bt_*/experience_* 技能全部注册；前端 tsc+eslint+vite build 通过（SPA 产物含 /memory）；后端已重启生效。
4. **待 E2E 验证**（真实宝塔服务器）：UT-V1 场景真实拦截、收尾总结用户确认闭环、同签名命中注入真实数据。
