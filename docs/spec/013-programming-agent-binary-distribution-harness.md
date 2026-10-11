# 编程 Agent 预编译二进制分发 Harness

## 1. 测试金字塔

```
            ┌───────────┐
            │   E2E     │  1 个：一行命令在真实 Linux 安装并注册
            ├───────────┤
            │ 集成测试  │  4 个：脚本各阶段串联、幂等升级、端口探测、sha256 校验
            ├───────────┤
            │  单元测试 │  6 个：平台检测、sha256 校验、端口探测、manifest 解析、
            │           │       版本比较、.env 生成
            └───────────┘
```

## 2. 单元测试清单

### 2.1 平台检测函数

| 用例 | 输入 | 期望 |
|------|------|------|
| UT-001 | `uname -s` 返回 `Linux`，`uname -m` 返回 `x86_64` | 返回 `linux-x64`，通过 |
| UT-002 | `uname -s` 返回 `Linux`，`uname -m` 返回 `aarch64` | 报错："仅支持 linux-x64" |
| UT-003 | `uname -s` 返回 `Darwin` | 报错："仅支持 linux-x64" |

### 2.2 sha256 校验函数

| 用例 | 输入 | 期望 |
|------|------|------|
| UT-004 | 文件实际 sha256 与 manifest.json 中一致 | 返回 0（成功） |
| UT-005 | 文件实际 sha256 与 manifest.json 中不一致 | 返回非 0，打印错误 |

### 2.3 端口探测函数

| 用例 | 输入 | 期望 |
|------|------|------|
| UT-006 | 4096 可用 | 返回 4096 |
| UT-007 | 4096 被占用，4097 可用 | 返回 4097 |
| UT-008 | 4096-4200 全被占用 | 报错退出 |

### 2.4 manifest 解析函数

| 用例 | 输入 | 期望 |
|------|------|------|
| UT-009 | 合法 manifest.json | 解析出 tilacode_version、sha256、filename |
| UT-010 | 缺少 sha256 字段 | 报错："manifest.json 缺少 sha256 字段" |
| UT-011 | 非法 JSON | 报错："manifest.json 解析失败" |

## 3. 集成测试清单

### 3.1 安装脚本各阶段串联

| 用例 | 场景 | 期望 |
|------|------|------|
| IT-001 | 在干净 linux-x64 容器执行安装命令 | 安装完成，systemd 服务 active，Agent 注册中枢并上报 idle |
| IT-002 | 安装脚本检测到非 linux-x64 平台 | 在平台检测阶段报错退出，不执行后续步骤 |
| IT-003 | 二进制 sha256 校验失败 | 在校验阶段报错退出，不执行解压与安装 |
| IT-004 | manifest.json 下载失败 | 在 manifest 解析阶段报错退出 |

### 3.2 幂等升级

| 用例 | 场景 | 期望 |
|------|------|------|
| IT-005 | 已安装 0.2.0 的节点再次执行安装命令 | 检测到已存在，刷新模板与 .env，重启服务，不报错 |
| IT-006 | tilacode 版本升级后重新执行安装命令 | 检测到 sha256 变化，重新下载二进制，替换后重启服务 |

### 3.3 端口探测集成

| 用例 | 场景 | 期望 |
|------|------|------|
| IT-007 | 4096 端口已被其他进程占用 | 脚本探测到 4097，写入 .env，服务启动后 tilacode 监听 4097 |
| IT-008 | 同机部署第二个 Agent 实例 | 第二次安装探测到 4096 被占用，使用 4097，两实例共存 |

## 4. E2E 测试清单

### 4.1 E2E-001：一行命令完整安装并注册

**前置条件**：
- 中枢运行在 `http://hub:8000`，已配置 `static/binaries/tilacode/1.18.13/` 与 manifest.json
- 干净的 linux-x64 容器（Ubuntu 22.04 或 Debian 12），无 Python 或 Python 版本低于 3.10
- 中枢已生成注册 Token

**步骤**：
1. 在容器内执行：
   ```bash
   curl -fsSL http://hub:8000/static/install-scripts/install-agent-programming-linux.sh \
     | sudo bash -s -- --hub-url http://hub:8000 --token <token>
   ```
2. 等待安装完成（最长 5 分钟）
3. 执行 `systemctl status opc-agent-programming` 查看服务状态
4. 执行 `/opt/opc-agent-programming/bin/tilacode --version` 验证二进制可执行
5. 在中枢查询 `GET /api/v1/agent-nodes?status=idle` 查看节点是否注册并在线

**期望**：
- 安装脚本无报错完成
- systemd 服务 active (running)
- tilacode 二进制可执行，输出版本号 1.18.13
- 中枢节点列表出现新节点，状态为 idle
- 节点持续心跳（连续 3 次心跳间隔 ≤ 10 秒）

### 4.2 E2E-002：幂等升级（重复执行安装命令）

**前置条件**：
- E2E-001 已完成，节点已在线

**步骤**：
1. 在同一容器再次执行安装命令（相同参数）
2. 等待完成
3. 查看服务状态
4. 查看中枢节点状态

**期望**：
- 安装脚本无报错完成（幂等）
- systemd 服务重启后 active (running)
- 中枢节点状态保持 idle（或短暂 offline 后恢复 idle）
- 节点身份（node_id）保持不变

## 5. CI 门禁

### 5.1 安装脚本语法检查

每次修改安装脚本后，CI 必须执行：

```bash
bash -n static/install-scripts/install-agent-programming-linux.sh
shellcheck static/install-scripts/install-agent-programming-linux.sh
```

### 5.2 manifest.json 格式校验

每次新增/修改 manifest.json 后，CI 必须执行：

```bash
python -c "import json; json.load(open('static/binaries/tilacode/{version}/manifest.json'))"
```

并校验必填字段：`tilacode_version`、`sha256`、`filename`、`size`、`platform`、`arch`。

### 5.3 二进制 sha256 一致性校验

每次发布前，校验 manifest.json 中的 sha256 与实际二进制一致：

```bash
cd static/binaries/tilacode/{version}/
actual=$(sha256sum tilacode-linux-x64 | awk '{print $1}')
expected=$(python -c "import json; print(json.load(open('manifest.json'))['sha256'])")
[ "$actual" = "$expected" ] || { echo "sha256 不一致"; exit 1; }
```

## 6. 测试基础设施

### 6.1 测试容器

- 基础镜像：`ubuntu:22.04`（x64，glibc，AVX2 支持）
- 测试用 Dockerfile 示例：

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y curl sudo
# 不预装 Python，验证脚本自动安装能力
```

### 6.2 中枢测试环境

- 中枢运行在测试机或另一个容器，监听 `0.0.0.0:8000`
- 已执行数据库迁移
- 已放置 `static/binaries/tilacode/1.18.13/` 与 manifest.json
- 已放置 `static/agent-templates/programming/0.2.0/` tarball

## 7. 关联文档

- [ADR-013 编程 Agent 预编译二进制分发协议](../adr/013-programming-agent-binary-distribution.md)
- [013-programming-agent-binary-distribution-spec.md](./013-programming-agent-binary-distribution-spec.md)
- [008-node-management-and-programming-agent-linux-harness.md](./008-node-management-and-programming-agent-linux-harness.md)
