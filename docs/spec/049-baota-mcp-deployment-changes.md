# 宝塔 MCP 部署一条龙 Changes（ADR-049）

> 关联决策：[ADR-049](../adr/049-baota-mcp-deployment.md) / [Spec](049-baota-mcp-deployment-spec.md) / [Harness](049-baota-mcp-deployment-harness.md)
> 边开发边实时登记，禁止开发完成后一次性补记。

## 变更清单

| 日期 | 文件 | 类型 | 函数/类级明细 | 作用 |
| --- | --- | --- | --- | --- |
| 2026-09-26 | docs/adr/049-baota-mcp-deployment.md | 增 | 全文 | 决策记录：中枢直连/自研客户端/代理模式/SOP 沉淀/两段式确认 |
| 2026-09-26 | docs/spec/049-baota-mcp-deployment-spec.md | 增 | 全文 | 用户故事/流程/接口/数据模型/验收 |
| 2026-09-26 | docs/spec/049-baota-mcp-deployment-harness.md | 增 | 全文 | 测试清单与 CI 门禁 |
| 2026-09-26 | docs/spec/049-baota-mcp-deployment-changes.md | 增 | 全文 | 本清单骨架 |
| 2026-09-26 | docs/INDEX.md | 修 | 1.1/1.2/1.3 追加 ADR-049 行；六、更新记录追加 | 决策登记 |
| 2026-09-26 | app/core/errors.py | 修 | ErrorCode 新增 BT_MCP_SERVER_NOT_FOUND/NAME_DUPLICATE/CONNECT_FAILED/CALL_FAILED/CONFIG_INVALID（207001-5） | 宝塔 MCP 域错误码 |
| 2026-09-26 | app/models/bt_mcp_server.py | 增 | BtMcpServer 模型（name 唯一/local_url/public_url/address_mode/api_token/ca_cert/enabled/remark） | 服务器连接表 |
| 2026-09-26 | app/models/__init__.py | 修 | 导出 BtMcpServer | 模型注册 |
| 2026-09-26 | alembic/versions/20260926_bt_mcp_servers.py | 增 | upgrade/downgrade 建/删 bt_mcp_servers | 迁移（down=20260925_meta_rules_v2） |
| 2026-09-26 | app/services/bt_mcp_client.py | 增 | BtMcpClient（initialize/list_tools/call_tool/_rpc/CA cadata 加载/有状态 session 兼容）、BtMcpError、_translate_http_error/_translate_network_error、sanitize_arguments、timed_call | 自研轻量 MCP 客户端与错误翻译 |
| 2026-09-26 | app/services/bt_mcp_risk.py | 增 | TOOL_CATEGORIES/TOOL_RISKS（high/medium 显式+low 派生）、get_tool_risk/get_tool_category/summarize_tools | 98 工具分类与风险映射 |
| 2026-09-26 | app/schemas/bt_mcp.py | 增 | BtMcpServerCreate（apply_mcp_info/validate_completeness）/Update/Response/ListResponse/BtMcpToolInfo/ToolsResponse/TestResponse | API Schema（凭据脱敏） |
| 2026-09-26 | app/services/bt_mcp_server_service.py | 增 | BtMcpServerService（list/list_enabled/get/create/update/delete/resolve_url/build_client/test_connection/list_tools/_ensure_name_available） | 服务器管理服务 |
| 2026-09-26 | app/api/v1/endpoints/bt_mcp.py | 增 | list/create/get/update/delete/test_connection/list_tools 路由（_to_response 脱敏转换） | /bt-mcp/servers 端点 |
| 2026-09-26 | app/api/deps.py | 修 | get_bt_mcp_server_service | 依赖注入 |
| 2026-09-26 | app/api/v1/api.py | 修 | 挂载 bt_mcp.router（prefix=/bt-mcp/servers） | 路由注册 |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 增 | _COMMON_RULES/_PYTHON_SOP/_NODEJS_SOP/_GO_SOP/_HTML_SOP/_TROUBLESHOOT、DEPLOY_SOPS、get_deploy_sop | 部署 SOP 精要（提炼官方 7 个 Skill） |
| 2026-09-26 | app/skills/bt_mcp.py | 增 | BtMcpServersSkill/BtMcpToolsSkill/BtMcpSchemaSkill/BtMcpCallSkill（waiting_confirm 指引+链路日志）/BtDeployGuideSkill | 5 个编排技能（代理入口模式） |
| 2026-09-26 | app/skills/registry.py | 修 | SKILL_REGISTRY 追加 5 技能 | 技能注册 |
| 2026-09-26 | tests/services/test_bt_mcp_client.py | 增 | UT-C1~C10（协议/会话/错误翻译/CA/脱敏） | 客户端单测 |
| 2026-09-26 | tests/api/v1/test_bt_mcp.py | 增 | UT-A1~A9（CRUD/mcp_info 导入/脱敏/test 端点/工具清单） | API 单测 |
| 2026-09-26 | tests/skills/test_bt_mcp_skills.py | 增 | UT-S1~S10（空列表/目录/schema/成功/确认/失败/SOP） | 技能单测 |
| 2026-09-26 | opc-web-console/src/types/btMcp.ts | 增 | BtMcpServerItem/Create/Update/TestResponse/ToolInfo 等类型 | 前端类型 |
| 2026-09-26 | opc-web-console/src/api/btMcp.ts | 增 | fetch/add/update/delete/test/fetchBtMcpServers 系列 | 前端 API |
| 2026-09-26 | opc-web-console/src/components/BtMcpPanel.tsx | 增 | BtMcpPanel（列表/mcp_info 导入/编辑/测试连接/工具清单/启停/删除） | 设置页管理面板 |
| 2026-09-26 | opc-web-console/src/pages/Settings.tsx | 修 | 引入并挂载 BtMcpPanel 卡片 | 设置页接线 |
| 2026-09-26 | app/skills/bt_mcp.py | 增 | BtMcpSetupSkill（提示词 URL 提取 _extract_url / mcp_info 解析 _extract_mcp_info / 自动命名 _derive_server_name / 重名后缀重试 / 下载-解析-添加-测试全链路）+ import 扩展 | 粘贴官方提示词自动配置服务器（用户需求：AI 自配置） |
| 2026-09-26 | app/skills/registry.py | 修 | SKILL_REGISTRY 追加 BtMcpSetupSkill | 第 6 个技能注册 |
| 2026-09-26 | tests/skills/test_bt_mcp_skills.py | 增 | UT-SET1~7（成功/下载失败/无效内容/缺 token/重名后缀/测试失败仍保存/无 URL） | setup 技能单测 |
| 2026-09-26 | opc-web-console/src/components/TaskNewDocPanel.tsx | 修 | TaskNewDocPanelProps 补 onCancelTask?: () => Promise<void> 并透传 PlanReviewPanel | 修复存量 build 阻断（9-25 取消任务遗留） |
| 2026-09-26 | opc-web-console/src/components/TaskNewInputBar.tsx | 修 | onStop 改可选 + onClick={() => onStop?.()} | 修复存量 build 阻断（9-25 取消任务遗留） |
| 2026-09-26 | opc-web-console（npm run build） | 构建 | SPA 产物更新（含 BtMcpPanel） | 用户可见管理界面（此前构建产物停留在旧版本） |
| 2026-09-26 | tests/api/v1/test_bt_mcp.py | 修 | _patch_client 修复：side_effect 模式跳过 return_value[1] 访问 | 修复 UT-A8 测试 TypeError（side_effect 时 return_value=None 导致的崩溃） |
| 2026-09-26 | tests/skills/test_bt_mcp_skills.py | 修 | test_setup_success 断言 token[:8] 截断前缀匹配 | 修复 token 脱敏断言与代码 token[:8] 截断一致（btmcp_se*** vs btmcp_set***） |
| 2026-09-26 | app/skills/bt_deploy_prompts.py | 修 | _COMMON_RULES 规则 4 改「参数必须与 schema 逐字一致」（含 site_name≠project_name 示例）；_HTML_SOP 新增第 0 条「工具族严格区分」（HtmlProject 族 vs 网站 PHP 族，SiteList/SiteGetConfig 查不到静态站）+ 修正「域名:端口」写法警告（实测 IP:8080 报端口范围不合法，先纯创建再 HtmlProjectModify）+ HtmlProjectInfo(site_name=) 参数名明示 | 修复 docs/issues/2026-09-26 问题二：工具族串用/参数名误用/SOP 端口写法误导 |
| 2026-09-26 | tests/skills/test_bt_mcp_skills.py | 增 | test_deploy_guide_html_tool_family_guard（UT-S9b：工具族区分/site_name/端口警告/全类型「逐字一致」断言） | SOP 修正回归测试（19/19 passed） |

（后续开发中实时追加）
