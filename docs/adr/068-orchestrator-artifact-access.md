# ADR-068：编排 AI 产物感知与文件访问（orchestrator-artifact-access）

- **状态**：Accepted
- **日期**：2026-10-08
- **关联**：ADR-028（产物缓存与落盘）、ADR-049（宝塔 MCP 部署，Upload 代执行）、ADR-051（Upload 两段式中枢代执行）、ADR-059（增量产物同步）、ADR-066（任务终态自动唤醒编排）、ADR-033（技能框架）

## 1. 问题背景（2026-10-08 用户实测）

用户实测「自主开发 → 部署到云服务器」全流程，编排 AI 卡死在产物环节：

1. **数据层有产物，AI 层看不见**：任务终态后 `_schedule_artifact_sync` 已把节点产物拉到中枢
   `data/artifacts/<task_id>/`（export.zip + 增量文件树 + manifest 清单），但编排 AI 的 24 个技能中
   **没有任何产物列举/读取技能**；`get_task_status` 返回摘要不含产物路径；系统提示词零产物指引。
2. **上传链路断裂**：中枢已有 Upload 两段式代执行（ADR-051，`maybe_run_local_command` 本机 curl），
   但文件解析 `_resolve_attachment_path` **只查 `session_attachments` 表**（用户聊天手动上传的附件），
   任务产物目录完全不在解析范围 → AI 调 `Upload(file_name="export.zip")` 必然返回
   「中枢本地未找到附件——无法代执行上传」。
3. **打包无兜底**：export.zip 仅在任务终态全量同步成功时存在；同步失败且节点离线时，已同步的
   文件树散件无法变成可上传的压缩包。
4. AI 把「我自己访问不到产物」误表述为「中枢拉取不到产物」，进一步误导排查方向。

**缺失的能力闭环**：产物落盘中枢 ✓ → **编排 AI 感知/读取/上传产物 ✗（本 ADR 补齐）**。

## 2. 决策（Decision）

为编排 AI 补齐「产物感知 + 通用文件读取 + 上传打通」三根线，全部为**接线工作**（复用既有
`artifact_service` 本地缓存，不新增同步/存储机制）：

### 2.1 新技能 `list_artifacts`（产物感知）

- 查**当前会话关联任务**的产物状态：export.zip 是否存在（含大小）、文件树清单
  （manifest：path/size 列表，按上限截断）、增量同步覆盖数（`.synced.json`）。
- 数据源全在本地缓存（`artifact_service.get_tree` / 目录扫描），**不触碰节点**；
- 会话无关联任务 / 产物目录不存在时明确报「无产物」，并指引先完成任务执行。

### 2.2 新技能 `read_file`（通用文件读取，会话范围白名单）

- 设计本意对齐编程 Agent 的 Read 工具：**通用读文本文件**（看代码判断项目类型、读配置、
  理解用户上传的文档），而非仅限产物目录；
- **目录白名单（会话 scope，防越权读全盘）**：
  1. 当前会话任务产物目录 `data/artifacts/<task_id>/`；
  2. 会话附件目录（`attachment_storage_dir` 下本会话附件）；
  3. 会话工作目录 `output_dir`（已绑定时）；
- **格式判定简化**：不做扩展名白名单——统一尝试 UTF-8 解码，失败即按二进制拒绝（返回
  「二进制文件不支持文本读取」）；超 2MB 截断并提示。免维护扩展名清单，行为自然；
- 路径解析：产物相对路径 → 产物目录；附件名 → 附件目录；绝对/相对路径 → 限 output_dir 内。

### 2.3 Upload 代执行文件源扩展（ADR-049/051 修订指向）

- `maybe_run_local_command` 的 `_resolve_attachment_path`：附件库未命中时，按**当前会话任务的
  产物目录**解析 `file_name`——优先 `export.zip`，其次文件树内同名文件；
- `file_name` 支持产物相对路径（如 `dist/app.tar.gz`），复用 `artifact_service.get_cached_file_path`；
- 产物也不存在时 hub_note 指引 AI：先 `list_artifacts` 确认产物状态，勿盲猜文件名。

### 2.4 本地打包兜底（用户裁决：做）

- `artifact_service` 新增 `build_zip_from_cache(task_id)`：export.zip 缺失时，把已同步的文件树
  散文件打包为 `export.zip`（覆盖安全：仅在 export.zip 不存在时生成）；
- **不完整性警告必须显式回传 AI**：增量同步有 2MB 单文件上限，大文件（依赖包/二进制）不在
  文件树里，本地打包的 zip 可能不完整——结果 summary 强制附带警告与补救指引（节点在线时
  重新全量同步为准）；
- 触发点：`list_artifacts` 返回 export.zip 缺失时提示 AI 可调兜底；兜底动作挂 `list_artifacts`
  的 `build_zip=true` 参数（单技能内闭环，不新增技能）。

### 2.5 提示词与唤醒消息补指引

- 系统提示词追加产物能力一句话描述（技能自带 description，无需常驻正文）；
- 唤醒触发消息（`build_wake_message`）部署相关指引补充：产物先 `list_artifacts` 核对、
  `export.zip` 可直接经宝塔 Upload 上传（中枢代执行）。

## 3. 备选方案（Considered Options）

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 只扩展 Upload 文件源（最小改动） | ❌ 不够 | AI 不知道 file_name 该传什么（无产物感知），依然会盲猜卡死 |
| 产物写入 session_attachments 表复用附件解析 | ❌ 否决 | 语义污染：附件=用户手动上传；产物入库需批量回填、清理生命周期纠缠 |
| read_file 不设白名单（全盘可读） | ❌ 否决 | 编排 AI 可读中枢配置/凭据文件，越权风险；会话 scope 白名单覆盖真实需求 |
| read_file 按扩展名白名单过滤 | ❌ 否决（用户裁决简化） | 清单免维护成本高；UTF-8 解码判定即可区分文本/二进制，行为更自然 |
| 独立 `build_artifact_zip` 技能 | ❌ 否决 | 兜底是 list_artifacts 场景的自然延伸，单参数闭环比多一个技能语义更清晰 |
| 本地打包不做，缺包时报错 | ❌ 否决（用户裁决） | 节点离线后无法补救，用户要求可自助推进 |

## 4. 影响分析

- **中枢**：`skills/artifact_access.py` 新增（2 技能）；`registry.py` 注册；`bt_call_guard.py`
  文件源扩展；`artifact_service.py` 打包兜底；`orchestrator_wake_service.py` 唤醒消息补充。
- **前端**：零改动（技能结果走既有步骤卡片渲染）。
- **节点**：零改动。
- **安全**：read_file 白名单会话 scope；产物路径解析复用 `_sanitize_relative_path` 防越界；
  Upload 代执行的安全边界不变（命令取自服务端 next_action 原文）。
- **风险与对策**：文件树打包不完整 → 强制警告 + 补救指引；大清单刷屏 → manifest 截断上限。

## 5. 验收要点

1. 会话关联任务完成后：`list_artifacts` 返回 export.zip 存在性与文件树摘要。
2. `read_file` 读产物内 `README.md` 返回全文；读 `.py` 正常；读二进制报「不支持文本读取」；
   读产物目录外路径报「超出可读范围」。
3. 部署对话：AI 调 `Upload(file_name="export.zip")` → 中枢代执行上传成功（hub_note 含服务器路径）。
4. export.zip 缺失场景（删掉后调 list_artifacts + build_zip=true）：本地打包成功且 summary 含
   不完整性警告。
5. 唤醒轮部署场景端到端：任务终态 → 唤醒 → AI 核对产物 → 上传 → 部署推进。

## 6. 实际实施情况

（回归验证中，完成后回填）
