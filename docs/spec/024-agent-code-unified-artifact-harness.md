# Code Agent 统一产物与 Nuitka 源码保护 Harness

关联 ADR：[ADR-024](../adr/024-agent-code-unified-artifact.md)
关联 Spec：[024-agent-code-unified-artifact-spec.md](./024-agent-code-unified-artifact-spec.md)

## 1. 测试金字塔

```
          E2E（手动：真实服务器 + 真实中枢）
         ┌────────────────────────────────────────┐
         │ 无Python节点安装 / 旧节点升级 / 任务执行  │
         └────────────────────────────────────────┘
        集成测试（构建机 + 本地验证）
       ┌──────────────────────────────────────────┐
       │ 组包脚本产物结构 / RuoYi 发布新 key / 清单 │
       └──────────────────────────────────────────┘
      单元测试（构建机）
     ┌────────────────────────────────────────────┐
     │ build-info 生成 / 清单条目映射 / service 定义 │
     └────────────────────────────────────────────┘
```

## 2. 单元/构建级检查清单（CI 或本地）

| 编号 | 测试项 | 位置 | 断言要点 |
|------|--------|------|---------|
| UT-1 | 组包脚本产物结构 | 服务器上检查 | tarball 顶层仅 bin/ systemd/ env.example build-info.json README.md；无 .py、无 __pycache__、无 linux-python/、无 tests/ |
| UT-2 | build-info.json 完整性 | 同上 | artifact_version/tilacode_version/built_at/platform 字段齐全且与入参一致 |
| UT-3 | systemd unit 定义 | 静态检查 | ExecStart 指向 /opt/opc-agent-code/bin/opc-agent；Restart=always；StartLimitIntervalSec=0（ADR-020 兜底保留） |
| UT-4 | RuoYi key 白名单 | RuoYi 后端 | `resolvePath("agent-code-linux-x64","0.5.0")` 返回 `agent-code/0.5.0/opc-agent-code-0.5.0-linux-x64.tar.gz`；旧 key 返回「不支持的资源类型」 |
| UT-5 | 清单契约不变 | hub 侧 | 现有 ADR-022 测试全绿（ResourceSyncService 未改动，回归确认） |

## 3. 集成测试清单（构建服务器 + 本地）

| 编号 | 测试项 | 步骤 | 断言要点 |
|------|--------|------|---------|
| IT-1 | Nuitka 产物冒烟 | 服务器上 `./bin/opc-agent --version` | 正常输出版本并退出（pydantic/structlog/psutil/websockets 打包完整） |
| IT-2 | Nuitka 产物启动冒烟 | 服务器上以示例 .env 启动，观察日志 | 能读取 .env 配置、能解析环境变量、能建立到中枢的 WS 连接 |
| IT-3 | 组包脚本幂等 | 连续执行两次 build-code-agent.sh | 两次均成功，产物可覆盖 |
| IT-4 | RuoYi 发布新 key | 发布页选择 agent-code-linux-x64 0.5.0 上传 | 发布成功；`curl {ROOT}manifest.json` 条目 path/sha256/size 正确 |
| IT-5 | 中枢同步消费 | 中枢配置 manifest_url → 立即检查 → 应用更新 | 状态 up-to-date；`/static/agent-code/...` 200 |
| IT-6 | 安装脚本依赖检测 | 无 curl/tar 的最小环境模拟 | 明确报错缺什么，不误装 Python |

## 4. E2E 测试清单（发版前手动，真实环境）

| 编号 | 场景 | 步骤 | 预期 |
|------|------|------|------|
| E2E-1 | 无 Python 全新安装 | Ubuntu 22.04（或验证机）执行一行安装命令 | 安装成功零 Python 依赖；中枢节点注册、心跳、metrics 正常 |
| E2E-2 | CentOS 8 兼容 | CentOS 8 节点安装（历史踩坑机型） | 安装成功（原 Python 兜底链场景现在无 Python 需求） |
| E2E-3 | 真实任务闭环 | 安装后从中枢派发一个编程任务 | tilacode 拉起、任务执行、交付物回传正常 |
| E2E-4 | 旧节点升级迁移 | 0.4.4 节点重跑安装命令 | 中枢节点记录不变（client_id 保留）；旧服务停用；新服务运行 |
| E2E-5 | 冷启动性能 | systemd start → 日志出现心跳 | 冷启动 < 5s（与源码版对比记录） |
| E2E-6 | 源码保护核验 | 解包产物 grep 源码特征 | 无 app/ 源码、无 .pyc 可直接反编译路径 |

## 5. 门禁

| 门禁 | 要求 |
|------|------|
| 构建 | build-code-agent.sh 产出通过 UT-1/2 检查 |
| 后端 | RuoYi `mvn compile` 通过；hub 侧现有测试全绿（UT-5） |
| 手动门禁 | E2E-1 ~ E2E-4 全部通过后方可全网推送新版安装命令 |
| 文件长度 | 新增脚本 ≤300 行（build-code-agent.sh、新安装脚本） |
