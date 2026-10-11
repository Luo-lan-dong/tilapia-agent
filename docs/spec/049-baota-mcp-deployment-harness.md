# 宝塔 MCP 部署一条龙 Harness（ADR-049）

## 1. 测试金字塔

| 层级 | 范围 | 工具 |
| --- | --- | --- |
| 单元测试 | bt_mcp_client（协议/错误翻译/CA/脱敏）、5 个技能执行、API CRUD | pytest + httpx.MockTransport + pytest-asyncio |
| 集成测试 | 迁移升级/回滚、API→service→DB 全链（测试库）、技能注册表 | pytest + 现有 conftest |
| E2E 冒烟 | 真实服务器只读验证 + 对话流部署引导 | 运行中枢 + curl / 编排对话 |

## 2. 单元测试清单

### 2.1 test_bt_mcp_client.py（客户端）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-C1 | initialize 成功 | 解析 serverInfo/protocolVersion；无 session id 时后续不带该头 |
| UT-C2 | 有状态兼容 | Mock 返回 Mcp-Session-Id → 后续请求携带同值头 |
| UT-C3 | tools/list | 返回工具清单；协议 SSE 与纯 JSON 两种响应均可解析 |
| UT-C4 | tools/call 成功 | 透传 arguments；解析 `{status,msg,data}` |
| UT-C5 | ip denied 翻译 | HTTP 403 + body ip denied → BtMcpError，message 含"白名单"与"出口 IP"指引 |
| UT-C6 | 401 翻译 | message 含"令牌/安全授权"指引 |
| UT-C7 | TLS 错误翻译 | ssl 层异常 → message 含"证书链"指引 |
| UT-C8 | 连接拒绝翻译 | ConnectError → message 含"8765 端口/防火墙"指引 |
| UT-C9 | ca_cert 生效 | 配置 ca_cert → httpx 收到基于该 CA 的 SSLContext（信任自签 Mock 证书）；无 ca_cert → 默认验证 |
| UT-C10 | 超时控制 | 请求超时抛 BtMcpError 且 message 可读 |

### 2.2 test_bt_mcp_api.py（端点）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-A1 | POST 创建（手填） | 201；库中 token/ca 落盘；响应脱敏 |
| UT-A2 | POST 创建（mcp_info JSON 导入） | token/local_url/public_url/ca_cert 自动填充 |
| UT-A3 | name 重复 | 207002 |
| UT-A4 | GET 列表/详情 | 脱敏字段为布尔；无明文凭据 |
| UT-A5 | PATCH 更新 | 缺省保留 token；传空串清除；name 可改备注等 |
| UT-A6 | DELETE | 200；再查 404（207001） |
| UT-A7 | test 连接成功 | mock client → server_info/tool_count 返回 |
| UT-A8 | test 连接失败 | mock 抛 BtMcpError → 207003 + 指引 message |
| UT-A9 | tools 清单 | 返回 name/description/risk |

### 2.3 test_bt_skills.py（技能）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-S1 | bt_mcp_servers 空列表 | summary 引导去设置页添加 |
| UT-S2 | bt_mcp_servers 有数据 | 仅列 enabled；含 id/name/address_mode |
| UT-S3 | bt_mcp_tools | 目录含名称+风险；description 截断防超长 |
| UT-S4 | bt_mcp_schema | 返回完整 schema 文本 |
| UT-S5 | bt_mcp_call 只读成功 | summary 含 msg/data 摘要 |
| UT-S6 | bt_mcp_call waiting_confirm | success=False，summary 含"用户确认"与"confirm=true"指引 |
| UT-S7 | bt_mcp_call status=false | 如实回传 msg |
| UT-S8 | bt_mcp_call 连接失败 | 错误翻译后回传 |
| UT-S9 | bt_deploy_guide 四类型 | 返回对应 SOP 文本；非法类型降级返回通用要点 |
| UT-S10 | 参数缺失守卫 | 缺 server_id/tool 时返回参数不完整而非异常 |

## 3. 集成测试清单

| 编号 | 用例 |
| --- | --- |
| IT-1 | Alembic 迁移 upgrade/downgrade 循环（bt_mcp_servers 建删） |
| IT-2 | 技能注册：5 个技能出现在 /skills 端点且默认启用 |
| IT-3 | API 全链：POST→GET→PATCH→DELETE（测试库） |

## 4. E2E 冒烟（真实环境）

| 编号 | 场景 | 预期 |
| --- | --- | --- |
| E2E-1 | 设置页添加现有服务器（mcp_info 导入）→ 测试连接 | serverInfo=FastMCP，tool_count=98 |
| E2E-2 | 编排对话："查看服务器系统信息" | AI 调 bt_mcp_servers→bt_mcp_call(SystemInfo)，返回真实 CPU/内存 |
| E2E-3 | 编排对话："部署"引导 | AI 询问目标服务器与项目信息，不擅自执行高危操作 |
| E2E-4 | 前端浏览器 | 列表/新增/导入/测试连接/工具清单弹窗全流程可用 |

## 5. CI 门禁

- ruff format + check 通过（app/ 全量）。
- 新增测试全过；既有测试无回归（hub 全量 pytest）。
- 文件长度 ≤ 600 行（coding-standards 4.3，超长需拆分或豁免注释）。
