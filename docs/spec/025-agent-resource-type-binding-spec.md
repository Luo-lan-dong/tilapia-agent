# Agent 资源按类型分发与更新地址云端下发 Spec

关联 ADR：[ADR-025](../adr/025-agent-resource-type-binding.md)
关联 Harness：[025-agent-resource-type-binding-harness.md](./025-agent-resource-type-binding-harness.md)
关联 Changes：[025-agent-resource-type-binding-changes.md](./025-agent-resource-type-binding-changes.md)

## 1. 功能描述

1. **脚本按类型绑定**：发布页上传安装脚本时选择 agent 类型；key `install-script-{type}`、path `install-scripts/{type}/...`，类型隔离
2. **按类型更新**：中枢按 agent 类型分组展示与一键应用更新（脚本随整体包自动更新）
3. **安装命令防错配**：脚本 URL 按 agent_type 拼接 + 本地清单存在性校验
4. **地址云端下发**：中枢配置文件写死服务端下发接口地址与 Token（`AGENT_RESOURCE_HUB_CONFIG_URL` / `AGENT_RESOURCE_HUB_TOKEN`，发布前手动指向生产，用户零配置零展示）；检查更新时先从服务端拉最新仓库地址（失败回退缓存）；中枢页面不再显示/配置资源仓库地址

## 2. 用户故事

| 编号 | 用户故事 | 验收要点 |
|------|---------|---------|
| US-1 | 作为运维，我给 code 类型发布新脚本时能声明类型 | 发布页选「安装脚本 + code」，key/path 按类型生成 |
| US-2 | 作为运维，我看到的是"code 有更新"而非资源条目 | 中枢资源页按类型分组，类型级更新状态 |
| US-3 | 作为运维，更新 code 时脚本自动一起更新 | 勾选 code 类型应用更新，包+脚本一并落盘 |
| US-4 | 作为运维，安装 code 节点绝不会拿到别的类型的脚本 | 安装命令 URL 按类型拼接；该类型脚本未发布时报错而非错发 |
| US-5 | 作为运维，改了桶配置后中枢无需手工改地址 | RuoYi 改桶/前缀 → 中枢下次检查自动用新地址 |
| US-6 | 作为运维，RuoYi 宕机不影响中枢正常检查 | 回退缓存地址继续检查，状态不误报 |
| US-7 | 作为运维，中枢页面看不到也不再配置资源仓库地址与管理后台地址 | 页面零配置零展示；地址/Token 发布前写在配置文件 |

## 3. 核心流程

### 3.1 检查更新（地址云端下发）

```
触发（定时 6h / 手动「立即检查」）
   ▼
读配置文件 AGENT_RESOURCE_HUB_CONFIG_URL / AGENT_RESOURCE_HUB_TOKEN
（ADR-025 修订：发布前写死，不存数据库、无页面配置，用户零配置）
   ├─ 已配置 ──► GET {hub_config_url}（header: X-Hub-Token）
   │    ├─ 成功 ──► 写入 agent_resource_manifest_url（运行期缓存）→ 用新地址拉云端清单
   │    └─ 失败 ──► 回退缓存地址（state → 数据库 → 内置默认）
   └─ 未配置 ──► 直接回退缓存地址
        ├─ 有缓存 ──► 用缓存地址继续
        └─ 无缓存 ──► 状态 disabled / failed，明确提示
   ▼
比对（现有 _diff 逻辑）→ 状态 up-to-date / update-available
```

### 3.2 按类型应用更新

```
前端勾选类型（如 code）→ POST update {type: "code"}
   ▼
后端解析该类型全部条目（agent-code-linux-x64 + install-script-code）
   ▼
下载全部条目 → sha256 校验 → 原子落盘（现有 _apply_locked）
   ▼
类型级 up-to-date
```

### 3.3 安装命令生成（防错配）

```
get_install_script(agent_type="code", os="linux")
   ├─ 白名单校验（现有）
   ├─ 本地清单查 install-script-code 存在？
   │    ├─ 缺失 ──► 明确报错「code 类型安装脚本未发布/未同步」
   │    └─ 存在 ──► script_url = {hub}/static/install-scripts/code/install-agent-code-linux.sh
```

## 4. 接口变更

### 4.1 RuoYi 新增：GET /portal/open/hub/config（官网门户公开路径，Token 认证）

请求：`GET {base}/portal/open/hub/config`，header `X-Hub-Token: {token}`

> ADR-025 修订（2026-09-09）：接口从初版 `/cloud/hub/manifest-url` 迁入官网门户公开路径
> `/portal/open/**`（nginx 仅反代该组路径，若依后端 8080 不直接暴露公网）。

```json
{ "code": 200, "data": { "manifestUrl": "https://bucket.cos.region.myqcloud.com/prefix/" } }
```

Token 错误返回 401。Token 在 COS 配置页生成/重置（`cloud_cos_config.hub_token`）。

### 4.2 中枢配置项（ADR-025 修订：配置文件写死，用户零配置）

| 配置 | 说明 |
|----|------|
| `AGENT_RESOURCE_HUB_CONFIG_URL`（配置文件） | 服务端配置下发接口地址，如 `http://192.168.101.104:8080/portal/open/hub/config`；打包发布前手动指向生产服务器 |
| `AGENT_RESOURCE_HUB_TOKEN`（配置文件） | 对接 Token（X-Hub-Token 认证，与 RuoYi COS 配置页一致） |
| `agent_resource_manifest_url`（DB system_settings，降级） | 运行期缓存，检查流程自动维护，页面不再展示 |

### 4.3 中枢状态视图（分组结构）

```json
{ "data": {
    "status": "update-available",
    "agent_types": [
      { "type": "code", "has_update": true, "selected": true,
        "resources": [ {key, local_version, cloud_version, size, present, outdated, ...} ] }
    ] } }
```

### 4.4 中枢应用更新：POST /settings/resources/update

```json
{"type": "code"}
```
（兼容：仍可传 `keys` 列表；二者都无=全部过时类型）

## 5. 降级链路

| 场景 | 行为 |
|------|------|
| 服务端下发接口未配置 | 跳过远程拉取，直接回退缓存地址（state → DB → 内置默认）；无任何缓存则 disabled/failed |
| RuoYi 不可达 | 回退缓存地址；无缓存则 failed |
| 某类型脚本未发布 | 该类型仅含包条目可更新；安装命令生成时报错 |
| key 无类型前缀（历史） | 解析失败条目归入「未分组」展示，不参与类型更新 |
| Token 轮换 | RuoYi 重置后中枢同步更新，否则持续回退缓存 |

## 6. 验收标准

1. 发布页上传脚本可选类型；key/path 正确（install-script-code）
2. 中枢页面按类型分组展示；勾选类型应用后包+脚本均落盘
3. code 脚本未同步时，安装命令接口报错而非返回其他脚本 URL
4. RuoYi 改桶前缀后，中枢「立即检查」自动用新地址（无需改中枢配置）
5. 停掉 RuoYi，中枢检查回退缓存地址正常比对（状态不为 failed）
6. 中枢页面无资源仓库地址、无管理后台地址/Token 配置展示（零配置零展示）
7. 迁移后云端清单：install-script-code + agent-code-linux-x64（旧 install-script-linux 已下架）
