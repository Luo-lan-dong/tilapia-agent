# OPC 节点显示名自定义 + Agent 类型更名为 `code` · 测试契约

> 本文档配套 ADR-016 / [016-agent-node-display-name-and-code-type-spec.md](./016-agent-node-display-name-and-code-type-spec.md)。
> 测试金字塔参考 [spec/002-central-dispatch-hub-harness.md](./002-central-dispatch-hub-harness.md)。

## 1. 测试金字塔

```
          ┌──────────────────┐
          │   E2E（1 场景）   │  Playwright + 真后端
          └────────┬─────────┘
                   │
        ┌──────────┴──────────┐
        │ 契约 / 集成（5 场景）│  真实 PostgreSQL + httpx AsyncClient
        └──────────┬──────────┘
                   │
            ┌──────┴──────┐
            │ 单元（11 场景）│  纯函数 / mock
            └─────────────┘
```

| 层       | 数量 | 跑分                    | 负责人 |
| ------- | -- | --------------------- | --- |
| 单元      | 11 | pytest -m unit        | AI  |
| 集成 / 契约 | 5  | pytest -m integration | AI  |
| E2E     | 1  | Playwright            | AI  |

## 2. 单元测试（11 项）

### 2.1 Agent 端 `_generate_name()`（4 项，落在 `opc-agent-programming/tests/test_config.py`）

| 用例                                                             | 描述                                                                                      | 期望                                                                |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `test_generate_name_uses_freedesktop_os_release_ubuntu`        | mock `platform.freedesktop_os_release()` 返回 `{"NAME": "Ubuntu", "VERSION_ID": "22.04"}` | `"Ubuntu 22.04"`                                                  |
| `test_generate_name_uses_freedesktop_os_release_macos`         | 返回 `{"NAME": "macOS", "VERSION_ID": "14.4"}`                                            | `"macOS 14.4"`                                                    |
| `test_generate_name_falls_back_to_platform_when_no_os_release` | mock `freedesktop_os_release()` 抛 `OSError`                                             | 走 `platform.platform()` 分支；不抛异常；非空字符串                             |
| `test_generate_name_falls_back_to_hostname_when_all_fail`      | mock 全部抛异常                                                                              | `"unknown"` 或 `socket.gethostname()` 结果，**不**带 `-programming-` 后缀 |

### 2.2 Agent 端 `_generate_client_id()`（1 项，新增）

| 用例                                         | 描述                   | 期望                                              |
| ------------------------------------------ | -------------------- | ----------------------------------------------- |
| `test_generate_client_id_format_unchanged` | 不允许动 client\_id 生成逻辑 | 仍为 `{hostname}-{8位hex}`，且不与 `_generate_name` 混用 |

### 2.3 中枢 Service `update_node_name`（4 项，落在 `opc-central-dispatch-hub/tests/services/test_agent_node_management_service.py`）

| 用例                                        | 描述                                                     | 期望                                                                                   |
| ----------------------------------------- | ------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `test_update_node_name_happy_path`        | 节点 A name=`old`，PATCH `name="new"`                     | DB name = `new`；返回 `AgentNodeResponse`；日志含 `node_name_updated`                       |
| `test_update_node_name_rejects_duplicate` | 同 `agent_type` 下已有 `name="taken"`，PATCH `name="taken"` | 抛 `BusinessError(202010)`；DB name 未变；日志 `node_name_update_rejected` reason=duplicate |
| `test_update_node_name_rejects_blank`     | PATCH `name="   "`                                     | 抛 `BusinessError(202011)`；DB name 未变；日志 reason=invalid                               |
| `test_update_node_name_not_found`         | 不存在的 node\_id                                          | 抛 `AgentNotFoundError`；日志 reason=not\_found                                          |

### 2.4 中枢 Service `get_install_script`（2 项）

| 用例                                                 | 描述                               | 期望                                                          |
| -------------------------------------------------- | -------------------------------- | ----------------------------------------------------------- |
| `test_get_install_script_supports_code_linux`      | `agent_type='code', os='linux'`  | 返回 200，`script_url` 含 `install-agent-programming-linux.sh`  |
| `test_get_install_script_rejects_other_agent_type` | `agent_type='video', os='linux'` | 抛 `BusinessError(202004)` (INSTALL\_SCRIPT\_NOT\_SUPPORTED) |

## 3. 集成 / 契约测试（5 项，真实 PostgreSQL）

### 3.1 Alembic 迁移

| 用例                                           | 描述                                                   | 期望                                                                   |
| -------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------- |
| `test_migration_renames_programming_to_code` | 迁移前手动 `INSERT agent_type='programming'` 3 行          | 迁移后 `agent_type='code'` 行数 = 3；`programming` 行数 = 0                  |
| `test_migration_adds_partial_unique_index`   | 检查 `pg_indexes`                                      | 索引 `ux_agent_nodes_agent_type_name` 存在，定义包含 `WHERE name IS NOT NULL` |
| `test_migration_renames_legacy_duplicates`   | 迁移前手动插入 `(agent_type='programming', name='dup')` 2 行 | 迁移后 1 行 name='dup'，1 行 name='dup-dup1'，无重复                           |

### 3.2 PATCH API 契约

| 用例                                                  | 描述                         | 期望                                                                                     |
| --------------------------------------------------- | -------------------------- | -------------------------------------------------------------------------------------- |
| `test_patch_agent_node_200_returns_response_schema` | PATCH 改 name 成功            | 200，body.data 字段集 = AgentNodeResponse 的字段集（id/name/agent\_type='code'/status/load/...） |
| `test_patch_agent_node_422_duplicate`               | httpx AsyncClient PATCH 重名 | 422，code=202010；DB 未变；中继日志有 WARNING                                                    |

## 4. E2E（1 项，Playwright + 真实中枢）

### 4.1 多容器同 hostname 改名场景

**前置**：

* 启动 PostgreSQL / Redis / 中枢 / 前端 (`pnpm dev`)。

* 启动 3 个 Linux 容器，hostname 都设成 `localhost.localdomain`。

* 每个容器跑 install 命令，`--agent-name` 留空，期望出现 `Ubuntu 22.04` / `Ubuntu 22.04` / `Ubuntu 22.04`（三台都叫 Ubuntu 22.04）→ 出现列表重名。

**步骤**：

1. 打开 `http://localhost:5173/nodes`，截图列表，记录重名。
2. 打开第一个节点的 `NodeDetailDrawer`，把 name 改为 `dev-machine-A`，保存。
3. 重复对第二个 / 第三个节点改名为 `dev-machine-B` / `dev-machine-C`。
4. 关闭抽屉，刷新列表，截图。
5. 验证三条记录的 name 分别显示 `dev-machine-A` / `dev-machine-B` / `dev-machine-C`。
6. 验证 `agent_type` 列全部显示 `code`。
7. 在后端 logs 里 grep `node_name_updated` 应能命中 3 条。
8. 跑 Alembic 降级（`alembic downgrade -1`）再升级（`alembic upgrade head`），验证 migration 幂等。

**期望**：

* 列表页无重名。

* agent\_type 全部 `code`。

* 升 / 降级后数据一致（不会回退到 `programming`）。

## 5. CI 门禁

PR 合并前必须通过：

1. `ruff check app tests --select E,F,W` —— 无错误。
2. `pytest tests/services/test_agent_node_management_service.py -m "unit or integration"` —— 全部通过。
3. `pytest tests/alembic/` —— 迁移幂等测试通过。
4. `cd opc-web-console && pnpm typecheck` —— 无错误。
5. `cd opc-web-console && pnpm lint` —— 无错误。
6. 手动执行 E2E 1 场景通过 + 截图归档。

## 6. 失败重试与 flaky 处理

* Agent `_generate_name()` 单元测试在 Windows 上跑时，`freedesktop_os_release` 不存在，需 mock；CI 上用 `pytest --strict-markers` 强制要求显式 mock。

* E2E 涉及多个容器，使用 docker-compose 编排；如本地 docker 不可用，标记 `pytest.skip("docker unavailable")`，不允许 silently pass。

## 7. 关联文档

* [ADR-016](../adr/016-agent-node-display-name-and-code-type.md)

* [016-agent-node-display-name-and-code-type-spec.md](./016-agent-node-display-name-and-code-type-spec.md)

* [002-central-dispatch-hub-harness.md](./002-central-dispatch-hub-harness.md) — 通用测试金字塔

* [api-standards.md](../standards/api-standards.md) — 业务错误码

* [logging-standards.md](../standards/logging-standards.md) — 结构化日志规范

