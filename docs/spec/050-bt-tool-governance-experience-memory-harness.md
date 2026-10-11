# 宝塔工具治理三层防线与经验记忆 Harness（ADR-050）

## 1. 测试金字塔

| 层级 | 范围 | 工具 |
| --- | --- | --- |
| 单元测试 | 白名单常量与 SOP 文本、参数名校验逻辑、experience_service、API CRUD、收尾总结钩子 | pytest + pytest-asyncio |
| 集成测试 | 迁移升级/回滚、bt_mcp_call 全链（校验→记录→注入）、技能注册 | pytest + 测试库 |
| E2E 冒烟 | 真实服务器错参数拦截 + 对话总结闭环 + /memory 页面 | 运行中枢 + curl / 编排对话 |

## 2. 单元测试清单

### 2.1 test_bt_deploy_prompts.py（白名单）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-W1 | 四场景白名单常量齐备 | SCENE_TOOL_WHITELISTS 含 python/nodejs/go/html；每场景含本族 Project 工具 |
| UT-W2 | get_deploy_sop 附白名单段落 | 四类型返回文本均含「本场景工具」与禁族描述 |
| UT-W3 | 白名单与风险映射一致性 | 白名单中的工具均存在于 TOOL_RISKS/目录（防手写错名） |

### 2.2 test_bt_mcp_skills.py 增补（校验钩子）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-V1 | 未知参数名拦截 | arguments 含 schema 外参数名 → 不调 MCP（client 未被调用）、success=False、summary 含正确参数名清单 |
| UT-V2 | 必填缺失拦截 | summary 含必填参数清单 |
| UT-V3 | 合法参数放行 | 正常调用 MCP，行为与现状一致 |
| UT-V4 | schema 获取失败降级 | tools/list 异常 → 跳过校验直接调用，WARNING 日志 |
| UT-V5 | 场景软提示 | 会话已读 SOP（上下文标记）+ 工具出白名单 → 结果附提示但仍执行 |
| UT-V6 | 失败自动记录 | status=false → experience_service.record_failure 被调（mock 断言参数） |
| UT-V7 | 命中注入 | find_matches 命中 → summary 含经验文本；未命中 → 无附加 |
| UT-V8 | 去重 | 同 session 同签名已有 pending → 不重复写 |

### 2.3 test_experience_service.py（服务）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-E1 | record_failure 落库 | tool/signature/problem/session 齐备，status=pending |
| UT-E2 | signature 归一化 | 含数字/空白波动的 msg 归一化为同签名 |
| UT-E3 | find_matches | 按 signature 匹配 resolved/pending 条目，返回按 hit_count/时间排序 |
| UT-E4 | resolve 回填 | root_cause/solution 写入、status=resolved、resolved_at 非空 |
| UT-E5 | list 搜索过滤 | keyword 模糊 + status 过滤 + 分页 |
| UT-E6 | delete | 删除后查询 404 |

### 2.4 test_experience_api.py（端点）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-EP1 | GET 列表/详情/404 | 200/200/208001 |
| UT-EP2 | PATCH 编辑回填 | 字段更新生效；非法 status 422 |
| UT-EP3 | DELETE | 200；再查 208001 |

### 2.5 test_orchestrator.py 增补（收尾总结）

| 编号 | 用例 | 断言 |
| --- | --- | --- |
| UT-O1 | 收尾检查 pending | 会话有 pending 条目时收尾提示词包含条目内容；AI 输出含草案 |
| UT-O2 | 确认回填 | 用户确认轮后条目 resolved（经 service 断言） |
| UT-O3 | 无 pending 不注入 | 正常收尾无额外提示 |

## 3. 集成测试清单

| 编号 | 用例 |
| --- | --- |
| IT-1 | Alembic upgrade/downgrade 循环（experience_records 建删） |
| IT-2 | bt_mcp_call 全链：错参数拦截 → 纠正后成功 → 注入失败场景构造失败 → pending 落库 → 二次失败命中注入 + hit_count+1 |
| IT-3 | /skills 端点技能注册（若含 experience_recall） |

## 4. E2E 冒烟（真实环境）

| 编号 | 场景 | 预期 |
| --- | --- | --- |
| E2E-1 | 对话诱导 AI 用 HtmlProjectInfo 传 project_name | 前端步骤卡显示拦截结果与正确参数清单，未到达宝塔服务端（tool_call_logs 无该次失败调用） |
| E2E-2 | 部署对话读 SOP | 回复引用本场景工具白名单；调用均在白名单内 |
| E2E-3 | 构造一次失败 → 收尾确认 → 复现同签名失败 | 第二次失败摘要含历史经验；/memory 页面条目 resolved 且 hit_count≥1 |
| E2E-4 | /memory 页面 | 列表/搜索/编辑/删除/高亮全流程可用 |

## 5. CI 门禁

- ruff format + check 通过（app/ 全量）。
- 新增测试全过；既有测试无回归（hub 全量 pytest）。
- 文件长度 ≤ 600 行（coding-standards 4.3）。
