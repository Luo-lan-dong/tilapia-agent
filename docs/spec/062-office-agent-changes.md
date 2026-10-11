# 办公 Agent 节点 Changes 变更清单

- 关联决策：[ADR-062](../adr/062-office-agent-node.md) / [062-office-agent-spec.md](./062-office-agent-spec.md)
- 登记规则：**边开发边实时登记**（修/增/减 + 函数级明细），禁止开发完成后一次性补记。

## 变更清单

| # | 文件路径 | 变更类型 | 函数/类级明细 | 作用说明 | 日期 |
| --- | --- | --- | --- | --- | --- |
| 1 | `opc-central-dispatch-hub/app/agents/planning.py` | 修 | `VALID_AGENT_TYPES` 增 `office`；`SYSTEM_PROMPT_TEMPLATE` 新增第 4 条「Agent 类型判定依据」 | 任务规划可判定办公类任务为 office | 2026-09-28 |
| 2 | `opc-central-dispatch-hub/app/services/agent_node_management_service.py` | 修 | `SUPPORTED_INSTALL_SCRIPTS` 增 `("office","linux")`；`get_install_script` docstring 同步 | office/linux 安装命令可下发（脚本资源需发布 install-script-office） | 2026-09-28 |
| 3 | `opc-web-console/src/components/NodeCluster.tsx` | 修 | `AGENT_TYPE_ICON_MAP` 增 `office: FileTextOutlined` | 仪表盘节点卡片 office 类型图标 | 2026-09-28 |
| 4 | `opc-agent-office/`（工程） | 增 | `app/main.py`（重写：无 tilacode，装配 OfficeBridge）；`app/config.py`（裁剪 tilacode 字段，增 OFFICE_* 工具循环配置，agent_type 默认 office，APP_VERSION=0.1.0） | 办公节点入口与配置 | 2026-09-28 |
| 5 | `opc-agent-office/app/client/` | 增 | `hub_client.py`（复用编程节点，TaskBridge→OfficeBridge 别名，capabilities=['office']，_AGENT_VERSION=0.1.0）；`endpoint_probe.py`/`notify_server.py` 原样复用 | 中枢通信层（注册/心跳/派发/重连/端点轮换） | 2026-09-28 |
| 6 | `opc-agent-office/app/executor/office_bridge.py` | 增 | `OfficeBridge`（execute_task/interrupt/handle_file_fetch/shutdown，接口对齐编程节点 TaskBridge 契约）；`BridgeCallbacks`；`TaskRunState`（取消事件/活动时间/最近进度）；`_heartbeat_loop`（ADR-055 静默期心跳）；`_pack_workspace`（zip 打包+500MB 上限） | 任务桥接层：凭据校验→工作目录→工具循环→file_tree→completed | 2026-09-28 |
| 7 | `opc-agent-office/app/executor/tool_loop.py` | 增 | `ToolLoopRunner.run`（LLM function calling 循环：轮次上限/token 预算/收尾轮注入/取消检查）；`SYSTEM_PROMPT`（办公助手角色+工具用法+引用来源要求）；`_stage_of`（工具→ADR-055 stage 映射）；`default_registry` | 自研工具循环引擎（ADR-062 决策 2） | 2026-09-28 |
| 8 | `opc-agent-office/app/executor/llm_client.py` | 增 | `LLMClient.chat`（OpenAI 兼容 /chat/completions，消费 ADR-029 executor_model 凭据，指数退避重试，4xx 不重试，usage 汇总）；`LLMError` | LLM 调用客户端 | 2026-09-28 |
| 9 | `opc-agent-office/app/tools/` | 增 | `base.py`（OfficeTool 抽象/ToolRegistry/路径越界防护/require_str）；`docx_tool.py`（blocks→python-docx 渲染）；`pptx_tool.py`（slides→python-pptx 渲染）；`search_tool.py`（parse_ddg_html 纯函数+DDG HTML 检索，Scrapling 失败降级 httpx）；`fetch_tool.py`（Scrapling Fetcher→StealthyFetcher 升级，URL 校验拒绝本机地址）；`html_text.py`（HTML→Markdown 转换器，script/style 剔除+20K 截断） | 四件套工具（ADR-062 决策 3） | 2026-09-28 |
| 10 | `opc-agent-office/app/{models,utils}/` | 增 | `schemas.py`/`logger.py`/`log_fetcher.py`/`monitor.py`（ADR-059 双门槛自评）/`file_scanner.py` 原样复用编程节点实现 | 数据模型/日志/资源监控/产物清单 | 2026-09-28 |
| 11 | `opc-agent-office/{pyproject.toml,.env.example,systemd/opc-agent-office.service}` | 增 | 依赖声明（python-docx/python-pptx/scrapling）；配置模板；systemd 单元（ExecStart=venv python -m app.main） | 工程配套 | 2026-09-28 |
| 12 | `opc-agent-office/scripts/` | 增 | `install-agent-office-linux.sh`（幂等源码部署：manifest 解析 agent-office-linux-x64+sha256 校验+Python≥3.10 检查+venv+systemd+身份迁移）；`package_office_artifact.sh`（打 tar.gz 供资源管理页发布） | 安装与打包脚本 | 2026-09-28 |
| 13 | `opc-agent-office/tests/` | 增 | `test_tools.py`（U-2/U-3/U-4/U-5）；`test_tool_loop.py`（U-1/U-7/U-9+取消+stage 映射）；`test_office_bridge.py`（U-6/U-10/I-2/I-4+中断+分块拉取） | 节点测试 **23 passed** | 2026-09-28 |
| 14 | `opc-central-dispatch-hub/tests/agents/test_planning_office.py`、`tests/api/v1/test_install_script_office.py` | 增 | C-1（枚举含 office/规划判定/提示词判据）；C-3（office/linux 矩阵+清单缺失 404） | 中枢契约测试 **15 passed** | 2026-09-28 |
| 15 | `opc-central-dispatch-hub/app/services/task_service.py` | 修 | `_create_task_from_spec` 去除 `agent_type="code"` 硬编码 → `_resolve_spec_agent_type()`（planning 判定，失败回退 code 并告警 `spec_chat_agent_type_fallback`） | **根因修复**：新建对话（spec_chat）创建的任务曾恒为 code，文档任务被调度到编程节点 | 2026-09-28 |
| 16 | `opc-central-dispatch-hub/app/agents/orchestrator_prompts.py` | 修 | `ORCHESTRATOR_BASE_PROMPT` 新增「节点类型与职责」段（office=文档/PPT/查资料，code=编程，文档任务禁导向 code）与「办公类任务特殊流程」（跳过技术栈/工程化追问，三件套按文档语义生成） | 编排 AI 建议派发目标时按职责推荐，不再误导用户 | 2026-09-28 |
| 17 | `opc-central-dispatch-hub/tests/services/test_task_service_spec_agent_type.py` | 增 | office 判定 + planning 失败回退 code 两用例 | 回归测试 **2 passed** | 2026-09-28 |
| 18 | `opc-central-dispatch-hub/app/skills/spec_generation.py` | 修 | `execute` 区分 need_info（有澄清回复且无 error → success=True 转述澄清问题）与真失败 | 修复「三件套生成失败：未知原因」误导——need_info 是正常追问流程非失败 | 2026-09-28 |
| 19 | `opc-agent-office/app/tools/base.py`、`docx_tool.py`、`pptx_tool.py` | 修 | `CJK_FONT_NAME`/`clean_llm_text`(NFKC)/`count_unrenderable_chars`(PUA 检测)；docx 样式设 w:eastAsia=微软雅黑；pptx run 级设 ea 字体；渲染后 PUA 计数>0 时反馈 LLM 覆盖重写 | **方框字修复**：docx 未设中文字体 + DeepSeek 偶发输出私用区字符（PUA 无字形必现方框） | 2026-09-28 |
| 20 | `opc-agent-office/app/executor/office_bridge.py`、`tool_loop.py` | 修 | `TaskRunState.apply_stage`/`todos_snapshot`（阶段化待办：planning/coding/testing/summarizing 基础项 + searching 动态插入，stage 变化随 progress 上报 todos）；`_STAGE_PROGRESS`（15/30/55/90 基准+轮次微增）；SYSTEM_PROMPT 增验收清单逐条自检要求；`_task_workdirs` 持久映射 + `data/task_workdirs.json` 落盘（修复任务完成后 file_fetch 报「工作目录不存在」） | 进度假死改善（待办明细可见/进度按阶段推进）、验收清单由 office 节点自检并回报、产物拉取失败修复 | 2026-09-28 |
| 21 | `opc-central-dispatch-hub/app/agents/orchestrator_prompts.py` | 修 | 新增「复合/多阶段需求处理」规则：分阶段计划经用户确认、一次只建当前阶段、严禁跳过前置阶段（未开发完成禁查宝塔/禁部署、未完成禁建宣传物料） | 修复长指令（开发→测试→部署→宣传）时编排 AI 跳阶段乱序调度 | 2026-09-28 |
| 22 | `opc-agent-office/tests/test_office_bridge.py` | 增 | 阶段化 todos 用例、NFKC/PUA 检测用例、docx PUA 反馈用例 | 节点测试 **26 passed** | 2026-09-28 |
| 23 | `opc-central-dispatch-hub/app/skills/node_inspection.py` | 修 | `FindIdleNodesSkill.execute` 无可自主候选时改用 `diagnose_eligibility` 逐台列出在线节点+准入缺口（✅可派发/❌原因+读数），不再笼统报「没有可用节点」 | 修复「内存超标时查询空闲节点只说没有可用节点」——透明化在等哪台、缺什么 | 2026-09-28 |
| 24 | `opc-central-dispatch-hub/app/skills/dispatch_skill.py` | 修 | `SmartDispatchSkill.execute`：`force=true` 且无可自主候选时从该类型全部在线节点选负载最低者走 `_dispatch_to_named_node(force=True)` 越门槛派发（此前 force 未指定节点时被忽略） | 修复「强制派送第一遍失败、必须再手动点名节点」 | 2026-09-28 |
| 25 | `opc-agent-office/app/executor/office_bridge.py` | 修 | `TaskRunState.complete_all_todos`（终态全 completed+todos_dirty）+ `_report_progress` 脏位检查，完成前上报 progress=100+全部完成 todos | 修复任务摘要「卡死在最后一步」（最后一项永远进行中） | 2026-09-28 |
| 26 | `opc-agent-office/app/tools/search_tool.py` | 修 | `parse_bing_html`（cn.bing.com 结果解析纯函数）+ 多后端检索 `_iter_backends`（OFFICE_SEARCH_BACKEND=auto 时 Bing→DDG 依序，失败原因逐后端记录并回传 LLM）+ `OFFICE_PROXY_URL` 代理支持 | **联网搜索不可用根因修复**：DuckDuckGo 在大陆网络直连超时（实测 10s ConnectTimeout），auto 模式首用大陆可达的 Bing（实测解析 5 条结果）；失败不再静默，原因进任务总结 | 2026-09-29 |
| 27 | `opc-agent-office/app/tools/fetch_tool.py`、`app/config.py`、`app/executor/tool_loop.py` | 修 | `WebFetchTool`/`WebSearchTool` 支持 proxy；`OFFICE_PROXY_URL`/`OFFICE_SEARCH_BACKEND` 配置；`_run_one_tool` 执行前报动作（带关键入参：搜索词/URL/文件名）+ 执行后报结果摘要（搜索到几条+前 3 条标题） | 联网过程与结果可视化（用户要求「显示搜索到了哪些内容」）；海外/翻墙环境可配代理 | 2026-09-29 |
| 28 | `opc-agent-office/app/executor/tool_loop.py`、`office_bridge.py` | 增 | `_extract_sources`（web_search/web_fetch 结果提取域名+标题）+ `on_sources` 回调；`TaskRunState.append_source_todos`（「参考来源：域名｜标题」completed 条目追加待办，去重+上限 12 条） | 联网参考来源展示在任务摘要待办下方（用户要求「看到访问了哪些网站」） | 2026-09-29 |
| 29 | `opc-web-console/src/hooks/useTaskBuildingChat.ts` | 修 | `thinking` 事件分支：最后一步技能 `status==='running'` 时合并到当前消息（不封口新建）——generate_spec_docs 内部 reasoning 透传（step 先到 thinking 后到）不再落在工具卡片下面 | 思考过程保持在调用工具上方（用户要求） | 2026-09-29 |
| 30 | `opc-web-console/src/components/PlanReviewPanel.tsx`、`src/hooks/useTaskPlanActions.ts` | 修 | STATUS_LABELS 补 `requeue_pending`；onPatchDetail 的 `assigned_node_id` null→undefined（Task 类型 optional string） | 修复另一会话 requeue_pending 半成品导致的 tsc 构建阻塞 | 2026-09-29 |
| 31 | `opc-agent-office/scripts/test_tools.py`、`fetch_tool.py`、`pyproject.toml`、`tool_loop.py` | 增/修 | 直测脚本（search/fetch/docx/pptx/loop 五个子命令，单工具零 token）；`_fetch` 多级抓取按 markdown 正文长度择优（<150 字符视为 SPA 占位升级隐身渲染）；依赖改 `scrapling[fetchers]`（curl_cffi 缺失曾致 Fetcher 全链路静默失效）；SYSTEM_PROMPT 增「给定 URL 优先直接抓取 + site: 语法」 | 用户要求「单独测试子代理各项能力避免中枢派发浪费 token」；实测 fetch yydsfeng.com 1.1s 成功（此前 0s 静默失败） | 2026-09-29 |
| 32 | `opc-central-dispatch-hub/app/agents/orchestrator.py`、`app/services/orchestrator_service.py` | 修 | `chat_stream` 增 `mode: str \| None`（前端 thinking/fast 显式选择优先，None 回退 `orchestrator_thinking_enabled`）；归一后的 mode 同时驱动编排主循环 `thinking=` 参数与 meta 回显；`assistant_entry` 的 `reasoning_content` 回传去除 `if thinking_enabled:` 条件（已产出的推理内容进历史与 mode 无关——带 tools 请求历史必须回传推理内容，否则 provider 400，UT-58 固化）；`_build_messages` 签名与 4 处调用点清理过渡参数 | **修复前端思考/快速模式切换不生效**：此前主循环只认全局配置；顺带修正 fast 模式下本轮已产出 reasoning 不回传的隐性 400 风险（此前被 `.env.local` ORCHESTRATOR_THINKING_ENABLED=true 掩盖） | 2026-09-29 |

## 实施验证记录（2026-09-28）

- 真实链路（本机 dev）：office 节点注册+心跳 → `POST /tasks`（requirement 触发规划 `agent_type='office'`，任务 b1495685）→ 调度自动派发 → 节点执行（web_search 真实联网 → web_fetch → write_docx）→ `completed` 100% → 产物 `开源网页抓取工具简介-Scrapling.docx`（39.5KB）落工作目录 → 交付物聚合页可见（ADR-059 增量同步生效）；
- 过程修复：中枢 dev 实例重启加载 office 规划新代码（首派误判 copywriting 系旧代码枚举无 office，重启后正确）；
- 已知边界：任务详情 `session_id` 为合成 id（`office_{task_id[:8]}`），中枢展示空——办公任务无 tilacode 会话语义，第二期对齐；Nuitka 编译打包未做（第一期源码+venv 部署，见 ADR-062 影响）。
