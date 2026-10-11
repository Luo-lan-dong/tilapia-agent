# Agent 资源云端分发与热更新 Harness

关联 ADR：[ADR-022](../adr/022-agent-resource-cloud-distribution.md)
关联 Spec：[022-agent-resource-cloud-distribution-spec.md](./022-agent-resource-cloud-distribution-spec.md)

## 1. 测试金字塔

```
      E2E（手动）
     ┌──────────────────────────┐
     │ 云端仓库搭建→同步→Agent 安装 │
     └──────────────────────────┘
    集成测试（pytest，本地 HTTP 假云端）
   ┌────────────────────────────────┐
   │ 同步全流程/降级链路/挂载选择/zip 导入│
   └────────────────────────────────┘
  单元测试（pytest）
 ┌──────────────────────────────────────┐
 │ manifest 解析/sha256/原子落盘/revision 比对│
 └──────────────────────────────────────┘
```

## 2. 单元测试清单（CI 门禁）

| 编号 | 测试项 | 文件 | 断言要点 |
|------|--------|------|---------|
| UT-1 | 云端 manifest 解析与字段校验 | `tests/services/test_resource_sync.py` | 缺 revision/resources 字段抛 InvalidManifest |
| UT-2 | sha256 校验 | `tests/services/test_resource_sync.py` | 匹配通过；不匹配抛 ShaMismatch 且不落盘 |
| UT-3 | 原子落盘 | `tests/services/test_resource_sync.py` | tmp→rename；中途失败不污染目标路径 |
| UT-4 | revision 比对 | `tests/services/test_resource_sync.py` | 相同跳过下载；不同触发全量同步 |
| UT-5 | 本地 manifest 读写往返 | `tests/services/test_resource_sync.py` | 落盘后读取字段一致；损坏时按无缓存处理 |

## 3. 集成测试清单（CI 门禁）

| 编号 | 测试项 | 文件 | 断言要点 |
|------|--------|------|---------|
| IT-1 | 检查发现新版本（只提示不下载） | `tests/services/test_resource_check.py` | revision 差异 → 状态 update-available、差异列表正确、缓存无任何变化 |
| IT-2 | 云端不可达降级 | `tests/services/test_resource_check.py` | 状态 offline，旧缓存完整保留 |
| IT-3 | 应用更新成功 | `tests/services/test_resource_apply.py` | POST update 后缓存文件与 manifest 一致、状态 up-to-date |
| IT-4 | 应用更新 sha256 不匹配整体放弃 | `tests/services/test_resource_apply.py` | 状态 failed，无半更新（旧 manifest 与文件未变） |
| IT-5 | 检查 revision 相同 | `tests/services/test_resource_check.py` | 状态 up-to-date，无下载请求 |
| IT-6 | URL 未配置 disabled | `tests/services/test_resource_check.py` | 状态 disabled，无网络请求 |
| IT-7 | zip 导入合法包 | `tests/api/test_resource_import.py` | 200，缓存落盘，/static 服务新内容 |
| IT-8 | zip 导入非法包（缺资源/校验错） | `tests/api/test_resource_import.py` | 400 + 明确错误，缓存未变 |
| IT-9 | /static 挂载选择 | `tests/api/test_static_mount.py` | 有缓存用缓存；无缓存回退 resource_root/static；都无则 404 |
| IT-10 | manifest URL 热更 | `tests/api/test_resource_settings.py` | PUT 后 system_settings 持久化且触发检查 |
| IT-11 | 应用更新并发互斥 | `tests/services/test_resource_apply.py` | 更新中再次触发被跳过 |
| IT-12 | 第三方路径扫描（宝塔/pyenv） | `tests/services/test_third_party_python.py` | /www/server/pyenv/versions 结构可检出 python3.11（安装脚本回归） |

## 4. E2E 测试清单（发版前手动）

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 云端发布→自动服务 | 搭建云端仓库 → 中枢配置 URL → 同步 → Agent 节点执行安装命令 | Agent 安装成功且使用云端版本 |
| E2E-2 | 热更新 | 更新云端模板 revision → 设置页手动同步 → 新节点安装 | 新节点使用新版本，无需重装中枢 |
| E2E-3 | 离线导入 | 断网环境 → 导入 zip → Agent 安装 | 安装成功 |
| E2E-4 | 安装包轻量化 | 检查安装包内容 | 无 static 资源，体积 ~60MB 级 |

## 5. CI 门禁

| 门禁 | 要求 |
|------|------|
| lint | ruff format + check 通过 |
| test | 全量测试（含 UT-1~5、IT-1~10）通过 |
| 文件长度 | 新增文件 ≤300 行 |
| 手动门禁 | 发版前 E2E-1~4 通过 |
