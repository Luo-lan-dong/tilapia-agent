# 工具清单结构化 Harness（宝塔 MCP 目录）

> 关联：[ADR-057](../adr/057-bt-tools-inventory-compaction.md) · [Spec](./057-bt-tools-inventory-spec.md) · [Changes](./057-bt-tools-inventory-changes.md)

## 1. 测试金字塔

```
      E2E（1）           真实宝塔 MCP：98 工具全量可见 + 长度小于 8000
       集成（2）          技能调用端到端（清单 → bt_mcp_schema 恢复闭环）
      单元（7+）          首句切分 / 空描述 / 极端兜底 / 恢复指令 / Upload 例外 / 数据完整性
```

## 2. 单元测试清单（tests/skills/test_bt_mcp_skills.py）

| 编号 | 用例 | 断言要点 |
|------|------|---------|
| UT-66 | 全量覆盖 | 98 个（或 N 个）工具时，摘要包含**每一个**工具名（逐名断言） |
| UT-67 | 首句切分 | `description="读取文本文件；用于查看…"` → 只保留「读取文本文件」；无句号时按 150 字符截断并加 `…` |
| UT-68 | 空描述容错 | `description=None` / `""` → 输出 `- <name>（<risk>）`，不抛错 |
| UT-69 | 恢复指令存在 | 摘要含 `bt_mcp_schema` 与 `server_id`、`tool` 字样；含 high 风险用户确认提示 |
| UT-70 | 长度不劣化 | 紧凑化长度 < `RESULT_SUMMARY_MAX_CHARS`（8000）；且**小于**修复前的机械头截长度 |
| UT-71 | 极端兜底 | 构造超长描述（如单工具 20,000 字符）→ 摘要不超过 `RESULT_SUMMARY_MAX_CHARS` |
| UT-72 | Upload 例外保留 | `Upload` 一行仍含「中枢自动代执行」说明（回归 issues 2026-09-26） |
| UT-73 | 数据字段不变 | `SkillResult.data["tools"]` 仍含全部工具的 `name/description/category/risk` |

## 3. 集成测试

| 编号 | 用例 | 方式 |
|------|------|------|
| IT-13 | 清单 → 深读闭环 | mock `list_tools` 返回多工具 → 调 `bt_mcp_tools` 得清单 → 对清单中**末尾**的工具调 `bt_mcp_schema` → 断言返回完整 description + inputSchema |
| IT-14 | 失败路径不变 | `list_tools` 抛 `BtMcpError` → 返回失败摘要（与修复前一致，不回归） |

## 4. E2E

| 编号 | 场景 | 验证 |
|------|------|------|
| E2E-4 | 真实宝塔 MCP（192.168.101.13，98 工具） | 调用 `bt_mcp_tools` 等价逻辑：**98 个工具名全部出现**；摘要长度 < 8,000 字符（实测目标 ~2.5k）；恢复指令存在；对 1 个工具调 `bt_mcp_schema` 能取回完整说明 |

## 5. CI 门禁

- `pytest tests/skills/test_bt_mcp_skills.py tests/skills/test_bt_call_guard.py` 全绿；
- **守卫用例**：UT-66（全量覆盖）必须存在——它是本 ADR 的核心验收，防「又回到截断」；
- **守卫用例**：UT-70 必须断言长度**不劣化**（更短）——防「为了全量而变成更贵」；
- **守卫用例**：UT-72 必须存在——防改动回退掉 ADR-049 的 Upload 例外修复；
- ruff format/check 全过；前端零改动。
