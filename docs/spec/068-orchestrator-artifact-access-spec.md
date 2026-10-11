# 068 编排 AI 产物感知与文件访问 Spec

> 关联 ADR：[ADR-068](../adr/068-orchestrator-artifact-access.md) ｜ Harness：[068-orchestrator-artifact-access-harness.md](./068-orchestrator-artifact-access-harness.md) ｜ Changes：[068-orchestrator-artifact-access-changes.md](./068-orchestrator-artifact-access-changes.md)

## 1. 功能描述

编排 AI（Orchestrator）在部署/验收/汇报场景下，能自主完成「找到产物 → 看懂产物 → 传上服务器」：

- **产物感知**：知道当前会话关联任务的产物是否存在、长什么样（清单/大小）；
- **文件读取**：像编程 Agent 一样读文本文件（产物代码、配置、用户上传的文档）；
- **上传打通**：把产物压缩包经宝塔 Upload 直接传到目标服务器（中枢代执行本机 curl）；
- **打包兜底**：export.zip 缺失时用本地已同步文件树打包（含不完整性警告）。

## 2. 用户故事

| # | 故事 | 验收 |
|---|---|---|
| US-1 | 用户说「部署」，AI 先自己确认产物在不在，而不是反问用户 | AI 调 `list_artifacts` 拿到 export.zip 与文件树摘要 |
| US-2 | AI 部署前想判断项目类型，读产物里的 `requirements.txt`/`package.json` | `read_file` 返回文本内容 |
| US-3 | 用户上传过需求文档，AI 部署时想回看 | `read_file` 按附件名读会话附件目录 |
| US-4 | AI 把 export.zip 传到宝塔服务器 | `bt_mcp_call(Upload, file_name="export.zip")` → 中枢代执行 → hub_note 返回服务器路径 |
| US-5 | 全量同步失败且节点离线，AI 仍能给出可部署的包 | `list_artifacts(build_zip=true)` 本地打包 + 不完整性警告 |

## 3. 核心流程

### 3.1 部署场景产物链路（端到端）

```
任务终态（completed）
  │
  ├─► _schedule_artifact_sync ──► data/artifacts/<task_id>/
  │       export.zip + 文件树散件 + .manifest.json + .synced.json
  │
  ├─► 唤醒轮（ADR-066）注入触发消息
  │       「…部署相关：产物先 list_artifacts 核对…」
  │
  └─► 编排 AI 部署推进
          │
          1) list_artifacts ──► 产物存在？────否──► build_zip=true 本地打包（警告不完整）
          │                        │是
          2) read_file ──────► 读 requirements.txt 等判类型（可选）
          │
          3) bt_deploy_guide ─► 读 SOP
          │
          4) bt_mcp_call(Upload, file_name="export.zip")
          │        │
          │        ▼
          │   宝塔返回 next_action（curl，<LOCAL_FILE> 占位）
          │        │
          │        ▼
          │   maybe_run_local_command 代执行：
          │     _resolve_artifact_path（新增）──► 命中产物 export.zip
          │     替换占位 → 本机 curl → 轮询 ready
          │        │
          ▼        ▼
          5) Bash: mv 到站点目录 / 解压 / 建库 / 装依赖 …（既有 SOP）
```

### 3.2 read_file 路径解析与降级链路

```
read_file(path)
  │
  ├─ 1) 产物目录解析：artifact_service.get_cached_file_path(task_id, path)
  │       （相对路径，_sanitize_relative_path 防越界）
  ├─ 2) 会话附件解析：attachment 目录 + file_name 匹配
  ├─ 3) output_dir 解析：仅限已绑定工作目录内部（resolve 后 is_relative_to 校验）
  │
  └─ 全部未命中 ──► 失败：列出三类范围的说明，指引 AI 用 list_artifacts 查清单
       命中 ──► UTF-8 解码 ──失败──► 「二进制文件不支持文本读取」
                    │成功
                    ▼
              >2MB？──是──► 截断前 2MB + 截断提示
                    │否
                    ▼
                 返回全文
```

### 3.3 Upload 文件源解析链路（bt_call_guard 扩展后）

```
Upload(file_name) 两段式
  │
  ├─ 1) 会话附件库（现状不变）
  ├─ 2) 会话任务产物：export.zip（file_name == "export.zip" 或文件名匹配 zip）
  ├─ 3) 会话任务产物：文件树内 get_cached_file_path(task_id, file_name)
  │
  └─ 全部未命中 ──► hub_note：未找到文件 → 指引先 list_artifacts 确认产物状态
```

## 4. 降级链路

| 故障 | 行为 |
|---|---|
| 会话无关联任务 | list_artifacts/read_file 明确报「当前会话无关联任务/产物」，不臆造 |
| 产物目录不存在 | 同上，指引先完成执行或检查同步日志 |
| export.zip 缺失 | list_artifacts 提示可 build_zip=true 兜底；兜底成功强制附不完整警告 |
| 文件树也为空（从未增量同步） | 兜底拒绝：「无可打包的本地缓存，需节点在线重新同步」 |
| read_file 二进制 | 明确报不支持，不返回乱码 |
| read_file 超大 | 截断 + 提示，不爆上下文 |
| Upload 文件不存在 | hub_note 指引 list_artifacts，AI 不得盲猜文件名重试 |

## 5. 边界与红线

- read_file 白名单仅三类会话 scope 来源，**禁止全盘任意路径**；
- 产物路径一律经 `_sanitize_relative_path`，拒绝 `..`/绝对路径/反斜杠；
- 本地打包仅当 export.zip 不存在时生成（不覆盖节点全量同步产物）；
- Upload 代执行安全边界不变（命令取自服务端 next_action 原文，仅替换 `<LOCAL_FILE>`）。

## 6. 验收标准

1. US-1~US-5 全部可复现；
2. §3 三条流程图的每个分支均有单测覆盖（见 Harness）；
3. 端到端：真实任务完成后，唤醒轮部署场景下 AI 无需用户提示即可完成「核对产物 → 上传」；
4. 回归：既有 bt_mcp（38+）、artifact、orchestrator 测试全绿。
