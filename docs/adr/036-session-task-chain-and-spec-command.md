# ADR-036: 会话内任务链自动挂接、工作目录可读命名与规范文档按需生成

## 状态

Accepted

## 日期

2026-09-16

## 问题背景

新建对话页（ADR-033 技能链路）中用户对同一需求反复提出修改时，`create_task` 技能创建的新任务 `parent_task_id` 恒为 None（技能只透传前端 URL `?parent=` 入口的值），导致：

1. **重复从头执行**：每个新任务都是独立链根（实锤：tasks 表 5 个同源任务 parent/root 全为 None 但 session_id 相同），Agent 端按 `project_key = base_task_id or task_id`（ADR-012）新开空工作目录 + 新 tilacode 会话，从零重写全部文件，浪费大量 token；ADR-034 handoff 也因链上无历史产物而不触发。
2. **目录不可辨认**：Agent 端工作目录为 `{workspace}/{UUID}`（ADR-012 §3 默认），用户在节点文件管理器中无法分辨哪个目录对应哪个任务。
3. **文档重复生成**：每次修改（哪怕只改背景色）都全量重新生成三件套规范文档，浪费 token。

用户确认（2026-09-16）：同会话内修改需求一律自动挂链（全新需求用户会新建会话）；目录用「中文标题-短ID」命名；三件套按需生成 + `/spec` 强制命令（带命令提示面板）。

## 决策

1. **会话内自动挂链（服务层）**：`TaskService.create_task` 在 `parent_task_id` 为空且 `session_id` 非空时，自动查询 `tasks.session_id == session_id` 的最近一条任务（`created_at desc limit 1`，含链尾子任务）作为父任务；`root_task_id` 派生复用 `_resolve_chain_fields` 现有逻辑。显式 `parent_task_id`（`?parent=` 入口）优先级更高。自动挂链成功且 `change_brief` 为空时，以 `requirement` 截断填充。
2. **工作目录可读命名（服务层）**：创建任务时按优先级解析 `output_dir`：
   - 用户显式指定 → 原样使用（最高优先级）；
   - 有父任务（挂链/迭代）→ 继承链根任务的 `output_dir`（根为空则留空，Agent 端回退 `{workspace}/{root_id}`）；
   - 新链根 → 自动生成 `{净化中文标题}-{任务ID前8位}`（标题去除文件系统非法字符、空白转 `-`、截断 20 字）。
   - Task 主键改为 flush 前显式生成，保证目录名与库中 ID 一致。Agent 端零改动（`output_dir` / `base_task_id` / handoff 通道已支持）。
3. **规范文档按需生成（提示词策略）**：`_spec_is_substantive` 门禁保留（全新会话任务必须先生成三件套）；提示词新增准则——三件套已有实质内容且为简单增量修改时不调用 `generate_spec_docs`，直接 `create_task` 复用现有文档；全新需求或复杂改造才先生成。
4. **/spec 强制命令**：Orchestrator `chat_stream` 检测最后一条用户消息 strip 后精确等于 `/spec` 时，不走 LLM 决策轮，直接执行 `generate_spec_docs` 技能（合成 tool_call 复用 `_run_tool_call` 事件协议），技能被禁用也强制执行；完成后以 `SkillResult.summary` 作为回复分片下发。前端输入框检测 `/` 开头弹出命令提示面板（当前仅 `/spec`），点击回填输入框。

## 备选方案

| 方案 | 结论 |
|------|------|
| 挂链：AI 自主判断（iterate 参数） | 否决。依赖 LLM 判断有遗漏风险，用户确认"全新任务会新建会话"，全自动更可靠 |
| 挂链：每次反问用户 | 否决。多一轮对话，体验差 |
| 命名：日期-短ID / 英文 slug-短ID | 否决。日期格式仍看不出项目；英文 slug 需翻译实现复杂且可能不准 |
| 文档：允许全新任务无文档创建 | 否决。用户要求全新任务必须生成，仅会话内迭代免生成 |
| /spec：仅让 LLM 处理该消息 | 否决。存在不调用技能的风险，确定性执行更可靠 |

## 影响

### 中枢

- `task_service.py`：新增 `_auto_link_parent` / `_resolve_output_dir` / `_sanitize_title_for_dir`；两条创建路径显式生成 Task id。
- `task_creation.py`：`TaskCreateRequest` 补 `change_brief`；技能 description 更新。
- `orchestrator.py`：`chat_stream` 前置 `/spec` 拦截 + `_run_forced_spec` 辅助。
- `orchestrator_prompts.py`：追加两条策略准则。
- 无数据库迁移、无协议变更、会话持久化零改动。

### 前端

- 新增 `TaskNewCommandPalette` 组件；`TaskNewChatPanel` 集成 `/` 触发逻辑。

### 兼容性

- 旧会话/旧任务不受影响：已存在目录保持原名；自动挂链仅在创建时刻生效。
- 「继续修改」（`?parent=`）流语义不变（子任务继承链根目录与其原有行为一致）。
- Agent 旧版本无感知（`output_dir` / `change_brief` 通道 ADR-028/012 均已支持）。

## 关联文档

- [ADR-012](./012-tilacode-execution-layer-integration.md)（任务迭代链与工作目录策略，本决策修订其 §3 默认命名）
- [ADR-028](./028-task-workspace-binding-and-artifact-sync.md)（output_dir 通道，本决策修订其"缺省保持现状"）
- [ADR-033](./033-ai-skills-and-smart-dispatch.md)（技能链路，本决策修订 create_task / generate_spec_docs 行为）
- [ADR-034](./034-task-handoff-context.md)（handoff 因挂链修复而开始正常触发）
- [session-task-chain-spec](../spec/036-session-task-chain-spec.md) / [harness](../spec/036-session-task-chain-harness.md) / [changes](../spec/036-session-task-chain-changes.md)

## 修订记录

- **2026-09-17（ADR-037）**：三件套在 Agent 工作目录的落盘路径版本化为 `docs/specs/<净化中文标题>-<任务ID前8>/`，命名规则沿用本决策的净化函数语义（非法字符集、截断 20、空串兜底 task）。详见 [ADR-037](./037-agent-spec-docs-versioned-dir.md)。
