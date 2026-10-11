# 多模型管理与自定义接入 Harness

关联决策：[ADR-030](../adr/030-llm-models-management.md) / [Spec](./030-llm-models-management-spec.md)

## 测试金字塔

```
        E2E（1）          添加自定义模型 → 启用 → 新建对话选择该模型创建任务 → 派发注入对应凭据
       集成（API）        models CRUD/激活/删除契约、executor_model custom 落库
       单元（Service）     CRUD 纯逻辑、激活互斥、get_active 查询、派发组装
```

## 单元测试清单

| # | 用例 | 断言 |
|---|------|------|
| U1 | add_model 首个自动激活 | 插入行 is_active=True |
| U2 | add_model 非首个不激活 | 新行 is_active=False，原激活行不变 |
| U3 | activate_model 互斥切换 | 目标 True、其余全部 False |
| U4 | delete_model 使用中拒绝 | TaskDispatchError/409 语义 |
| U5 | delete_model 非使用中成功 | 行删除 |
| U6 | get_active_provider 放开 provider 限制 | 非 deepseek 名称为 provider 的激活行可返回 |
| U7 | 派发组装 custom 来源 | 按 provider_id 行注入 provider/model/api_key/base_url |
| U8 | 派发组装 custom 行已删除 | 不带凭据 + warning 日志 |
| U9 | 列表 api_key 脱敏 | 响应中不含明文 Key |

## 集成测试清单

| # | 用例 | 断言 |
|---|------|------|
| I1 | GET /settings/llm/models | 200；items 含脱敏 key 与 is_active 标记 |
| I2 | POST /settings/llm/models | 201/200；列表新增 |
| I3 | PUT activate | 200；GET 后目标行 is_active=True |
| I4 | DELETE 使用中 | 409 业务码 |
| I5 | POST /tasks 带 custom executor_model | 201；DB 行 {source: custom, provider_id, model} |

## E2E 测试清单

| # | 场景 | 步骤 | 预期 |
|---|------|------|------|
| E1 | 自定义模型全链路 | 设置页添加硅基流动兼容模型 → 启用 → 新建对话选择该模型 → 创建任务派发 | 派发 payload 注入该行凭据；节点以该模型执行（task_started 上报 model 描述） |

## CI 门禁

- hub：`ruff check` + `pytest tests/`（含新增）+ 文件行数 ≤300（豁免声明）。
- web：`eslint` + `tsc -b` + `vitest`。
- 任一失败禁止合并。

## 回归范围

- 既有 GET/PATCH /settings/llm 用例、test_llm_provider_service、
  test_task_executor_model（hub/free 来源组装）、ADR-029 执行模型链路不回归。
