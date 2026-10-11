# tilacode 二进制构建与分发指南

> 本文档基于 [ADR-013](../../docs/adr/013-programming-agent-binary-distribution.md) 决策，描述 tilacode 预编译二进制的构建、迁移、分发完整流程。

## 1. 构建环境要求

| 项           | 要求                                         |
| ----------- | ------------------------------------------ |
| Bun         | >= 1.3.x（仅构建机需要，Agent 节点不需要）               |
| 操作系统        | Linux x64 / macOS / Windows 均可（Bun 支持交叉编译） |
| 内存          | >= 4GB（全平台构建需要更多，单平台构建 2GB 即可）             |
| tilacode 源码 | 已 `bun install`                            |

## 2. 构建命令

进入 tilacode 的 opencode 包目录：

```bash
cd opc-agent-programming/tilacode/packages/opencode
```

### 2.1 仅构建 linux-x64-baseline（推荐，MVP 用）

在**任意操作系统**上都可以构建 linux-x64-baseline 二进制（Bun 交叉编译），内存占用小，且兼容所有 x86\_64 CPU（不需要 AVX2）：

```bash
bun run build --skip-embed-web-ui --target linux-x64-baseline
```

参数说明：

- `--skip-embed-web-ui`：不嵌入 Web UI，产出命令行版本（\~140MB）

- `--target linux-x64-baseline`：只构建 linux-x64-baseline 一个目标，兼容无 AVX2 的老 CPU

> **为什么用 baseline 而不是 linux-x64？**
> `linux-x64` 默认需要 CPU 支持 AVX2，老 CPU 运行会报 `非法指令（核心已转储）`。
> `linux-x64-baseline` 不使用 AVX2，在新老 x86\_64 CPU 上都能运行，性能差异对 Agent 场景可忽略。

### 2.2 在 Linux x64 机器上构建当前平台

```bash
bun run build --single --skip-embed-web-ui
```

参数说明：

- `--single`：只构建当前平台（在 Linux x64 上 = linux-x64，需要 AVX2）

### 2.3 全平台构建（12 个目标，慎用）

```bash
bun run build --skip-embed-web-ui
```

**注意**：全平台构建会同时编译 12 个目标平台，内存占用大。在 Windows 上容易触发 `Bun has run out of memory` 错误。如果需要全平台构建，建议在 Linux 机器上执行，或使用 `--target` 逐个构建。

### 2.4 可用的 --target 值

| --target 值                | 说明                                                  |
| ------------------------- | --------------------------------------------------- |
| `linux-x64`               | Linux x64 + glibc + AVX2（需要较新 CPU）                  |
| `linux-x64-baseline`      | Linux x64 + glibc + 无 AVX2（MVP 推荐，兼容所有 x86\_64 CPU） |
| `linux-arm64`             | Linux ARM64 + glibc                                 |
| `linux-x64-musl`          | Linux x64 + musl（Alpine）                            |
| `linux-arm64-musl`        | Linux ARM64 + musl（Alpine）                          |
| `linux-x64-musl-baseline` | Linux x64 + musl + 无 AVX2                           |
| `darwin-arm64`            | macOS ARM64（Apple Silicon）                          |
| `darwin-x64`              | macOS x64                                           |
| `darwin-x64-baseline`     | macOS x64 + 无 AVX2                                  |
| `windows-arm64`           | Windows ARM64                                       |
| `windows-x64`             | Windows x64                                         |
| `windows-x64-baseline`    | Windows x64 + 无 AVX2                                |

## 3. 构建产物位置

构建成功后，产物位于：

```
opc-agent-programming/tilacode/packages/opencode/dist/
└── tilacode-linux-x64-baseline/
    ├── bin/
    │   └── opencode              # 预编译二进制（~140MB，文件名固定为 opencode）
    └── package.json              # 平台元数据
```

**命名说明**：

- 产物目录名：`tilacode-{os}-{arch}[-baseline][-musl]`

- 二进制文件名：固定为 `opencode`（build.ts 中 `outfile: dist/${name}/bin/opencode`，渐进式重命名，暂不改）

- 发布到中枢时重命名为：`tilacode-linux-x64`（安装脚本按此文件名下载，不暴露 baseline 后缀）

- tilacode 版本号：来自 `packages/opencode/package.json` 的 `version` 字段

## 4. 迁移到中枢 static 目录

### 4.1 复制二进制（重命名）

```bash
# 创建目标目录（已存在则跳过）
mkdir -p ../../opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/

# 复制并重命名：opencode → tilacode-linux-x64
# 注意：产物来自 tilacode-linux-x64-baseline，但发布文件名保持 tilacode-linux-x64
cp dist/tilacode-linux-x64-baseline/bin/opencode \
   ../../opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/tilacode-linux-x64

# 赋予执行权限（Linux/macOS 上，Windows 上跳过）
chmod +x ../../opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/tilacode-linux-x64
```

### 4.2 计算 sha256 和 size

```bash
cd ../../opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/

# 计算 sha256
sha256sum tilacode-linux-x64 | awk '{print $1}'
# 输出示例：a1b2c3d4e5f6...（64 位十六进制）

# 计算文件大小（字节）
stat -c %s tilacode-linux-x64      # Linux
stat -f %z tilacode-linux-x64      # macOS
# 输出示例：136524288
```

### 4.3 更新 manifest.json

编辑 `opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/manifest.json`，将 `sha256` 和 `size` 字段从占位值替换为真实值：

```json
{
  "tilacode_version": "1.18.13",
  "agent_template_version": "0.2.0",
  "platform": "linux",
  "arch": "x64",
  "abi": "glibc",
  "avx2": false,
  "filename": "tilacode-linux-x64",
  "sha256": "这里填入 sha256sum 的输出",
  "size": 这里填入 stat 的输出数字,
  "built_at": "2026-08-10T10:00:00Z",
  "build_flags": ["--target", "linux-x64-baseline", "--skip-embed-web-ui"],
  "download_url": "/static/binaries/tilacode/1.18.13/tilacode-linux-x64"
}
```

### 4.4 打包 Agent 模板 tarball（0.2.0）

```bash
cd opc-agent-programming

# 打包（排除 tilacode 源码、.venv、缓存、二进制）
tar --exclude='tilacode' --exclude='.venv' --exclude='__pycache__' --exclude='.git' --exclude='*.pyc' -czf ../opc-central-dispatch-hub/static/agent-templates/programming/0.2.0/opc-agent-programming-0.2.0.tar.gz .
```

打包完成后，必须更新对应的 `manifest.json`：

```bash
cd ../opc-central-dispatch-hub/static/agent-templates/programming/0.2.0/

# 计算 tarball sha256
sha256sum opc-agent-programming-0.2.0.tar.gz | awk '{print $1}'

# 计算 tarball 大小（字节）
stat -c %s opc-agent-programming-0.2.0.tar.gz
```

将 `manifest.json` 中的 `tarball.sha256` 和 `tarball.size` 替换为真实值：

```json
{
  "agent_template_version": "0.2.0",
  "agent_type": "programming",
  "tilacode_version": "1.18.13",
  "tarball": {
    "filename": "opc-agent-programming-0.2.0.tar.gz",
    "sha256": "这里填入 sha256sum 的输出",
    "size": 这里填入 stat 的输出数字
  },
  "built_at": "2026-08-14T02:00:00Z"
}
```

**为什么必须更新 manifest.json？**
安装脚本根据 `manifest.json` 中的 `tarball.sha256` 判断本地模板是否已过期。如果代码已修改但 manifest 中的 sha256 未更新，重复执行安装脚本时会认为本地已是最新，导致节点无法升级到修复后的版本。

## 5. 最终目录结构

完成构建和迁移后，中枢 static 目录应该是：

```
opc-central-dispatch-hub/static/
├── install-scripts/
│   └── code/install-agent-code-linux.sh   # 安装脚本（已扩展二进制下载逻辑）
├── agent-templates/
│   └── programming/
│       └── 0.2.0/
│           └── opc-agent-programming-0.2.0.tar.gz  # Agent 模板包
└── binaries/
    └── tilacode/
        └── 1.18.13/
            ├── tilacode-linux-x64                # 预编译二进制（~140MB，baseline 版本）
            └── manifest.json                     # 版本与校验元数据
```

## 6. 验证

### 6.1 二进制可执行性验证

```bash
# 在 Linux x64 机器上
./opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/tilacode-linux-x64 --version
# 应输出版本号

./opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/tilacode-linux-x64 serve --port 4096
# 应启动 HTTP server，Ctrl+C 退出
```

如果报 `非法指令（核心已转储）`，说明当前 CPU 不支持 AVX2，请改用 `linux-x64-baseline` 重新构建。

### 6.2 中枢分发验证（中枢需运行中）

```bash
# 验证 manifest 可下载
curl http://<hub>:8000/static/binaries/tilacode/1.18.13/manifest.json

# 验证二进制可下载（HEAD 请求看大小）
curl -I http://<hub>:8000/static/binaries/tilacode/1.18.13/tilacode-linux-x64
```

### 6.3 sha256 一致性校验

```bash
cd opc-central-dispatch-hub/static/binaries/tilacode/1.18.13/
actual=$(sha256sum tilacode-linux-x64 | awk '{print $1}')
expected=$(python -c "import json; print(json.load(open('manifest.json'))['sha256'])")
[ "$actual" = "$expected" ] && echo "sha256 一致" || echo "sha256 不一致"
```

## 7. tilacode 升级流程

当 tilacode 版本升级时（如 1.18.13 → 1.19.0）：

```bash
# 1. 构建新版本二进制
cd opc-agent-programming/tilacode/packages/opencode
bun run build --skip-embed-web-ui --target linux-x64-baseline

# 2. 创建新版本目录
mkdir -p ../../opc-central-dispatch-hub/static/binaries/tilacode/1.19.0/

# 3. 复制二进制（产物目录带 -baseline，发布文件名保持 tilacode-linux-x64）
cp dist/tilacode-linux-x64-baseline/bin/opencode \
   ../../opc-central-dispatch-hub/static/binaries/tilacode/1.19.0/tilacode-linux-x64
chmod +x ../../opc-central-dispatch-hub/static/binaries/tilacode/1.19.0/tilacode-linux-x64

# 4. 计算 sha256 和 size
cd ../../opc-central-dispatch-hub/static/binaries/tilacode/1.19.0/
sha256sum tilacode-linux-x64 | awk '{print $1}'
stat -c %s tilacode-linux-x64

# 5. 创建新版本的 manifest.json（复制旧版本，更新 version、sha256、size、built_at）

# 6. 更新安装脚本中的 TILACODE_VERSION 变量
# 编辑 opc-central-dispatch-hub/static/install-scripts/code/install-agent-code-linux.sh
# 将 TILACODE_VERSION="1.18.13" 改为 TILACODE_VERSION="1.19.0"

# 7. 在已部署节点重新执行安装命令（幂等升级）
curl -fsSL http://<hub>/static/install-scripts/code/install-agent-code-linux.sh \
  | sudo bash -s -- --hub-url http://<hub>:8000 --token <token>
```

## 8. 常见问题

### Q: 全平台构建报 `Bun has run out of memory`

**原因**：全平台构建（不带 `--target` 或 `--single`）会同时编译 12 个目标，内存占用大。

**解决**：使用 `--target linux-x64-baseline` 只构建需要的平台：

```bash
bun run build --skip-embed-web-ui --target linux-x64-baseline
```

### Q: 构建时报 `ConnectionRefused downloading tarball` 或 `Failed to extract executable for 'bun-linux-x64'`

**原因**：在 Windows 上交叉编译 Linux 二进制时，Bun 需要从 GitHub 下载 Linux 版 Bun 编译器，网络不通会失败。

**解决**：配置代理后重试。PowerShell 中：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:HTTP_PROXY = "http://127.0.0.1:7890"
bun run build --skip-embed-web-ui --target linux-x64-baseline
```

**验证代理连通性**：

```powershell
curl.exe --proxy http://127.0.0.1:7890 -sI https://github.com
# 应返回 HTTP/1.1 200 OK
```

### Q: 构建时报 `.git can't be found`

这是 husky 的警告，不影响构建，可忽略。

### Q: 二进制文件名是 `opencode` 而不是 `tilacode`

build.ts 中 `outfile: dist/${name}/bin/opencode` 固定了二进制文件名为 `opencode`。迁移到中枢时需重命名为 `tilacode-linux-x64`（见第 4.1 节）。

### Q: Windows 上构建的 linux 二进制能否直接在 Linux 上运行

可以。Bun 的 compile 功能支持交叉编译，在 Windows 上构建的 linux-x64 二进制可以直接复制到 Linux x64 机器上运行（需 `chmod +x`）。

### Q: 130MB 的二进制是否提交到 Git

不提交。`.gitignore` 已排除 `static/binaries/tilacode/*/tilacode-linux-x64`。二进制通过手动复制或后续 CI 同步到部署环境。manifest.json 和安装脚本会提交到 Git。

## 9. 关联文档

- [ADR-013 编程 Agent 预编译二进制分发协议](../../docs/adr/013-programming-agent-binary-distribution.md)

- [编程 Agent 预编译二进制分发 Spec](../../docs/spec/programming-agent-binary-distribution-spec.md)

- [安装脚本](../../opc-central-dispatch-hub/static/install-scripts/code/install-agent-code-linux.sh)

- [build.ts 构建脚本](packages/opencode/script/build.ts)

## 变更记录

| 日期         | 变更内容                          | 变更人  |
| ---------- | ----------------------------- | ---- |
| 2026-08-10 | 初始创建：构建命令、产物位置、迁移路径、升级流程、常见问题 | AI助手 |

