# ADR-022：Agent 资源云端分发与热更新

## 状态

Superseded（被 [ADR-024](./024-agent-code-unified-artifact.md) 修订产物协议、[ADR-025](./025-agent-resource-type-binding.md) 修订 key 规范 v2；当前有效见 ADR-025，云端仓库见 [ADR-023](./023-cloud-repository-management.md)，本 ADR 保留历史协议供追溯）

## 日期

2026-09-06

## 问题背景

[ADR-013](./013-programming-agent-binary-distribution.md) 确立了「中枢 static 托管 → Agent 节点下载」的分发协议。在 [ADR-021](./021-hub-windows-desktop-deployment.md) 桌面一体化部署落地后，暴露出新的问题：

1. **安装包体积巨大**：TilapiaHub 安装程序被迫内置 `static/` 全部内容——tilacode 二进制（~130MB）+ Agent 模板 + 安装脚本，安装包达到 200MB 级。
2. **资源更新必须重装**：Agent 模板（`opc-agent-programming`）迭代频繁（当前 0.4.4），每次更新用户都要重新下载安装 TilapiaHub。
3. **职责混乱**：中枢控制台桌面程序被迫携带 Agent 分发内容，二者发布节奏完全不同。

期望形态：**Agent 资源与 TilapiaHub 安装程序解耦**——资源由云端仓库托管，TilapiaHub 运行期自动/手动同步到本地缓存并对外服务，实现资源热更新，用户无需重装。

### 需求（用户已确认）

1. 云端源：自建服务器/对象存储，manifest URL 可配置。
2. 离线兜底：拉取失败时用本地缓存已有版本继续服务；全新离线安装提供「资源包导入」入口。
3. Agent 节点侧零改动（安装命令与下载协议保持 ADR-013 不变）。

## 决策

### 1. 云端资源仓库结构（扩展 ADR-013 的 manifest 机制）

云端仓库为一个 HTTP 静态目录（自建服务器/对象存储静态托管均可），根目录放总清单 `manifest.json`：

```json
{
  "revision": "2026-09-06T12:00:00Z",
  "resources": [
    {
      "key": "install-script-linux",
      "path": "install-scripts/install-agent-programming-linux.sh",
      "sha256": "…64 位十六进制…",
      "size": 31234
    },
    {
      "key": "agent-template-programming",
      "path": "agent-templates/programming/0.4.4/opc-agent-programming-0.4.4.tar.gz",
      "sha256": "…",
      "size": 1234567,
      "version": "0.4.4"
    },
    {
      "key": "tilacode-linux-x64",
      "path": "binaries/tilacode/1.18.13/tilacode-linux-x64",
      "sha256": "…",
      "size": 136524288,
      "version": "1.18.13"
    }
  ]
}
```

- `revision`：仓库整体版本（ISO 时间戳），中枢据此判断是否需要同步
- `path` 为相对仓库根的路径；下载 URL = `{manifest_url 去掉 manifest.json}/ + path`
- 单资源版本信息（`version`）用于前端展示，同步判定以 `revision` 为准

### 2. 本地缓存：`data/resources/`（运行期数据目录）

```
data/resources/
├── manifest.json            ← 当前生效清单（云端副本）
└── install-scripts/、agent-templates/、binaries/   ← 与云端同构的资源实体
```

- 原子性：资源先下载到 `data/resources/.tmp/`，sha256 校验通过后 rename 到目标路径
- `manifest_url` 未配置或云端不可达：缓存已有内容继续服务（不阻塞启动）；缓存为空则 `/static` 回退到安装目录内置基线（开发模式仍用仓库 static/）

### 3. 同步时机：检查自动、应用需用户确认（两阶段分离）

| 阶段 | 时机 | 行为 |
|------|------|------|
| **检查（check）** | 启动时 + 定时（默认 6 小时，间隔可配置，0=禁用）+ 设置页手动按钮 | 仅下载云端 manifest 比对版本：发现新版本则置状态 update-available 并在设置页提示，**不自动下载** |
| **应用（update）** | 用户在设置页点击「更新」（可按资源条目勾选，当前仅编程 Agent 一种模板） | 下载选定资源 → sha256 校验 → 原子落盘 → `/static` 立即服务新内容 |
| **导入（import）** | 用户上传 zip 资源包（离线兜底） | 校验后直接应用，不经检查阶段 |

### 4. `/static` 服务指向动态化

FastAPI 挂载调整：`data/resources/` 存在有效清单时挂载它，否则挂载安装目录内置 `static/`（开发模式回退）。Agent 侧下载协议与 URL 完全不变。

### 5. 配置与热更新

- 云端 manifest URL 存入 `system_settings` 表（键 `agent_resource_manifest_url`），支持运行期热更新（沿用 ADR-011 网络配置的模式）
- URL 为空 = 禁用云端同步
- 打包不再内置 `static/`（PyInstaller spec 移除该 datas）；开发模式仓库内 `static/` 保留作为回退基线

### 6. 手动导入（离线兜底）

系统设置「资源管理」页提供 zip 上传：zip 结构与云端仓库根一致（含 manifest.json），后端解压校验（manifest + 逐资源 sha256）通过后原子落入缓存。

### 7. 资源粒度与更新确认

- 检查结果按**资源条目**展示：本地版本 vs 云端版本、大小、更新时间
- 用户勾选需要更新的条目后点击「应用更新」（当前业务资源仅编程 Agent 模板一项；安装脚本与 tilacode 二进制同属清单，可一并勾选）
- 设计为按资源粒度以兼容未来多 Agent 类型（视频/文案等）扩展

## 备选方案

| 方案 | 优势 | 劣势 | 结论 |
|------|------|------|------|
| **A（本决策）：云端清单驱动 + 本地缓存** | 安装包降至 ~60MB；资源热更新；Agent 零改动；离线可兜底 | 新增同步服务与缓存管理 | ✅ 选中 |
| B：仅云端化 tilacode 二进制，脚本/模板仍内置 | 改动最小 | 模板才是高频更新对象，不符合诉求 | ❌ 否决 |
| C：中枢纯代理转发，不落盘 | 无缓存管理 | Agent 必须访问外网；离线不可用；排障困难 | ❌ 否决 |
| D：安装包内置基线 + 云端覆盖 | 离线开箱即用 | 包体积回到 200MB 级 | ❌ 否决（保留为 resource_root/static 回退行为，不作安装内容） |
| E：云端 URL 改为 GitHub Releases | 免费托管 | 国内访问不稳定 | ❌ 否决（URL 可配置，未来可指向任意源） |

## 决策原因

1. **解耦发布节奏**：TilapiaHub 与 Agent 资源各自独立发版，资源更新零安装
2. **安装包瘦身**：200MB 级 → ~60MB
3. **Agent 侧零改动**：下载协议与 URL 不变，符合 ADR-013 协议稳定性要求
4. **离线可用性优先**：缓存续用 + 手动导入，内网部署不受影响（用户已确认）
5. **复用既有机制**：sha256 校验（ADR-013）、system_settings 热更（ADR-011）、scheduler 循环（ADR-015）均复用

## 影响

### 对 ADR-013 的影响

- 分发链路新增「云端仓库 → 中枢缓存」一环：资源由开发者上传云端仓库，中枢同步落地后继续按原协议服务
- manifest.json 从「单二进制元数据」扩展为「仓库总清单」（原单二进制 manifest 格式仍保留在 binaries 目录内，兼容安装脚本现有解析）
- Agent 节点下载协议、URL、sha256 校验完全不变

### 对 ADR-021 的影响

- 修订打包内容：PyInstaller spec 移除 `static/` datas（安装程序不再内置 Agent 资源）
- 修订降级链路：资源缺失时 Agent 安装功能降级不可用（明确报错），其余功能不受影响

### 对系统架构的影响

- 新增 `ResourceSyncService`（清单比对、下载校验、原子落盘）
- `/static` 挂载指向动态化（缓存优先、基线回退）
- `system_settings` 新增 `agent_resource_manifest_url` 键
- 新增 API：资源状态查询 / 手动同步 / zip 导入（挂 `/settings/resources` 下）
- scheduler 新增资源同步定时协程

### 对运维的影响

- 资源发布流程：构建产物 → 上传云端仓库（更新 manifest.json 的 revision/sha256）→ 中枢自动/手动同步 → 无需重装
- 内网环境：导入 zip 或预先放置 `data/resources/`

### 对安全的影响

- 云端 URL 走 HTTPS（生产要求）；sha256 校验防篡改
- 资源仅落 `data/resources/`，由中枢本地服务，不直接暴露云端地址给 Agent 节点

## 关联文档

- [ADR-013 编程 Agent 预编译二进制分发协议](./013-programming-agent-binary-distribution.md)（本决策修订其分发源策略，Agent 下载协议不变）
- [ADR-021 中枢 Windows 桌面一体化部署](./021-hub-windows-desktop-deployment.md)（本决策修订其打包内容）
- system_settings 运行期热更机制（沿用网络配置热更的既有模式；原文 ADR-011 引用序号有误，无可解析目标）
- [022-agent-resource-cloud-distribution-spec.md](../spec/022-agent-resource-cloud-distribution-spec.md)
- [022-agent-resource-cloud-distribution-harness.md](../spec/022-agent-resource-cloud-distribution-harness.md)
- [022-agent-resource-cloud-distribution-changes.md](../spec/022-agent-resource-cloud-distribution-changes.md)

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|---------|--------|
| 2026-09-06 | 初始创建：云端清单驱动 + data/resources 本地缓存 + 启动/定时/手动三时机同步 + zip 导入兜底 | AI助手 |
