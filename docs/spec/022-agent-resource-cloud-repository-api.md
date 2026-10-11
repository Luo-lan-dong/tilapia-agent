# Agent 资源云端仓库接口要求

关联 ADR：[ADR-022](../adr/022-agent-resource-cloud-distribution.md)
读者：云端仓库的开发/运维实现者。

## 1. 云端仓库是什么

**一个纯静态 HTTP 文件目录**，不需要任何 API 逻辑。中枢（TilapiaHub）是它唯一的拉取客户端；Agent 节点永远不直接访问云端（Agent 只从中枢 `/static` 下载，见 ADR-013）。

中枢侧只需一个配置：仓库根 URL（下称 `{ROOT}`，如 `https://resources.example.com/agent/`），在系统设置页可随时修改。

## 2. 目录结构

```
{ROOT}/
├── manifest.json                                        ← 仓库总清单（唯一入口约定）
├── install-scripts/
│   └── install-agent-programming-linux.sh               ← Agent 安装脚本
├── agent-templates/
│   └── programming/
│       └── 0.4.4/
│           └── opc-agent-programming-0.4.4.tar.gz       ← Agent 模板包
└── binaries/
    └── tilacode/
        └── 1.18.13/
            ├── tilacode-linux-x64                       ← 预编译二进制
            └── manifest.json                            ← 单二进制元数据（ADR-013 原有，安装脚本使用）
```

## 3. manifest.json 规范（核心）

`{ROOT}/manifest.json` 必须为合法 JSON，编码 UTF-8（无 BOM）：

```json
{
  "revision": "2026-09-06T12:00:00Z",
  "resources": [
    {
      "key": "install-script-linux",
      "path": "install-scripts/install-agent-programming-linux.sh",
      "sha256": "3f79bb7b435b05321651daefd374cdc681dc06faa65e374e38337b88ca046dea",
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

### 字段要求

| 字段 | 层级 | 必填 | 说明 |
|------|------|------|------|
| `revision` | 根 | ✅ | 仓库整体版本，**ISO 8601 UTC 时间戳**或任意单调递增字符串；中枢用它判断「是否有更新」，每次发布必须变化 |
| `resources` | 根 | ✅ | 资源条目数组，至少 1 项 |
| `key` | 条目 | ✅ | 资源逻辑名，全局唯一。当前约定：`install-script-linux` / `agent-template-programming` / `tilacode-linux-x64` |
| `path` | 条目 | ✅ | 相对 `{ROOT}` 的路径，**禁止 `..`、绝对路径、反斜杠** |
| `sha256` | 条目 | ✅ | 文件内容的 SHA-256 十六进制（64 位小写）；中枢下载后强制校验 |
| `size` | 条目 | ✅ | 文件字节数（整数），中枢校验用 |
| `version` | 条目 | ⬜ | 语义版本（如 `0.4.4`），仅用于前端展示，模板与二进制条目建议填写 |

> `key` 是中枢资源管理的粒度：设置页按条目展示「本地版本 vs 云端版本」，用户按条目勾选更新。

## 4. HTTP 行为要求

| 请求 | 必须返回 | 说明 |
|------|---------|------|
| `GET {ROOT}manifest.json` | `200` + JSON 字节 | 中枢每次检查时拉取；建议 `Cache-Control: no-cache` 保证及时性 |
| `GET {ROOT}{path}` | `200` + 原始文件字节 | 大文件（130MB 二进制）要求支持流式读取；建议支持 `Range`（非必须） |
| 资源不存在 | `404` | 中枢判定云端仓库内容缺失，同步失败并明确报错 |
| 服务异常 | `5xx` | 中枢按「云端不可达」降级，用本地缓存续用 |

其他要求：

1. **认证**：MVP 阶段不做认证（中枢拉取为匿名 GET）。如需防盗链可用随机长路径前缀。后续迭代可加签名 URL。
2. **编码**：文本文件（脚本/JSON）必须 UTF-8 **无 BOM**，LF 行尾。
3. **HTTPS**：生产环境必须；内网测试可 HTTP。
4. **可用性**：云端不可达时中枢自动降级为本地缓存续用（不影响已装节点），因此云端短暂宕机不会破坏现有 Agent。

## 5. 发布流程（更新一个资源时做什么）

以「Agent 模板从 0.4.4 升级到 0.4.5」为例：

```
1. 构建新模板 tarball（现有流程，ADR-013）
2. 计算 sha256 与 size
3. 上传新文件到 {ROOT}agent-templates/programming/0.4.5/...
4. 修改 {ROOT}manifest.json：
   - resources 中 agent-template-programming 条目：path/sha256/size/version 更新为 0.4.5
   - 根 revision 更新为新的时间戳（例如当前 UTC 时间）
   - （可选）删除 0.4.4 旧文件与条目
5. 完成。中枢会在下一次检查（≤6 小时）发现更新，或由运维在设置页手动「立即检查」→ 用户确认更新
```

> 中枢提供辅助脚本 `scripts/build_cloud_manifest.py`：对本地资源目录自动计算 sha256/size 并生成 manifest.json，避免手写出错（见该脚本 --help）。

## 6. 中枢侧的消费方式（云端实现者需了解的对接事实）

- 拉取 URL 规则：`{ROOT}manifest.json` 与 `{ROOT}{resources[].path}`（manifest_url 配置为**目录级**，中枢自动拼接）
- 校验：下载字节的 sha256 与 size 双重校验，任何不匹配 → 本次同步整体放弃，保留旧缓存
- 原子性：先落 `data/resources/.tmp/` 再 rename，云端发布中途替换文件不会造成中枢半更新
- 并发：中枢内置互斥，检查/更新不会并发执行
- 中枢本地没有的缓存时，`/static` 回退安装目录内置基线（过渡期兼容）；资源云端化完全落地后安装程序不再内置

## 7. 验收清单（云端实现者自测）

- [ ] `curl {ROOT}manifest.json` 返回 200 且 JSON 合法、无 BOM
- [ ] manifest 中每个 path 用 `curl -sI {ROOT}{path}` 返回 200 且 Content-Length 与 size 一致
- [ ] 每个 path 的文件 sha256 与清单一致（`sha256sum` 核对）
- [ ] revision 已更新为新时间戳
- [ ] （生产）HTTPS 证书有效
