# Code Agent 统一产物与 Nuitka 源码保护 Changes 变更清单

关联 ADR：[ADR-024](../adr/024-agent-code-unified-artifact.md)
关联 Spec：[024-agent-code-unified-artifact-spec.md](./024-agent-code-unified-artifact-spec.md)
关联 Harness：[024-agent-code-unified-artifact-harness.md](./024-agent-code-unified-artifact-harness.md)

> 按 `.trae/rules/decision-documents.md` 要求：编码前仅创建骨架，开发过程中**每修改/新增/删除一个文件立即登记**，不得事后一次性补记。

## 变更清单

| 日期 | 文件路径 | 变更类型（修/增/减） | 函数/类级明细 | 作用说明 |
|------|---------|---------------------|--------------|---------|
| 2026-09-07 | docs/adr/024-agent-code-unified-artifact.md | 增 | - | ADR 决策文档 |
| 2026-09-07 | docs/spec/024-agent-code-unified-artifact-spec.md | 增 | - | Spec |
| 2026-09-07 | docs/spec/024-agent-code-unified-artifact-harness.md | 增 | - | Harness |
| 2026-09-07 | docs/spec/024-agent-code-unified-artifact-changes.md | 增 | - | Changes 骨架 |
| 2026-09-07 | docs/adr/013-programming-agent-binary-distribution.md | 修 | 影响/变更记录登记 | ADR-024 修订说明（单二进制 manifest 协议废止） |
| 2026-09-07 | docs/INDEX.md | 修 | 1.1/1.2/1.3/六 登记 | 决策文档索引 |
| 2026-09-07 | opc-agent-programming/app/config.py | 修 | `tilacode_binary_path`/`tilacode_workspace_root` 默认值 → `/opt/opc-agent-code/...`；新增 `APP_VERSION = "0.5.0"` 常量 | 适配新安装目录；版本号供 --version 输出 |
| 2026-09-07 | opc-agent-programming/app/main.py | 修 | `main()` 新增 `--version` 快速路径（打印后退出，不加载 .env 不建连） | 安装脚本二进制冒烟校验入口 |
| 2026-09-07 | opc-agent-programming/pyproject.toml | 修 | version 0.4.4 → 0.5.0 | 整体产物版本（ADR-024） |
| 2026-09-07 | opc-agent-programming/systemd/opc-agent-code.service | 增 | ExecStart=/opt/opc-agent-code/bin/opc-agent；保留 StartLimitIntervalSec=0（ADR-020） | 新 systemd 服务定义（二进制直跑） |
| 2026-09-07 | opc-agent-programming/scripts/build-code-agent.sh | 增 | `log`/前置检查/venv+Nuitka 安装/Nuitka 编译/冒烟/组包（build-info.json 生成）/tarball+sha256 输出 | Linux 服务器一键构建脚本（用户手动执行） |
| 2026-09-07 | opc-agent-programming/scripts/install-agent-programming-linux.sh | 修（重写） | 删除 Python 检测/venv/pip/EPEL/Miniconda/宝塔扫描全链；新增 `manifest_field`（awk 解析总清单条目）/`resolve_artifact`（ADR-022 总清单取 sha256+path）/`migrate_legacy`（旧目录 .env 身份迁移+旧服务停用）/`smoke_test`（--version）；893 → 336 行 | 零 Python 安装脚本；sha256 复用 ADR-022 总清单（无需每版本 manifest）；UTF-8 无 BOM |
| 2026-09-07 | opc-central-dispatch-hub/static/install-scripts/install-agent-programming-linux.sh | 修 | 与源副本同步（无 BOM 已校验） | hub 下发版同步 |
| 2026-09-07 | Tilapia-web/RuoYi-Vue/ruoyi-system/.../impl/CloudResourceServiceImpl.java | 修 | `resolvePath`：新增 `agent-code-linux-x64` → `agent-code/{ver}/opc-agent-code-{ver}-linux-x64.tar.gz`；移除 `agent-template-programming`/`tilacode-linux-x64` 白名单 | 发布页新 key 路径映射（ADR-024） |
| 2026-09-07 | Tilapia-web/RuoYi-Vue3/src/views/cloud/repository/index.vue | 修 | `keyOptions` 更新为整体包+安装脚本两项；上传提示文案更新 | 发布页下拉与提示适配 |
| 2026-09-07 | Tilapia-web/RuoYi-Vue/scripts/restore_cloud_manifest.py | 增 | `cos_sign`（标准库 HMAC-SHA1 实现 COS XML 签名，含 URL 编码与 content-type/content-length 强制头）/`main`（读库凭证→拉清单→恢复条目→PUT→删垃圾对象） | 一次性事故恢复脚本：开发期间误用旧 key 将 26B 假文件发布到真实 COS 污染清单，已恢复至 0.4.4 真实值并删除垃圾对象（revision 2026-09-07T12:59:47Z）；沉淀了无 SDK 环境的 COS 签名实现 |
| 2026-09-07 | docs/ops/code-agent-build-guide.md | 增 | 职责分工/上传文件清单/PowerShell 上传命令/服务器环境/构建/发布/节点升级/迭代矩阵/FAQ | ADR-024 配套运维手册（构建流程文档化落实） |
| 2026-09-07 | docs/INDEX.md | 修 | 第五节运维文档表 + 六 更新记录登记 | code-agent-build-guide 索引 |
| 2026-09-07 | docs/spec/024-agent-code-unified-artifact-spec.md | 修 | §4 头部交叉引用 ops 指南 | 文档互链 |
| 2026-09-07 | opc-agent-programming/scripts/build-code-agent.sh | 修 | Nuitka 命令追加 `--include-package=websockets` | Bug 修复：websockets 库用 `__getattr__` 动态导入子模块，Nuitka 静态分析追踪不到，运行时报 `ModuleNotFoundError: No module named 'websockets.legacy'`；强制整包包含 |
| 2026-09-07 | docs/ops/code-agent-build-guide.md | 修 | 环境准备补 Fedora/dnf 命令；FAQ 新增 websockets.legacy 报错条目 | 服务器实为 Fedora；沉淀本次踩坑 |
| 2026-09-08 | opc-agent-programming/app/main.py | 修 | 第 22 行 import 恢复为 `from app.config import APP_VERSION, AgentConfig` | Bug 修复（服务器冒烟报 `NameError: APP_VERSION is not defined` 的根因）：用户编辑器在「改 import」与「加 --version 代码块」两次编辑之间用旧缓冲区自动保存，把 import 修改覆盖掉，产生半新半旧文件并被上传服务器；本地恢复后全量核验 6 个 ADR-024 相关文件均完好 |
| 2026-09-14 | opc-agent-programming/scripts/build-code-agent.sh | 修 | VERSION 提取管道追加 `tr -d '\r'`；NUITKA_VERSION 同步剥离 | Bug 修复：构建服务器上 pyproject.toml 为 CRLF 行尾时，sed 只替换匹配部分、行尾 `\r` 残留在 VERSION 值中，产物文件名变成 `opc-agent-code-0.5.0$'\r'-linux-x64.tar.gz`（含控制字符，资源发布/安装脚本 sha256 清单均受影响） |

（开发过程中持续追加，格式同上）
