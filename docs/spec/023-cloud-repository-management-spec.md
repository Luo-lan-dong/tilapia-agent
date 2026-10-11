# 云端资源仓库管理 Spec（Tilapia-web × 腾讯云 COS）

关联 ADR：[ADR-023](../adr/023-cloud-repository-management.md)
关联 Harness：[023-cloud-repository-management-harness.md](./023-cloud-repository-management-harness.md)
关联 Changes：[023-cloud-repository-management-changes.md](./023-cloud-repository-management-changes.md)
实现依据：[022-agent-resource-cloud-repository-api.md](./022-agent-resource-cloud-repository-api.md)（云端仓库接口规范）

## 1. 功能描述

在 Tilapia-web 管理后台（若依 RuoYi-Vue3 前端 + RuoYi-Vue 后端）实现 ADR-022 云端资源仓库的**发布端**：

1. **COS 连接配置**：SecretId/SecretKey/Bucket/Region/路径前缀存 MySQL，管理后台随时修改，支持测试连接
2. **资源发布**：页面上传资源文件（安装脚本/Agent 模板/tilacode 二进制），后端自动计算 sha256/size、按规范生成 path、上传 COS、更新 manifest.json（revision 自动 bump）
3. **清单查看**：展示 COS 上当前 manifest.json 的全部条目（key/版本/大小/sha256/revision/发布时间）
4. **中枢接入引导**：展示完整 manifest_url 供复制到 TilapiaHub 系统设置

不在本模块范围：中枢侧同步消费（ADR-022 已实现）、桶内旧版本文件清理（后续迭代）、STS 前端直传（后续迭代）。

## 2. 用户故事

| 编号 | 用户故事 | 验收要点 |
|------|---------|---------|
| US-1 | 作为管理员，我在后台配置腾讯云 COS 的密钥与桶信息，不需要登录腾讯云控制台 | 配置保存到 `cloud_cos_config` 表；「测试连接」返回成功/失败与明确原因 |
| US-2 | 作为管理员，我修改配置后旧配置立即失效，新配置立即生效 | 每次操作实时读库构建 COS 客户端，无进程级缓存凭证 |
| US-3 | 作为管理员，我上传新版 Agent 模板，系统自动算好 sha256/size 并更新 manifest | 发布后 `curl {ROOT}manifest.json` 的条目与 revision 已更新；sha256 与本地文件一致 |
| US-4 | 作为管理员，我能看到当前云端清单里每个资源的版本、大小、发布时间 | 清单页数据来自 COS 上的 manifest.json 实时解析 |
| US-5 | 作为管理员，发布失败时我要明确的错误提示，且不能产生「半发布」状态 | 中途失败明确报错；重发覆盖同 path，幂等可恢复 |
| US-6 | 作为运维，我复制资源发布页展示的 manifest_url 配到中枢，中枢就能检查发现新版本 | URL 形态 `https://{bucket}.cos.{region}.myqcloud.com/{prefix}/`，匿名 GET 可达 |
| US-7 | 作为管理员，密钥不能泄露给前端 | SecretKey 返回时脱敏为 `****`（仅尾 4 位）；前端不直连 COS |

## 3. 核心流程

### 3.1 COS 配置流程

```
管理后台「云端资源 → COS 配置」
   │ 填写 SecretId/SecretKey/Bucket/Region/路径前缀
   ▼
保存（PUT /cloud/cos/config）──── 单行 upsert 到 cloud_cos_config
   │
   ▼
「测试连接」（POST /cloud/cos/config/test）──── headBucket 校验
   ├─ 成功 → "连接成功"（返回桶地域一致性校验结果）
   └─ 失败 → 明确错误（凭证无效 / 桶不存在 / 网络不可达）
```

### 3.2 资源发布流程（核心）

```
「云端资源 → 资源发布」→ 上传发布
   │ 选择资源 key（三选一）+ 填版本号（模板/二进制必填）+ 选择文件
   ▼
POST /cloud/resource/publish（multipart）
   │
   ├─ COS 未配置 ──► 500 "请先在 COS 配置页完成配置"
   ▼
后端流式处理：临时文件接收 → 计算 sha256 + size
   ▼
生成目标 path（遵循 repository-api 目录结构）：
   install-script-linux        → install-scripts/install-agent-programming-linux.sh
   agent-template-programming  → agent-templates/programming/{ver}/opc-agent-programming-{ver}.tar.gz
   tilacode-linux-x64          → binaries/tilacode/{ver}/tilacode-linux-x64
   ▼
上传资源文件到 COS {prefix}{path}（覆盖同 path，幂等）
   ├─ 失败 ──► 明确报错，manifest 未动（可重发）
   ▼
拉取 COS 上 manifest.json ── 不存在 ──► 创建空清单 {"revision":"…","resources":[]}
   ▼
更新条目（key 命中则覆盖，未命中则追加）+ revision = 当前 UTC ISO 时间戳
   ▼
上传新 manifest.json 到 COS {prefix}manifest.json
   ├─ 失败 ──► 报错提示重发（文件已在桶，重发覆盖即可恢复）
   ▼
返回发布结果（path/sha256/size/version/revision/manifest_url）
```

### 3.3 中枢接入（对接事实，非本模块实现）

```
资源发布页展示 manifest_url ──复制──► TilapiaHub 系统设置「资源管理」
   ▼
中枢启动 + 定时(6h 可配) + 手动「立即检查」拉取 {ROOT}manifest.json
   ▼
revision 变化 → update-available 提示 → 用户确认「应用更新」→ 下载校验落盘
```

## 4. 降级链路

| 场景 | 行为 |
|------|------|
| COS 未配置 | 配置页正常打开；发布/清单/测试接口明确报错引导配置 |
| 测试连接失败 | 返回具体原因（凭证/桶/网络），不写库副作用 |
| 上传资源文件失败 | 报错终止，manifest.json 未变（中枢不受影响，仍用旧版） |
| manifest.json 上传失败 | 报错提示重发；资源文件已在桶但清单未更新 → 中枢不会发现（无半生效），重发覆盖恢复 |
| COS 上的 manifest.json 损坏/不存在 | 按空清单处理，发布时自动重建 |
| SecretKey 泄露风险 | 返回脱敏；数据库受控网络；接口挂权限注解 |

## 5. 接口定义

若依风格（`/cloud` 前缀，ResponseEntity 走框架统一 AjaxResult）。

### 5.1 GET /cloud/cos/config

```json
{ "code": 200, "data": {
    "secretId": "AKIDxxxxxxxx", "secretKey": "****abcd",
    "bucketName": "tilapia-agent", "region": "ap-guangzhou", "basePrefix": "agent/" } }
```
未配置时 `data` 为 null（前端显示空表单）。

### 5.2 PUT /cloud/cos/config

```json
{ "secretId": "…", "secretKey": "…", "bucketName": "…", "region": "…", "basePrefix": "agent/" }
```
单行 upsert。secretKey 传 `****` 前缀值（未修改）时保留原值。

### 5.3 POST /cloud/cos/config/test

按当前库内配置（或请求体临时配置）headBucket，返回成功/失败原因。

### 5.4 GET /cloud/resource/manifest

实时拉取并解析 COS 上 manifest.json：

```json
{ "code": 200, "data": {
    "manifestUrl": "https://…/agent/",
    "revision": "2026-09-06T12:00:00Z",
    "resources": [
      { "key": "agent-template-programming", "path": "agent-templates/programming/0.4.4/…tar.gz",
        "version": "0.4.4", "size": 1234567, "sha256": "…64 位小写…" } ] } }
```

### 5.5 POST /cloud/resource/publish（multipart/form-data）

| 参数 | 说明 |
|------|------|
| key | `install-script-linux` / `agent-template-programming` / `tilacode-linux-x64` |
| version | 语义版本（模板/二进制必填；安装脚本可空） |
| file | 资源文件 |

返回：发布结果（path/sha256/size/version/新 revision/manifest_url）。

### 权限标识

| 功能 | 权限 |
|------|------|
| COS 配置查看/保存/测试 | `cloud:cos:config` |
| 清单查看 | `cloud:resource:list` |
| 上传发布 | `cloud:resource:publish` |

## 6. 数据库设计

```sql
create table cloud_cos_config (
  config_id    bigint(20)   not null auto_increment comment '主键（单行）',
  secret_id    varchar(255) not null comment 'COS SecretId',
  secret_key   varchar(255) not null comment 'COS SecretKey（明文，接口返回脱敏）',
  bucket_name  varchar(128) not null comment '存储桶名',
  region       varchar(64)  not null comment '地域，如 ap-guangzhou',
  base_prefix  varchar(255) default 'agent/' comment '仓库根前缀（{ROOT} = 桶域名 + 前缀）',
  remark       varchar(500) default null,
  create_by    varchar(64)  default '',
  create_time  datetime,
  update_by    varchar(64)  default '',
  update_time  datetime,
  primary key (config_id)
) engine=innodb comment='云端资源仓库 COS 连接配置（单行）';
```

菜单 SQL：一级目录「云端资源」+ 二级菜单「COS 配置」「资源发布」+ 按钮权限。

## 7. 后端模块结构（若依体系）

```
ruoyi-admin（或新模块 ruoyi-cloud，MVP 放 ruoyi-system）
├── controller/cloud/CloudCosConfigController.java
├── controller/cloud/CloudResourceController.java
├── service/cloud/CloudCosConfigService.java          ← 配置读写 + COS 客户端构建
├── service/cloud/CloudResourcePublishService.java    ← 发布编排（sha256/path/manifest）
├── domain/cloud/CloudCosConfig.java                  ← 配置实体
├── domain/cloud/ResourceEntry / PublishResult / …   ← manifest 条目与结果 DTO
└── mapper/cloud/CloudCosConfigMapper.xml
```

- COS SDK：`cos_api`（腾讯云官方 Java SDK），流式 putObject
- multipart 上限：`spring.servlet.multipart.max-file-size=400MB`、`max-request-size=420MB`

## 8. 前端页面（RuoYi-Vue3）

### 8.1 云端资源 → COS 配置（/cloud/cos）

- 表单：SecretId / SecretKey（密码框）/ Bucket / Region / 路径前缀
- 操作：「保存」「测试连接」（结果 message 展示具体原因）

### 8.2 云端资源 → 资源发布（/cloud/repository）

- 顶部：manifest_url 展示 + 一键复制（引导配置到中枢）；当前 revision
- 清单表格：key / 版本 / 大小 / sha256（截断+复制）/ path / 发布时间（revision）
- 「上传发布」对话框：资源 key 下拉（三种）→ 版本号输入（按 key 动态必填）→ 文件选择 → 提交（进度 loading）
- 发布成功：message 展示新 revision，清单自动刷新

## 9. 验收标准

1. 配置保存后「测试连接」对真实桶返回成功；错误凭证返回明确失败原因
2. 发布 Agent 模板 0.4.4 后：`curl {ROOT}manifest.json` 返回合法 JSON（UTF-8 无 BOM），条目 key/path/sha256/size/version 与本地文件实算一致，revision 为新 UTC 时间戳
3. sha256 复核：`sha256sum` 本地文件 == manifest 中值（64 位小写）
4. 重复发布同 key 同版本：manifest 条目覆盖（不重复追加），revision 更新，桶内文件覆盖
5. 中枢配置该 manifest_url 后：检查状态变 update-available；应用更新后 `/static` 服务新内容（E2E 联调）
6. SecretKey 在 GET /cloud/cos/config 响应中脱敏；未登录/无权限访问接口被若依鉴权拦截
7. COS 未配置时发布/清单接口返回明确错误提示
8. 上传 ~130MB 二进制发布成功（后端不 OOM）
