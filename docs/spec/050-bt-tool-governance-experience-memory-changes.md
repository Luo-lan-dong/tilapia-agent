# 宝塔工具治理三层防线与经验记忆 Changes（ADR-050）

> 关联决策：[ADR-050](../adr/050-bt-tool-governance-experience-memory.md) / [Spec](050-bt-tool-governance-experience-memory-spec.md) / [Harness](050-bt-tool-governance-experience-memory-harness.md)
> 边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 日期 | 文件 | 类型 | 函数/类级明细 | 作用 |
| --- | --- | --- | --- | --- |
| 2026-09-26 | docs/adr/050-bt-tool-governance-experience-memory.md | 增 | 全文 | 决策记录：三层防线（事前白名单/事中参数硬校验/事后记忆）+ 经验记忆系统 MVP |
| 2026-09-26 | docs/spec/050-bt-tool-governance-experience-memory-spec.md | 增 | 全文 | 用户故事/三层防线流程/双通道总结/降级链路/接口/验收 |
| 2026-09-26 | docs/spec/050-bt-tool-governance-experience-memory-harness.md | 增 | 全文 | 测试清单与 CI 门禁 |
| 2026-09-26 | docs/spec/050-bt-tool-governance-experience-memory-changes.md | 增 | 全文 | 本清单骨架 |
| 2026-09-26 | docs/INDEX.md | 修 | 1.1/1.2/1.3 追加 ADR-050 行；六、更新记录追加；七、问题排查记录状态更新 | 决策登记 |

（以下为实施记录，2026-09-26）

| 日期 | 文件 | 类型 | 函数/类级明细 | 作用 |
| --- | --- | --- | --- | --- |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 修 | +`from typing import Any`；+_SHARED_INFRA_TOOLS（共享基础设施工具集，PHP 网站管理族排除）；+SCENE_TOOL_WHITELISTS（python/nodejs/go/html 四场景）；+_SCENE_TITLES；+PHP_SITE_MGMT_TOOLS（网站管理专有族，软提示范围）；+detect_scene_from_messages（SOP 标题识别场景）；+whitelist_section（白名单段落生成）；get_deploy_sop 场景分支附白名单段落 | L1 事前圈定 |
| 2026-09-26 | app/skills/bt_call_guard.py | 增 | fetch_tool_schemas（tools/list 拉全量 inputSchema，失败降级 None）；validate_arguments（未知参数/必填缺失校验，confirm 豁免）；scene_soft_hint（会话场景检测 + 族工具软提示，只提示不阻断）；record_and_match（失败自动记录 + 命中注入文本，内部全兜底） | L2 事中校验 + L3 经验钩子（独立文件防 bt_mcp.py 超 600 行） |
| 2026-09-26 | app/skills/bt_mcp.py | 修 | BtMcpCallSkill.execute：调用前 +schema 硬校验块（未知参数/必填缺失前置拒绝并回显正确参数清单、工具不存在提示）；BtMcpError 分支 +record_and_match；status_flag=False 分支 +record_and_match；成功/失败 summary +scene_soft_hint | BtMcpCallSkill 接入三层防线 |
| 2026-09-26 | app/models/experience_record.py | 增 | ExperienceRecord 模型（tool/error_signature/problem/root_cause/solution/status/session_id/hit_count/resolved_at） | 经验表 |
| 2026-09-26 | app/models/__init__.py | 修 | 导出 ExperienceRecord | 模型注册 |
| 2026-09-26 | alembic/versions/20260926_experience_records.py | 增 | upgrade/downgrade（建/删 experience_records + idx_experience_signature/status/session 三索引；down=20260926_bt_mcp） | 迁移（开发库已应用） |
| 2026-09-26 | app/services/experience_service.py | 增 | ExperienceService（record_failure 同键去重/find_matches resolved 优先/record_hit/resolve 回填/get_record/list_records keyword+status+分页/list_session_pending/delete_record）+normalize_signature（去数字/压空白/截断） | 经验服务 |
| 2026-09-26 | app/schemas/experience.py | 增 | ExperienceUpdate（status 校验）/ExperienceResponse/ExperienceListResponse | API DTO |
| 2026-09-26 | app/api/v1/endpoints/experience.py | 增 | list/get/update/delete 路由（_get_or_404 抛 208001；PATCH status=resolved 自动写 resolved_at） | /experience 端点 |
| 2026-09-26 | app/api/deps.py | 修 | +get_experience_service | 依赖注入 |
| 2026-09-26 | app/api/v1/api.py | 修 | +experience.router（prefix=/experience） | 路由注册 |
| 2026-09-26 | app/core/errors.py | 修 | +EXPERIENCE_NOT_FOUND（208001） | 错误码 |
| 2026-09-26 | app/skills/experience.py | 增 | ExperienceRecallSkill（scope=session 本会话 pending/search 关键词检索）；ExperienceResolveSkill（用户确认后回填 record_id+root_cause+solution） | 双通道总结的执行侧技能 |
| 2026-09-26 | app/skills/registry.py | 修 | SKILL_REGISTRY +ExperienceRecallSkill/ExperienceResolveSkill | 技能注册 |
| 2026-09-26 | app/agents/orchestrator.py | 修 | +import ExperienceService；+_experience_pending_hint（会话 pending 条目收尾提示，异常静默）；chat_stream 自然收尾 done 前 +pending_hint chunk | L3 通道 a：收尾自动提议 |
| 2026-09-26 | opc-web-console/src/types/experience.ts | 增 | ExperienceItem/ListResponse/UpdateRequest | 前端类型 |
| 2026-09-26 | opc-web-console/src/api/experience.ts | 增 | fetchExperienceList/updateExperience/deleteExperience | 前端 API |
| 2026-09-26 | opc-web-console/src/pages/Memory.tsx | 增 | Memory 页面（搜索/状态 Segmented/Table/pending 高亮/详情 Drawer 可编辑/删除） | /memory 管理页 |
| 2026-09-26 | opc-web-console/src/App.tsx | 修 | +Memory lazy +Route path="memory" | 路由 |
| 2026-09-26 | opc-web-console/src/components/AppLayout.tsx | 修 | menuItems +memory（日志旁）/getSelectedKey/getPageTitle 补 memory 分支 | 导航接线 |
| 2026-09-26 | tests/skills/test_bt_deploy_prompts.py | 增 | UT-W1/W2/W3 + PHP 族排除 + 场景识别 | L1 测试 |
| 2026-09-26 | tests/skills/test_bt_call_guard.py | 增 | UT-V1~V8 + confirm 豁免/未读 SOP 不提示/经验异常静默 | L2/L3 测试 |
| 2026-09-26 | tests/services/test_experience_service.py | 增 | UT-E1~E6/E8 + 归一化/会话 pending | service 测试 |
| 2026-09-26 | tests/api/v1/test_experience_api.py | 增 | UT-EP1~EP3（列表/过滤/PATCH 回填/DELETE/208001） | API 测试 |
| 2026-09-26 | tests/skills/test_experience_skills.py | 增 | recall（session 空/有/search）/resolve（成功/不存在/缺参）/_experience_pending_hint（空/有） | 技能与收尾钩子测试 |
| 2026-09-26 | tests/skills/test_bt_mcp_skills.py | 修 | UT-S5/S6/S7/S8 补 patch fetch_tool_schemas→None（防线降级）与 record_and_match→""（S7/S8 经验不写库） | 存量回归适配 |
| 2026-09-26 | opc-web-console（npm run build） | 构建 | SPA 产物更新（含 /memory 页面） | 用户可见管理界面 |
| 2026-09-26 | app/api/v1/endpoints/task_building.py | 修 | `_task_build_event_stream`：thinking_chunks/steps 扁平收集改按 LLM 回合收集（`rounds` + `_ensure_round`，以 `_history_trajectory` 为回合边界、thinking 到达时当前回合已有 steps 亦切新回合）；`_persist_chat_exchange`：签名 steps/thinking→rounds，新增按顺序对齐逻辑（第 k 回合附加到第 k 个轨迹 `assistant(tool_calls)` 消息，剩余回合附最终 assistant） | 修复会话恢复后思考丢失/步骤贴一起（issues 2026-09-26 问题四） |
| 2026-09-26 | tests/api/v1/test_task_building_session.py | 修/增 | UT-17 适配 rounds 参数与轨迹附加断言；+UT-17b 多回合对齐、+UT-17c 剩余回合附最终消息 | 回合拆分回归 |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 修 | _COMMON_RULES 新增第 0 条（部署前规划强制：目标/运行条件/端口方案须询问用户/防火墙/步骤，确认后 deploy_plan 落清单再执行） | issues 问题五 |
| 2026-09-26 | app/skills/deploy_plan.py | 增 | DeployPlanSkill（steps→Markdown 清单写 specification.tasks，spec/checklist 保留现有值；支持完成项 [x] 更新） | 待办清单落右面板「任务清单」 |
| 2026-09-26 | app/skills/registry.py | 修 | SKILL_REGISTRY +DeployPlanSkill | 技能注册 |
| 2026-09-26 | tests/skills/test_deploy_plan.py | 增 | 清单写入/三件套保留/缺参/空 steps 4 项 | deploy_plan 回归 |
| 2026-09-26 | scripts/e2e_bt_site_20260926.py | 增 | 宝塔 MCP 建站 E2E 脚本（连接测试/建站/Write 首页/HtmlProjectInfo 核验/HTTP Host 头验证，--cleanup 可选） | issues 问题六：实测 MCP 建站全链路通过 |
| 2026-09-26 | app/agents/orchestrator.py | 修 | `_inject_attachment_images` 重构：+`_load_attachment_records`（批量读附件记录）+`_describe_file_attachment`（非图片附件结构化描述：文件名/类别/大小/附件 ID/下载地址 + curl 拉取提示）；占位按 kind 分组（image→图片占位+视觉 parts，archive/file→文件附件文本注入，视觉模型下以 text part 与图片 parts 共存）；不存在附件 ID 下 notice | 修复 zip 附件被识别为图片（issues 问题七：上传产物包 AI 可见信息为零） |
| 2026-09-26 | tests/agents/test_orchestrator_attachments.py | 增 | zip 占位含下载地址/图片视觉回归/混合附件 parts 共存 3 项 | 附件分类注入回归（orchestrator 全量 70/70） |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 修 | HTML SOP 第 1 条扩写为「产物包拉取与解压」：curl 拉包 → unzip 解压（python3 -m zipfile 兜底）→ 确认产物结构再建站 | 补齐 zip 部署链路解压步骤缺口（用户问询发现） |
| 2026-09-26 | app/services/attachment_service.py | 修 | _ALLOWED_KINDS archive mime 白名单扩充（rar/7z/gzip/bzip2/xz/tar）；+_ARCHIVE_EXTENSIONS/_IMAGE_EXTENSIONS 扩展名兜底；_resolve_kind(mime, filename) 双判定；落盘后缀扩展名优先；错误文案更新 | 压缩包全格式支持（用户需求：zip/rar/7z/tar.gz/tgz/tar.bz2/tar.xz/tar） |
| 2026-09-26 | opc-web-console/src/components/TaskNewChatPanel.tsx | 修 | Upload accept 放开压缩包全家桶；isAllowedFile 扩展名+mime 双判定 | 前端选择放开（tsc+build 过） |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 修 | +_PHP_SOP（Site 族工具/产物拉取解压/数据库/SiteCreate/SiteGetConfig/核验）；DEPLOY_SOPS+_SCENE_TITLES+SCENE_TOOL_WHITELISTS+other_families 加 php；通用规则 0 加**项目类型自动判定**（package.json→nodejs 等 5 特征映射，判不准问用户）；bt_deploy_guide 描述加 php | 多类型部署（用户需求：不限于 HTML，python/nodejs/go/html/php 全覆盖 + 产物特征自动判型） |
| 2026-09-26 | tests/skills/test_bt_deploy_prompts.py | 修 | UT-W1 五场景+php 白名单断言；php Site 族仅 php 场景放行回归；UT-W2 五类型 | 白名单回归 |
| 2026-09-26 | tests/services/test_attachment_service.py | 增 | 压缩包全格式放行（6 格式含 octet-stream 误报扩展名兜底）+evil.exe 拒绝回归 | 附件多格式回归 |
| 2026-09-26 | tests/skills/test_bt_mcp_skills.py | 修 | UT-S9 加 php marker | SOP 回归（UT-W3 白名单一致性校验抓出 SiteModify 手写错名已修正） |
