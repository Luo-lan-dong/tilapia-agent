# 任务执行模型选择与凭据下发 Harness

关联决策：[ADR-029](../adr/029-task-executor-model-dispatch.md) / [Spec](./029-task-executor-model-spec.md)

## 测试金字塔

```
        E2E（1）          浏览器选择模型创建任务 → 节点执行走自有模型
       集成（API/WS）     创建请求落库、派发 payload 组装、Agent 桥接注入
       单元（Service）     executor_model 校验与默认值、payload 组装纯逻辑
```

## 单元测试清单

| # | 用例 | 断言 |
|---|------|------|
| U1 | executor_model 缺省 | 任务落库 executor_model 为 NULL，响应不带该字段（exclude_unset） |
| U2 | executor_model 非法 source | 422 + 202012（复用 OUTPUT_DIR_INVALID 语义或新增错误码，以实现为准） |
| U3 | opencode_free 未指定 model | 归一化为默认 `mimo-v2.5-free` |
| U4 | 派发 payload 组装（hub 来源） | 含 api_key/base_url 且值来自实时读取的 system_settings；executor_model.model 正确 |
| U5 | 派发 payload 组装（opencode_free） | 不含 api_key/base_url |
| U6 | 派发 payload 组装（中枢 LLM 未配置） | hub 来源但不带 api_key（降级） |
| U7 | 日志脱敏 | 派发日志不含 api_key 明文 |

## 集成测试清单

| # | 用例 | 断言 |
|---|------|------|
| I1 | `POST /tasks` 带 executor_model=opencode_free | 201；DB 行 source/model 正确 |
| I2 | `POST /tasks` 带 executor_model=hub + model 覆盖 | 201；DB 行正确 |
| I3 | Agent 桥接收到 hub 来源任务 | 执行前调用 connect/key 注入；session model providerID=deepseek |
| I4 | Agent 桥接收到 free 来源任务 | 不调用注入；session model=opencode/mimo-v2.5-free |
| I5 | Agent 收到无 executor_model 的 payload | 回退默认（不注入、免费模型） |
| I6 | Key 热更新后再派发 | 新 payload 使用新 Key |

## E2E 测试清单

| # | 场景 | 步骤 | 预期 |
|---|------|------|------|
| E1 | 自有模型执行任务 | 新建对话 → 确认默认选中枢模型 → 提交需求生成三件套 → 创建任务 → 自动派发到在线 code 节点 | 任务 progress 推进越过执行阶段、无 provider 400；产物正常回传 |
| E2 | 免费模型尝试 | 新建对话选「官方免费」→ 创建任务 → 派发 | 任务执行使用免费通道；若被官方拒绝则 failed 且失败原因包含官方报错 |

## CI 门禁

- hub：`ruff check` + `ruff format --check`（仅变更文件）+ `pytest tests/`（全量）+ 文件行数 ≤300（豁免按规范声明）。
- web：`eslint` + `tsc -b` + `vitest`。
- agent：`pytest tests/`（含新增桥接用例）。
- 任一失败禁止合并。

## 回归范围

- 旧 payload 兼容（I5）、任务创建/派发既有用例全量、ADR-028 产物回传不回归。
