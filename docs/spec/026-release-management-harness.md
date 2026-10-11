# 版本发布管理与中枢更新 Harness

关联 ADR：[ADR-026](../adr/026-hub-self-update.md)
关联 Spec：[026-release-management-spec.md](./026-release-management-spec.md)

## 1. 测试金字塔

```
      E2E（手动）
     ┌───────────────────────────────────────────────┐
     │ 后台发版→中枢日志分组可见→检查更新→下载链接可达     │
     └───────────────────────────────────────────────┘
    集成测试（pytest，mock HTTP）
   ┌───────────────────────────────────────────────┐
   │ releases 拉取分组 / 检查更新三态 / RuoYi 不可达降级 │
   └───────────────────────────────────────────────┘
  单元测试（pytest / Java 单测）
 ┌─────────────────────────────────────────────────┐
 │ 版本比较 / 分组组装 / product+version 唯一校验      │
 └─────────────────────────────────────────────────┘
```

## 2. 单元测试清单（中枢 pytest）

| 编号 | 测试项 | 文件 | 断言要点 |
|------|--------|------|---------|
| UT-1 | 版本比较 | `tests/services/test_release_service.py` | `v0.6.0 > v0.5.0`；`v0.5.0 == v0.5.0`；非法版本回退手写比较 |
| UT-2 | 分组组装 | 同上 | releases 按 product 分组；label 映射 hub→中枢、agent-code→Code Agent；release_date 倒序 |
| UT-3 | 检查更新三态 | 同上 | update-available（含 title/content/downloadUrl）；up-to-date；failed（无 hub 记录 / 拉取失败） |
| UT-4 | 配置解析 | 同上 | hub_config_url 去尾拼接 `/portal/open/hub/releases`；Token 透传 header |

## 3. 集成测试清单

| 编号 | 测试项 | 断言要点 |
|------|--------|---------|
| IT-1 | `GET /settings/releases` | mock RuoYi 200 → products 分组结构；mock 超时 → error 字段 + 200 响应（不 5xx） |
| IT-2 | `POST /settings/check-hub-update` | 三态各自正确；云端 401 → failed |
| IT-3 | 本地当前版本 | `GET /settings/versions` 返回 v0.5.0（is_current=true）唯一记录 |
| IT-4 | RuoYi CRUD 唯一约束 | Java：重复 product+version 新增被拒 |
| IT-5 | Token 查询接口 | Java：无/错 Token 401；正确 Token 返回 releases 数组 |

## 4. E2E 清单（手动）

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 发布→展示 | RuoYi 后台新增 hub v0.6.0 与 agent-code 0.5.1 日志 → 中枢刷新日志页 | 两个 Tab 各自出现新版本，日期倒序 |
| E2E-2 | 检查更新 | 发布 hub v0.6.0（>v0.5.0）→ 中枢点「检查中枢更新」 | 提示有新版，Modal 展示日志，下载链接可访问 |
| E2E-3 | 已是最新 | 不发新版再检查 | 提示「当前已是最新版本」 |
| E2E-4 | RuoYi 宕机 | 停 RuoYi → 刷新日志页 + 检查更新 | 日志页错误提示可重试；检查返回失败提示；侧边栏 v0.5.0 不变 |
| E2E-5 | 安装包上传 | hub 新版上传 exe → COS | `hub-releases/{version}/` 对象存在，URL 公有可下载 |

## 5. 门禁

| 门禁 | 要求 |
|------|------|
| 中枢 | 新增 pytest 全过 + 既有 settings/resource 测试回归全过 |
| RuoYi | `mvn compile` 通过；前端 `npm run build:prod` 通过 |
| 手动 | E2E-1~4 通过后发布 |
