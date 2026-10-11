# 宝塔 MCP 部署一条龙 Spec（ADR-049）

> 中枢直连宝塔官方 MCP，实现"开发完成 → 部署上线 → 运维排障"的最后一条通道。

## 1. 功能描述

用户在编排对话中发起部署，中枢作为唯一 MCP 客户端连接目标服务器的宝塔 MCP 服务（支持配置多台），AI 按部署 SOP 调用宝塔面板工具完成：产物上服务器 → 建库建环境 → 项目注册 → 域名/SSL/反代 → 健康验证；高危操作经服务端两段式确认在对话中征求用户同意。

## 2. 用户故事

- **US-1 服务器管理**：作为用户，我在设置页添加/编辑/删除多个宝塔 MCP 服务器连接（可粘贴插件生成的 mcp_info JSON 一键导入），点击"测试连接"查看服务端信息与工具清单；凭据脱敏展示。
- **US-2 部署发起**：作为用户，我在对话中说"把任务 X 的产物部署到服务器"，AI 列出可用服务器让我确认目标，收集项目类型/域名等缺失信息。
- **US-3 部署执行**：AI 读取部署 SOP，按顺序调用宝塔工具（上传产物 → 准备环境 → 创建项目 → 轮询 prep_status → 绑域名/SSL → 验证），逐步向我汇报进度。
- **US-4 高危确认**：AI 调用删除/执行命令类高危工具时，服务端返回待确认状态，AI 在对话中向我展示操作内容与影响，我明确同意后 AI 才真正执行。
- **US-5 运维排障**：部署后或平时，我可以让 AI 查询服务器状态、网站日志、容器状态等（只读工具），AI 直接调用对应工具汇报。

## 3. 核心流程

### 3.1 部署主流程

```text
用户："部署任务X"
   │
   ▼
[bt_mcp_servers] 列出启用服务器 ──无──► 提示去设置页添加（终止）
   │
   ▼ 用户选择目标服务器（多台时确认）
[bt_deploy_guide] 读取部署 SOP（按项目类型）
   │
   ▼ 收集缺失信息（项目类型/产物路径/域名/端口）
   │
   ▼
┌─ 按 SOP 循环 [bt_mcp_call] ─────────────────────────┐
│ 1. 产物上服务器（Upload 流程 / Bash 拉取）             │
│ 2. 环境准备（PythonVersion/PythonEnv/NodeVersion…）  │
│ 3. 数据库（DatabaseList→DatabaseCreate）             │
│ 4. 项目创建（PythonProjectCreate analyze_only 先行）  │
│ 5. 轮询 PythonProjectInfo.prep_status → complete     │
│ 6. 域名/SSL/反代（bind_extranet→add_proxy/SiteSSLApply）│
│ 7. 验证（进程/端口/日志/HTTP 200）                     │
└──────────────┬──────────────────────────────────────┘
               │ 高危工具返回 waiting_confirm
               ▼
        对话中展示操作+影响 → 用户明确同意
               │
               ▼
        confirm=true 相同参数重调 → 执行
```

### 3.2 连接与降级链路

```text
bt_mcp_call / 测试连接
   │
   ├─ TLS 失败 ──► 提示重新从插件获取证书链并更新服务器配置
   ├─ ip denied ─► 提示将中枢出口 IP 加入插件白名单（展示检测方法）
   ├─ 401 ──────► 提示 token 失效，重新在插件【安全授权】获取
   ├─ 连接拒绝 ──► 提示检查面板防火墙/云安全组 8765 端口
   └─ 工具返回 status=false ─► msg 如实回传 AI，由 AI 判断处置
```

## 4. 接口设计（`/api/v1/bt-mcp/servers`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `` | 服务器列表（token/ca_cert 脱敏为 has_token/has_ca_cert 布尔） |
| POST | `` | 新增；支持 `mcp_info` 字段传插件 JSON 一键解析（与手填字段二选一或互补） |
| GET | `/{id}` | 详情（脱敏） |
| PATCH | `/{id}` | 更新（token/ca_cert 传空串表示清除，缺省表示保留） |
| DELETE | `/{id}` | 删除 |
| POST | `/{id}/test` | 连接测试：initialize + tools/list，返回 server_info/tool_count/tool_summary |
| GET | `/{id}/tools` | 工具清单（name/description/风险等级） |

错误码（新增 207xxx）：`BT_MCP_SERVER_NOT_FOUND=207001`、`BT_MCP_SERVER_NAME_DUPLICATE=207002`、`BT_MCP_CONNECT_FAILED=207003`、`BT_MCP_CALL_FAILED=207004`。

## 5. 数据模型（`bt_mcp_servers`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID PK | |
| name | String(100) unique | 显示名（如"生产服务器-101.13"） |
| local_url | Text | 内网 MCP 地址 |
| public_url | Text NULL | 公网 MCP 地址 |
| address_mode | String(20) | local/public，默认 local |
| api_token | Text | Bearer Token |
| ca_cert | Text NULL | PEM 证书链全文（自签场景） |
| enabled | Boolean | 默认 true；技能只列 enabled |
| remark | String(500) NULL | |
| created_at / updated_at | TimestampMixin | |

## 6. 技能定义（5 个，仿 ADR-033 框架）

| 技能 | 参数 | 返回 |
| --- | --- | --- |
| `bt_mcp_servers` | 无 | 启用服务器清单 [{id, name, remark, address_mode}] |
| `bt_mcp_tools` | server_id | 工具目录 [{name, description(截断), risk}] |
| `bt_mcp_schema` | server_id, tool | 该工具完整 inputSchema + 官方说明 |
| `bt_mcp_call` | server_id, tool, arguments | `{status, msg, waiting_confirm?, data}` + 高危指引 |
| `bt_deploy_guide` | project_type(python/nodejs/go/html) | 对应 SOP 精要文本 |

## 7. 验收标准

1. US-1：mcp_info JSON 导入后各字段自动填充正确（token/双地址/证书链）；测试连接返回工具数 98；列表接口响应无明文 token/证书。
2. US-2：多服务器时 AI 先列出让用户选；未添加任何服务器时 AI 引导去设置页。
3. US-3：对真实服务器执行只读 `SystemInfo` 调用返回真实系统数据；部署 SOP 中关键顺序（分析→版本→环境→项目→轮询）被 AI 遵循。
4. US-4：`SiteDelete(confirm 缺省)` 返回 waiting_confirm，AI 停止并询问；用户同意后 confirm=true 重调成功。
5. 全链路日志：每次 tools/call 落 tool_call_logs（kind=bt_mcp_call，参数中 token/密码字段脱敏）。
6. 迁移可升级可回滚；无服务器配置时对话技能优雅提示、API 返回空列表不报错。
