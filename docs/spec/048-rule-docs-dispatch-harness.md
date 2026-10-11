# 开发规则文档库与可选规则包下发 Harness（ADR-048）

## 1. 测试金字塔

```
        E2E（1 个场景：文档库→绑定→派发→节点落盘→注入）
       /   IT（创建锁定版本 / 下载端点 / payload 构造 / 派发缺文档降级）
      /     UT（service CRUD / token 签名校验 / schema / AGENTS.md 生成器 / 文件名净化）
```

## 2. 单元测试（UT）

| 编号 | 测试点 | 位置（新增文件） |
| --- | --- | --- |
| UT-1 | standard_docs service：创建/更新 version+1/删除/列表元数据不含 content | `opc-central-dispatch-hub/app/tests/services/test_standard_docs.py` |
| UT-2 | token 生成与校验：有效通过、过期 401、篡改 401 | 同上（或 services/test_rule_doc_token.py） |
| UT-3 | file_name 净化：中文名→合法文件名、路径穿越字符剔除 | 同上 |
| UT-4 | Agent 端 `TaskDispatchPayload` 解析 rule_docs（含/不含字段） | `opc-agent-programming/tests/test_config.py` 风格新文件 `test_rule_docs.py` |
| UT-5 | AGENTS.md 索引生成：含全部文档 file_name+apply_when、含按需加载指令、内容确定性 | 同上 |
| UT-6 | 节点落盘路径越界防护：file_name 二次取 basename | 同上 |
| UT-7 | scheduler `_build_dispatch_message`：有/无 rule_docs 时 payload 结构 | `opc-central-dispatch-hub/app/tests/services/test_scheduler_rule_docs.py` |

## 3. 集成测试（IT）

| 编号 | 测试点 |
| --- | --- |
| IT-1 | POST /tasks 带 rule_doc_ids → tasks.rule_docs 锁定当时版本；随后 PUT /docs/{id} bump 版本，重新派发仍用旧版本内容与 sha256 |
| IT-2 | 绑定文档被删除后派发 → payload 跳过该文档 + warning，其余正常 |
| IT-3 | GET /tasks/dispatch/rule-docs：有效 token 200 文件流且 sha256 与 payload 一致；无效/过期 token 401 |
| IT-4 | Agent hub_client 派发处理：下载成功落 blobs / 下载失败重试 1 次后 warning 不抛异常 |
| IT-5 | task_bridge 执行链路：有 rule_docs 时工作目录出现 .tilacode/rules/* 与 AGENTS.md；无 rule_docs 时目录干净（回归） |

## 4. E2E 测试

| 编号 | 场景 | 步骤 |
| --- | --- | --- |
| E2E-1 | 规则包全链路 | ① 管理页创建文档 A → ② 创建任务绑定 A → ③（WS 模拟 Agent，仿 ADR-047 验证方式）收到 payload 含 rule_docs 与 url+token → ④ 下载并校验 sha256 → ⑤ 模拟 task_bridge 落盘 → ⑥ 断言 .tilacode/rules/A.md 内容与 AGENTS.md 索引 → ⑦ 浏览器验证文档管理页四操作 |

## 5. CI 门禁

- hub：`pytest app/tests` 全过 + `ruff check` 全过；
- agent：`pytest tests` 全过；
- 前端：`tsc --noEmit` + `eslint` 全过；
- 迁移：`alembic upgrade head` 在空库与存量库均可执行（含 seed 两行）。

## 6. 实施前置验证（非测试，但为门禁）

- V-1：tilacode 对项目级 `AGENTS.md` 的自动注入行为验证（本地起 tilacode 会话，确认模型上下文含该文件；失败则启用 Spec §5 回退方案并更新 ADR）。
