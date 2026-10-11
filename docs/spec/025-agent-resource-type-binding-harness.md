# Agent 资源按类型分发 Harness

关联 ADR：[ADR-025](../adr/025-agent-resource-type-binding.md)
关联 Spec：[025-agent-resource-type-binding-spec.md](./025-agent-resource-type-binding-spec.md)

## 1. 测试金字塔

```
      E2E（手动）
     ┌─────────────────────────────────────────┐
     │ 改桶前缀→中枢自动跟随 / 类型一键更新 / 防错配 │
     └─────────────────────────────────────────┘
    集成测试（pytest，mock HTTP）
   ┌───────────────────────────────────────────┐
   │ 地址拉取回退 / 按类型应用 / 安装命令校验        │
   └───────────────────────────────────────────┘
  单元测试（pytest）
 ┌─────────────────────────────────────────────┐
 │ key→(kind,type) 解析 / 分组视图组装 / Token 校验 │
 └─────────────────────────────────────────────┘
```

## 2. 单元测试清单

| 编号 | 测试项 | 文件 | 断言要点 |
|------|--------|------|---------|
| UT-1 | key 解析 | `tests/services/test_resource_type_grouping.py` | `agent-code-linux-x64`→(package,code)；`install-script-code`→(script,code)；无前缀/空→未分组 |
| UT-2 | 分组视图 | 同上 | 同类型包+脚本聚合到同一 type；has_update=任一条目过时；未分组条目单独组 |
| UT-3 | RuoYi 地址拉取 | `tests/services/test_resource_type_grouping.py`（resolve_manifest_url 系列） | 成功写缓存+更新 manifest_url；失败回退 state 缓存；无缓存返回失败 |
| UT-4 | 按类型条目展开 | `tests/services/test_resource_type_grouping.py`（keys_for_type 系列） | type=code 展开为该类型全部 key；未知 type 报错 |
| UT-5 | 安装命令校验 | `tests/api/v1/test_agent_nodes.py`（install-scripts 端点系列） | 清单有脚本→URL 按类型拼接；无脚本→报错（不回退其他类型） |
| UT-6 | RuoYi Token 校验 | RuoYi 侧（Java） | Token 正确返回 manifestUrl；错误 401 |

## 3. 集成测试清单

| 编号 | 测试项 | 断言要点 |
|------|--------|---------|
| IT-1 | 地址变更跟随 | mock RuoYi 返回新地址 → check 用新地址拉云端清单（manifest_url 已更新） |
| IT-2 | RuoYi 宕机回退 | mock RuoYi 超时 → 用 state 缓存地址完成检查，状态 up-to-date/update-available（非 failed） |
| IT-3 | 按类型应用更新 | update type=code → 该类型包+脚本均下载落盘；其他类型条目不受影响 |
| IT-4 | 兼容 keys 参数 | update keys=[...] 仍正常（老语义保留） |
| IT-5 | RuoYi 发布脚本选类型 | 发布 install-script-code → 清单条目 path 为 install-scripts/code/... |

## 4. E2E 清单（手动）

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 地址自动跟随 | RuoYi 改桶前缀 → 中枢「立即检查」 | 中枢用新地址检查成功；页面无地址展示 |
| E2E-2 | 类型一键更新 | 发布新版 code 包 → 中枢按类型应用 | 包+脚本一起更新落盘 |
| E2E-3 | 防错配 | 删除 code 脚本条目 → 请求安装命令 | 明确报错，不返回其他类型脚本 |
| E2E-4 | RuoYi 宕机 | 停 RuoYi → 中枢检查 | 回退缓存地址，检查正常 |
| E2E-5 | 迁移完整 | 旧 install-script-linux 下架 + install-script-code 发布 → 节点安装 | 安装命令 URL 为 install-scripts/code/...，安装成功 |

## 5. 门禁

| 门禁 | 要求 |
|------|------|
| 中枢 | 新增单测全过 + 既有资源同步测试全过（回归） |
| RuoYi | `mvn compile` 通过；前端 `npm run build:prod` 通过 |
| 手动 | E2E-1~3 通过后发布 |
