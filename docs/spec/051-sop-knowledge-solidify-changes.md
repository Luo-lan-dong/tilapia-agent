# SOP 动态固化 Changes（ADR-051）

> 关联：[ADR-051](../adr/051-sop-knowledge-solidify.md) / [Spec](051-sop-knowledge-solidify-spec.md) / [Harness](051-sop-knowledge-solidify-harness.md)。边开发边登记。

## 变更清单

| 日期 | 文件 | 类型 | 函数/类级明细 | 作用 |
| --- | --- | --- | --- | --- |
| 2026-09-26 | docs/adr/051-sop-knowledge-solidify.md | 增 | 全文 | 决策：知识分层重构 + SOP 固化闭环 |
| 2026-09-26 | docs/spec/sop-knowledge-solidify-{spec,harness,changes}.md | 增 | 全文 | 三件套 |

（实施记录，2026-09-26）

| 日期 | 文件 | 类型 | 函数/类级明细 | 作用 |
| --- | --- | --- | --- | --- |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 修 | 删 _PYTHON/_NODEJS/_GO/_HTML/_PHP_SOP 与 DEPLOY_SOPS/get_deploy_sop；_COMMON_RULES 精简（判定表删除→模型自主判型）；+_PITFALLS 通用坑地图（工具族互斥/端口写法/参数名差异/绑域名两步/伪静态/证书）；+get_deploy_guide_text(project_type, scene_sop)；白名单防线全保留 | 坑地图精简（指引 ~1.2K token） |
| 2026-09-26 | app/models/sop_record.py | 增 | SopRecord（name 唯一/scene/content/source/enabled/hit_count） | SOP 表 |
| 2026-09-26 | alembic/versions/20260926_sop_records.py | 增 | 迁移 + 五类 builtin seed（html/python/nodejs/go/php 要点；down=20260926_experience） | 存储层（已应用） |
| 2026-09-26 | app/services/sop_service.py | 增 | SopService（find_by_scene 命中计数/save 同名 209002/list/get/delete） | 服务 |
| 2026-09-26 | app/skills/bt_mcp.py | 修 | BtDeployGuideSkill 读库命中（SopService.find_by_scene，异常降级）→ get_deploy_guide_text | 指引读库 |
| 2026-09-26 | app/skills/sop_save.py | 增 | SopSaveSkill（name/scene/content → learned 入库，同名失败指引） | 固化技能 |
| 2026-09-26 | app/skills/registry.py | 修 | +SopSaveSkill（22 技能） | 注册 |
| 2026-09-26 | app/schemas/sop.py + app/api/v1/endpoints/sops.py + app/core/errors.py | 增/修 | /sops CRUD（209001/209002） | 管理 API |
| 2026-09-26 | opc-web-console（types/sop.ts、api/sop.ts、components/SopPanel.tsx、pages/Memory.tsx） | 增/修 | Memory 页 Tabs（经验记忆/部署 SOP）；SopPanel 列表/搜索/编辑/启停/删除/新建（tsc+build 过） | SOP Tab |
| 2026-09-26 | tests（test_bt_deploy_prompts 重写、test_bt_mcp_skills UT-S9/S9b 适配、test_sop_save 增 7 项） | 修/增 | UT-G1 精简结构断言/UT-G2 命中与无命中/UT-S1 seed/UT-S2 命中计数/UT-S3 重名/固化技能 3 项 | 回归（50/50 过） |
| 2026-09-26 | app/agents/orchestrator.py | 修 | _describe_file_attachment：hub_public_url 为空时输出引导（配置外部地址或改用 Upload 链路）而非静默缺 URL | 修复 AI 拿不到下载地址（实测：hub_public_url 为空导致 AI 向用户索要链接） |
| 2026-09-26 | app/skills/bt_call_guard.py | 增 | maybe_run_local_command（Upload 两段式协议中枢代执行：next_action.windows/posix 命令原文仅替换 <LOCAL_FILE> 为附件库本地路径 → asyncio subprocess 执行 → file_id 轮询 ready → 服务器绝对 path 回传 hub_note）；_resolve_attachment_path（本会话优先/全局最新，文件存在性校验） | 产物直传根本解：中枢磁盘文件 → 宝塔服务器，零网络配置依赖（协议已用 scripts/probe_upload_20260926.py 实测全通） |
| 2026-09-26 | app/skills/bt_mcp.py | 修 | BtMcpCallSkill timed_call 后接 maybe_run_local_command | Upload 链路接入 |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 修 | 约束第 5 条改「产物包上服务器两条路优先 Upload」 | SOP 同步 |
| 2026-09-26 | tests/skills/test_bt_call_guard.py | 增 | UT-U1 非 Upload 透传/UT-U2 代执行+轮询断言/UT-U3 附件未命中指引 | Upload 代执行回归（37/37 过） |
| 2026-09-27 | app/skills/bt_mcp.py | 修 | BtMcpToolsSkill：目录中 Upload 条目附加「中枢自动代执行本机 curl，直接调用即可」环境说明 | 修复 AI 读了宝塔原文描述「调用方必须本机执行 curl」后放弃上传、臆造调用结果（tool_call_logs 无任何 Upload 记录取证） |
| 2026-09-27 | app/skills/bt_deploy_prompts.py | 修 | _PITFALLS 首条新增「Upload 工具描述的误导」：中枢环境描述不成立，直接调用传 file_name/size 即可 | 坑地图纠正（防模型被工具描述吓退） |
| 2026-09-27 | alembic/versions/20260927_bt_mcp_call_kind.py + app/models/tool_call_log.py | 增/修 | CheckConstraint 白名单补 bt_mcp_call（drop+recreate；down 恢复） | **修复隐藏缺陷：kind 白名单缺 bt_mcp_call 致宝塔工具审计从未落库**（record 静默吞 CheckViolation；ADR-049 验收点 5 实为失效，本次诊断取证补正） |
| 2026-09-27 | app/skills/bt_mcp.py | 修 | BtMcpCallSkill summary 构造：result.data 非空前缀「返回数据：…」（json 截断 1500） | **修复 AI 看不到工具实际输出**：宝塔把命令输出/站点信息放 data 字段，此前只回传 msg（Bash 返回 done 之谜根因） |
| 2026-09-27 | app/skills/bt_deploy_prompts.py | 修 | _PITFALLS 新增 Bash 前台 20s 转后台坑（msg=done → 取 data.task_id 用 BashStatus 查询） | 坑地图补充 |
| 2026-09-27 | app/skills/bt_mcp.py | 修 | **重接丢失的调用点**：BtMcpCallSkill timed_call 后的 maybe_run_local_command 调用与 import 在 ADR-051 大改中被顶掉（实测取证：Upload 审计记录 result 无 hub_note=代执行未触发；grep 磁盘确认调用点缺失），本次补回并 grep+pytest 双重验证 | 根因修复 Upload 代执行未生效 |
| 2026-09-27 | app/skills/bt_deploy_prompts.py | 修 | 约束第 6 条：15 次上限→**40 次预算**；明确后台任务等待语义（Bash 20s 转后台→data.task_id→BashStatus(task_id, wait=true) 阻塞等待，长任务须等完成核验再推进，不因等待中断流程） | 用户反馈：任务未完工具额度先到顶 |
| 2026-09-27 | opc-web-console（TaskNew.tsx/TaskNewRightPanel.tsx/AppLayout.tsx/TaskNew.css） | 修 | ①对话区去卡片（.task-new__chat 删 border/radius）；②工具面板收起/展开按钮移右上角顶栏原头像位置（.task-new__top-toggle，AppLayout 头像删除）；③面板分隔改一条 1px 细线（resizer 无间隔，hover 变色 + ::before 扩大命中区可拖动）；④清理 unused import | UI 迭代（用户反馈 AI 味卡片/头像位置/分隔间隔） |
