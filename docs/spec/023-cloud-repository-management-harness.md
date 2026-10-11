# 云端资源仓库管理 Harness（Tilapia-web × 腾讯云 COS）

关联 ADR：[ADR-023](../adr/023-cloud-repository-management.md)
关联 Spec：[023-cloud-repository-management-spec.md](./023-cloud-repository-management-spec.md)

## 1. 测试金字塔

```
        E2E（手动，真实 COS + 真实中枢）
       ┌─────────────────────────────────────┐
       │ 配置→发布→中枢检查→应用更新→Agent 安装 │
       └─────────────────────────────────────┘
      集成测试（JUnit，COS SDK 桩/Mockito）
     ┌───────────────────────────────────────┐
     │ 发布编排/manifest 更新/配置服务/降级报错  │
     └───────────────────────────────────────┘
    单元测试（JUnit）
   ┌─────────────────────────────────────────┐
   │ path 生成规则/sha256 计算/revision 格式    │
   │ manifest 序列化（契约：符合 hub 解析规范）  │
   └─────────────────────────────────────────┘
```

> 说明：若依 RuoYi-Vue 项目无既有 CI 门禁体系，本模块测试以 `mvn test` 手动/本地门禁为主；前端 `npm run build` 编译门禁。发布产物正确性另以「验收清单命令」形式供运维自测（见 §5）。

## 2. 单元测试清单

| 编号 | 测试项 | 类 | 断言要点 |
|------|--------|-----|---------|
| UT-1 | path 生成规则 | `CloudResourcePathTest` | 三种 key × 版本生成的 path 与 repository-api 目录结构一致；install-script 固定路径；path 无 `..`、无反斜杠、无绝对路径 |
| UT-2 | sha256 流式计算 | `Sha256StreamTest` | 与已知摘要文件比对（64 位小写十六进制）；空文件摘要正确 |
| UT-3 | revision 格式 | `RevisionTest` | ISO 8601 UTC（`yyyy-MM-dd'T'HH:mm:ss'Z'）；每次生成单调不减 |
| UT-4 | manifest 序列化契约 | `ManifestSerializeTest` | 字段名/结构完全符合 repository-api §3（revision/resources/key/path/sha256/size/version）；序列化字节 UTF-8 无 BOM；JSON 合法可被 hub 侧 ResourceSyncService 的解析规则接受（缺字段抛 InvalidManifest） |
| UT-5 | manifest 更新逻辑 | `ManifestUpdateTest` | key 命中覆盖（不重复追加）、未命中追加、resources 顺序保持、空清单初始化 `{"revision":…,"resources":[]}` |
| UT-6 | SecretKey 脱敏 | `SecretMaskTest` | 返回 `****` + 尾 4 位；保存请求中脱敏占位值不覆盖原密钥 |

## 3. 集成测试清单（COS 访问全部 Mock）

| 编号 | 测试项 | 类 | 断言要点 |
|------|--------|-----|---------|
| IT-1 | 配置单行 upsert | `CloudCosConfigServiceTest` | 首次 insert，再次 update；查无配置返回 null |
| IT-2 | 发布编排成功 | `CloudResourcePublishServiceTest` | 依序调用：算 sha256 → 上传资源 → 拉 manifest → 更新条目+revision → 传 manifest；返回结果字段完整 |
| IT-3 | COS 上无 manifest 时首发布 | `CloudResourcePublishServiceTest` | 拉取 404 → 创建空清单 → 追加条目后上传 |
| IT-4 | 资源上传失败降级 | `CloudResourcePublishServiceTest` | 上传抛错 → 发布失败报错，manifest 上传未被调用（无半发布） |
| IT-5 | manifest 上传失败 | `CloudResourcePublishServiceTest` | 报错且提示可重发；资源已传但清单未更新（中枢不可见） |
| IT-6 | 未配置时发布/清单报错 | `CloudResourcePublishServiceTest` | 抛业务异常，提示「请先完成 COS 配置」 |
| IT-7 | 测试连接 | `CloudCosConfigServiceTest` | headBucket 异常分类：凭证错误 / 桶不存在 / 网络不可达 |
| IT-8 | 重复发布同 key 同版本 | `CloudResourcePublishServiceTest` | 条目数不增（覆盖），revision 变化 |
| IT-9 | 接口鉴权 | `CloudControllerAopTest`（或随框架既有鉴权测试） | `@RequiresPermissions("cloud:resource:publish")` 生效，无权限返回 403 |
| IT-10 | 删除条目（下架） | `CloudResourcePublishServiceTest` | 清单条目移除 + revision bump + 可选 COS 对象删除；重复删除报「清单中不存在资源条目」；删除的 key 不受白名单限制（旧形态 key 可删） |

## 4. E2E 测试清单（发版前手动，真实环境）

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 全链路：配置→发布→中枢消费 | 管理后台配置真实 COS → 发布 agent-template-programming 0.4.4 → 复制 manifest_url 到 TilapiaHub → 中枢「立即检查」→ 应用更新 | 中枢状态 up-to-date；`/static/agent-templates/...` 服务新内容；sha256 校验通过 |
| E2E-2 | 热更新闭环 | 更新模板到 0.4.5（后台再发布）→ 中枢手动「立即检查」 | update-available 提示；应用后新节点安装使用 0.4.5 |
| E2E-3 | 大文件发布 | 上传 ~130MB tilacode 二进制发布 | 发布成功；后端内存无 OOM；中枢可下载校验 |
| E2E-4 | 产物核验（repository-api 验收清单） | `curl -s {ROOT}manifest.json`；`curl -sI {ROOT}{path}`；`sha256sum` | JSON 合法无 BOM；每 path 200 且 Content-Length=size；sha256 一致 |
| E2E-5 | 安全 | 未登录访问 /cloud/**；普通用户（无 cloud:* 权限）访问发布接口 | 均被若依鉴权拦截 |
| E2E-6 | Agent 安装回归 | 中枢配置 COS 仓库后，Linux 机器执行一行安装命令 | 安装成功，下载走中枢 `/static`（协议不变） |

## 5. 运维自测命令（发布产物验收）

```bash
ROOT="https://{bucket}.cos.{region}.myqcloud.com/agent/"
curl -s ${ROOT}manifest.json | python -m json.tool          # 合法 JSON
for p in $(curl -s ${ROOT}manifest.json | jq -r '.resources[].path'); do
  curl -sI ${ROOT}${p} | head -1                            # 200
done
sha256sum <local-file>                                      # 与 manifest 比对
```

## 6. 门禁

| 门禁 | 要求 |
|------|------|
| 后端 | `mvn test` 通过（UT-1~6、IT-1~9）；ruff/spotless 无既有告警新增 |
| 前端 | `npm run build:prod` 编译通过；无 ESLint error |
| 文件长度 | 新增 Java 类 ≤300 行（超出拆 Service） |
| 手动门禁 | 发版前 E2E-1~6 通过（尤其产物核验 E2E-4） |
