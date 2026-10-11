# 工具清单结构化 Spec（宝塔 MCP 目录）

> 关联：[ADR-057](../adr/057-bt-tools-inventory-compaction.md) · [Harness](./057-bt-tools-inventory-harness.md) · [Changes](./057-bt-tools-inventory-changes.md)
> 上游：[ADR-049](../adr/049-baota-mcp-deployment.md)（工具代理模式）· [ADR-050](../adr/050-bt-tool-governance-experience-memory.md)（工具治理）

## 1. 功能描述

把 `bt_mcp_tools` 返回给模型的**工具目录**从「机械头截 8000 字符」改为
「**紧凑全量清单 + 显式恢复指令**」，解决两个问题：

1. **覆盖**：98 个工具的**名字全部可见**（现状只到第 60 行，65 个工具名消失）；
2. **可恢复**：模型知道「要完整说明与入参怎么取」，不再靠猜。

## 2. 用户故事

- **US-1（选对工具）**：作为用户，AI 不该因为看不到 `SystemInfo`/`BashStatus`/`ContainerList`
  而在部署任务里漏掉环境侦察与后台任务管理。
- **US-2（别猜参数）**：作为用户，AI 不该对着半句话的描述猜入参；它应知道去哪儿取完整 schema。
- **US-3（别更贵）**：作为运维，修完之后这次调用的 token 消耗**不应上升**（实测紧凑化后更短）。
- **US-4（能力不变）**：`bt_mcp_schema` / `bt_mcp_call` / `bt_deploy_guide` 的行为与返回结构不变。

## 3. 核心流程

```
bt_mcp_tools(server_id)
  │
  ├─ _SERVICE.list_tools(session, server)        ← 实时拉取（含 name/description/risk）
  │
  ├─ 逐工具渲染单行： "- <name>（<risk>）：<首句≤50 字>"
  │     · 首句 = description 在第一个「。；\n」处切开后的内容
  │     · 上限 50 由算术保证：98 × 81 = 7,938 < 8,000（最坏情况也不触发兜底）
  │     · Upload 追加中枢环境例外说明（issues 2026-09-26 既有修复，保留）
  │
  ├─ 头部："服务器「<名称>」共 N 个工具（下列为全部工具的一句话用途，完整说明与入参按需查询）："
  ├─ 尾部："要某个工具的完整说明与入参 Schema，调用 bt_mcp_schema(server_id, tool)；"
  │        "high 风险工具服务端要求用户确认。"
  │
  └─ 极端兜底：summary[:RESULT_SUMMARY_MAX_CHARS]（8000）
        · 紧凑化后实测约 4,145 字符；最坏情况 7,938 字符，均不触及
```

## 4. 输出样例（真实形态）

```
服务器「宝塔服务器-192.168.101.13」共 98 个工具（仅给一句话用途，完整说明按需查询）：
- Read（low）：读取文本文件
- Write（medium）：写入文本文件
- Bash（medium）：执行 Shell 命令
- BashStatus（low）：查询后台任务的状态、输出与结果
- SystemInfo（low）：获取服务器系统资源概况
…
【要某个工具的完整说明与入参 Schema，调用 bt_mcp_schema(server_id=<id>, tool=<工具名>)。】
【high 风险工具（如删除数据库/项目）服务端要求用户确认后再执行。】
```

## 5. 降级链路

| 异常场景 | 行为 |
|---------|------|
| `description` 为空 / None | 该行省略描述部分，只输出 `- <name>（<risk>）`，不抛错 |
| `description` 无句号分号换行 | 按 150 字符截断，末尾加 `…` |
| 风险标签为 `unknown` 等非标准值 | 原样输出（与 `list_tools` 返回一致），不臆造等级 |
| 工具名列表异常膨胀（>8000 字符） | 触发 `RESULT_SUMMARY_MAX_CHARS` 兜底截断（极端场景，正常不会） |
| `list_tools` 失败（网络/证书/白名单） | 与现状一致：返回失败摘要，不改动 |

## 6. 验收标准

- [ ] 98 工具场景下，**全部 98 个工具名**都出现在摘要中；
- [ ] 摘要尾部含明确的 `bt_mcp_schema` 恢复指令与 high 风险确认提示；
- [ ] 摘要长度 **小于修复前的 8,000 字符**（实测紧凑化约 2,467 字符）；
- [ ] 首句截断规则：无句号时按 150 字符截断并加 `…`；`description` 为空时不报错；
- [ ] `Upload` 的空间例外说明仍然存在（回归 ADR-049 的既有修复）；
- [ ] `bt_mcp_schema` 仍返回完整 description + schema（深读路径不受影响）；
- [ ] `SkillResult.data.tools` 结构不变（前端与后续技能兼容）；
- [ ] 非宝塔技能与 `bt_mcp_call` 行为零变化。

## 7. 关联文档

- [ADR-057](../adr/057-bt-tools-inventory-compaction.md)
- [057-bt-tools-inventory-harness.md](./057-bt-tools-inventory-harness.md)
- [057-bt-tools-inventory-changes.md](./057-bt-tools-inventory-changes.md)
