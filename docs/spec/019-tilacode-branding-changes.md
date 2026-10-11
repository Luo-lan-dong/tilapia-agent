# tilacode 品牌化路径重命名 Changes

> 关联决策文档：[ADR-019](../adr/019-tilacode-branding-rename.md) · [Spec](./019-tilacode-branding-spec.md) · [Harness](./019-tilacode-branding-harness.md)

## 变更清单

> 编码期间边开发边实时登记；类型：修（修改既有）/ 增（新增）/ 减（删除）。

### 一、tilacode TS 源码（L1/L2 目录与配置文件名）

| #  | 文件                                                     | 类型 | 函数/类级明细                                                                                                                          | 作用说明                                  |
| -- | ------------------------------------------------------ | -- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| 1  | `packages/core/src/global.ts`                          | 修  | `app = "opencode"` → `"tilacode"`；`OPENCODE_TEST_HOME` → `TILACODE_TEST_HOME`                                                    | 全局数据/配置/缓存/状态目录由 opencode 改为 tilacode |
| 2  | `packages/core/src/config.ts`                          | 修  | `names` 数组、`.opencode` targets 与 basename 过滤（4 处）                                                                                | 配置发现改为 tilacode.json(c) / .tilacode   |
| 3  | `packages/core/src/skill/discovery.ts`                 | 修  | `.opencode-version` → `.tilacode-version`（2 处）                                                                                   | skill 缓存版本标记文件名                       |
| 4  | `packages/core/src/plugin/agent.ts`                    | 修  | plans 权限规则路径 `.opencode/plans` → `.tilacode/plans`                                                                               | plan 产物目录权限                           |
| 5  | `packages/opencode/src/config/paths.ts`                | 修  | `.opencode` targets（2 处）                                                                                                         | 配置目录发现                                |
| 6  | `packages/opencode/src/config/config.ts`               | 修  | `ConfigPaths.files("opencode",...)` → `("tilacode",...)`；`TILACODE_CONFIG_CONTENT`；`endsWith(".opencode")` → `.tilacode`         | 配置文件名与目录判定                            |
| 7  | `packages/opencode/src/config/tui.ts`                  | 修  | `endsWith(".opencode")`（2 处）                                                                                                     | TUI 配置目录判定                            |
| 8  | `packages/opencode/src/config/tui-migrate.ts`          | 修  | `opencode.json` 引用 → `tilacode.json`                                                                                             | 迁移提示文案                                |
| 9  | `packages/opencode/src/plugin/install.ts`              | 修  | `.opencode` 目录 + patchName                                                                                                       | 插件安装目录                                |
| 10 | `packages/opencode/src/session/session.ts`             | 修  | `.opencode/plans` → `.tilacode/plans`                                                                                            | plan 产物路径                             |
| 11 | `packages/opencode/src/plugin/tui/runtime.ts`          | 修  | `.opencode` basename/themes/tui.json（3 处）                                                                                        | TUI 主题与配置目录                           |
| 12 | `packages/opencode/src/agent/agent.ts`                 | 修  | plans 权限规则路径                                                                                                                     | plan 产物目录权限                           |
| 13 | `packages/opencode/src/cli/cmd/agent.ts`               | 修  | agents 目录 `.opencode` → `.tilacode`                                                                                              | agent 文件目录                            |
| 14 | `packages/opencode/src/cli/cmd/mcp.ts`                 | 修  | 配置候选路径                                                                                                                           | MCP 配置发现                              |
| 15 | `packages/opencode/src/installation/index.ts`          | 修  | `.opencode/bin` → `.tilacode/bin` 检测                                                                                             | 安装目录检测                                |
| 16 | `packages/opencode/src/cli/cmd/uninstall.ts`           | 修  | `.opencode/bin` PATH 清理（4 处）                                                                                                     | 卸载时 PATH 清理                           |
| 17 | `packages/opencode/src/cli/cmd/serve.ts`               | 修  | listening 输出行（协议）；describe 文案 `opencode server` → `tilacode server`；警告文案 `OPENCODE_SERVER_PASSWORD` → `TILACODE_SERVER_PASSWORD` | 协议行 + 用户可见文案                          |
| 18 | `packages/opencode/src/server/**`（workspace-routing 等） | 修  | `x-opencode-directory` 头读取/删除 → `x-tilacode-directory`                                                                           | 工作目录路由协议头                             |
| 19 | 根目录 `install` 脚本                                       | 修  | `INSTALL_DIR=$HOME/.tilacode/bin`                                                                                                | 安装目录品牌化（下载 URL 保留）                    |

### 二、tilacode TS 源码（L3 环境变量）

| #   | 文件                                                 | 类型 | 函数/类级明细                                                                                                                                                                         | 作用说明                                                                        |
| --- | -------------------------------------------------- | -- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| 20  | `packages/core/src/flag/flag.ts`                   | 修  | 全部 `process.env["OPENCODE_*"]` 读取 → `["TILACODE_*"]`（约 40 处；Flag 对象属性名保持 `OPENCODE_*` 不变）                                                                                       | 环境变量前缀切换                                                                    |
| 21  | `packages/opencode/src/control-plane/workspace.ts` | 修  | 子进程 env 键 `OPENCODE_AUTH_CONTENT`/`OPENCODE_WORKSPACE_ID`/`OPENCODE_EXPERIMENTAL_WORKSPACES` → `TILACODE_*`                                                                     | workspace 子进程环境注入                                                           |
| 22  | `packages/opencode/src/effect/runtime-flags.ts`    | 修  | `TILACODE_DISABLE_DEFAULT_PLUGINS` 等 bool() 键（4 处）                                                                                                                              | Effect 运行时 flag 读取                                                          |
| 23  | `packages/sdk/js/src/client.ts`                    | 修  | 请求头 `x-opencode-directory` → `x-tilacode-directory`                                                                                                                             | SDK v1 工作目录路由头                                                              |
| 24  | `packages/sdk/js/src/v2/client.ts`                 | 修  | 请求头 `x-opencode-directory` → `x-tilacode-directory`                                                                                                                             | SDK v2 工作目录路由头                                                              |
| 24a | `packages/server/src/location.ts`                  | 修  | `ref()`：`request.headers["x-opencode-directory"]` → `["x-tilacode-directory"]`（v1 HttpApiLocation 中间件；上轮漏改的独立 server 包）                                                         | 修复 v1 路由（/api/reference、session、project-copy 等）工作目录路由失效回退 process.cwd() 的问题 |
| 24b | `packages/sdk/js/src/server.ts`                    | 修  | 监听行解析 `opencode server listening` → `tilacode server listening`；env 键 `OPENCODE_CONFIG_CONTENT` → `TILACODE_CONFIG_CONTENT`；spawn 二进制名 `opencode` → `tilacode`（server 与 tui 两处） | 修复 SDK 拉起嵌入式 server 永久挂起（协议行/env/二进制名三处联动）                                  |
| 24c | `packages/sdk/js/src/v2/server.ts`                 | 修  | 同 24b（v2 版本）                                                                                                                                                                    | 同上                                                                          |
| 24d | `.tilacode/tilacode.jsonc`（仓库自身配置）                 | 修  | reference 别名 `opencode-local` → `tilacode-local`，路径 `~/.local/share/opencode` → `~/.local/share/tilacode`                                                                       | 仓库自身 references 指向新数据目录                                                     |

### 三、tilacode TUI 包源码遗留修复（本次补充）

| #  | 文件                                                    | 类型 | 函数/类级明细                                                                                                                                              | 作用说明             |
| -- | ----------------------------------------------------- | -- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- |
| 25 | `packages/tui/src/app.tsx`                            | 修  | `process.env.OPENCODE_ROUTE`/`OPENCODE_FAST_BOOT` → `TILACODE_*`（2 处）                                                                                | TUI 启动参数环境变量     |
| 26 | `packages/tui/src/editor-zed.ts`                      | 修  | `resolveZedDbPath()`：`process.env.OPENCODE_ZED_DB` → `TILACODE_ZED_DB`                                                                               | Zed 数据库路径覆盖变量    |
| 27 | `packages/tui/src/context/editor.ts`                  | 修  | `process.env.OPENCODE_EDITOR_SSE_PORT` → `TILACODE_EDITOR_SSE_PORT`                                                                                  | 编辑器 SSE 端口变量     |
| 28 | `packages/tui/src/context/theme.tsx`                  | 修  | `themeSource.discover()`：`.opencode` → `.tilacode` 目录                                                                                                | 主题发现目录           |
| 29 | `packages/tui/src/feature-plugins/home/tips-view.tsx` | 修  | 用户可见 tips 文案：`opencode.json`/`~/.config/opencode/`/`.opencode/{commands,agents,tools,plugins,themes}/`/CLI 命令 `opencode xxx` → tilacode 对应命名（约 19 处） | 首页提示品牌化          |
| 30 | `packages/tui/src/util/error.ts`                      | 修  | 错误提示 `opencode models`/`opencode.json` → `tilacode models`/`tilacode.json`（2 处）                                                                      | 模型错误提示文案         |
| 31 | `packages/tui/src/component/dialog-provider.tsx`      | 修  | 凭据保存提示 `opencode.json` → `tilacode.json`（2 处）                                                                                                        | provider 凭据对话框文案 |

### 四、内置技能文档

| #  | 文件                                                     | 类型 | 函数/类级明细                                                                                                                                                                                                                                                                                                    | 作用说明                    |
| -- | ------------------------------------------------------ | -- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| 32 | `packages/core/src/plugin/skill/customize-opencode.md` | 修  | 全文品牌化：标题/prose `opencode` → `tilacode`、`.opencode/` → `.tilacode/`、`opencode.json(c)` → `tilacode.json(c)`、`~/.config/opencode/` → `~/.config/tilacode/`、逃生舱环境变量 `OPENCODE_*` → `TILACODE_*`、示例插件名 → `tilacode-*`。**保留**（ADR-019 §2）：`https://opencode.ai/config.json` schema URL、`@opencode-ai/plugin` 包名 | 内置 customize 技能正文与新命名一致 |

### 五、Python 桥接层

| #  | 文件                                 | 类型 | 函数/类级明细                                                                                        | 作用说明                          |
| -- | ---------------------------------- | -- | ---------------------------------------------------------------------------------------------- | ----------------------------- |
| 33 | `app/executor/workspace.py`        | 修  | `.opencode` 目录、`opencode.jsonc` 文件名、`OPENCODE_CONFIG_TEMPLATE` → `TILACODE_CONFIG_TEMPLATE` 常量 | 创建 `.tilacode/tilacode.jsonc` |
| 34 | `app/executor/task_bridge.py`      | 修  | plan 模式 prompt 文本路径 `.opencode/plans` → `.tilacode/plans`                                      | 产物路径提示                        |
| 35 | `app/executor/tilacode_manager.py` | 修  | `TILACODE_SERVER_PASSWORD` 注入、listening 行解析正则 `tilacode server listening`                      | 协议联动                          |
| 36 | `app/executor/tilacode_client.py`  | 修  | `x-tilacode-directory` 头、Basic Auth 用户名 `tilacode`                                             | 协议联动                          |

### 六、tilacode 项目自身目录

| #  | 文件/目录                                                              | 类型 | 作用说明        |
| -- | ------------------------------------------------------------------ | -- | ----------- |
| 37 | `.opencode/` → `.tilacode/`（含 `opencode.jsonc` → `tilacode.jsonc`） | 修  | 项目自身配置目录品牌化 |

### 七、tilacode 测试修复

| #  | 文件                                                                          | 类型 | 修改点                                                                                           | 作用说明                              |
| -- | --------------------------------------------------------------------------- | -- | --------------------------------------------------------------------------------------------- | --------------------------------- |
| 38 | `packages/core/test/config/config.test.ts`                                  | 修  | 测试标题 2 处（tilacode JSON/JSONC、.tilacode configuration）；此前已修 fixture 路径                         | 标题与新命名一致                          |
| 39 | `packages/core/test/models.test.ts`                                         | 修  | 注释 `TILACODE_MODELS_PATH`（1 处）+ 此前 fixture 路径                                                 | 指向实际环境变量                          |
| 40 | `packages/core/test/plugin/provider-opencode.test.ts`                       | 修  | `TILACODE_API_KEY` 标题与 `CUSTOM_TILACODE_API_KEY` env 键/names（3 处）                             | 与 src provider/opencode.ts 一致     |
| 41 | `packages/core/test/plugin/skill.test.ts`                                   | 修  | 断言 `tilacode's own configuration`（1 处）                                                        | 与 skill.ts 描述一致                   |
| 42 | `packages/opencode/test/server/httpapi-sdk.test.ts`                         | 修  | 断言头名 `x-tilacode-directory`（1 处）                                                              | 与 SDK/服务端协议一致                     |
| 43 | `packages/opencode/test/cli/tui/editor-context.test.tsx`                    | 修  | `TILACODE_EDITOR_SSE_PORT`（9 处）                                                               | 与 tui src 一致                      |
| 44 | `packages/opencode/test/cli/tui/editor-context-zed.test.ts`                 | 修  | `TILACODE_ZED_DB`（4 处）                                                                        | 与 tui src 一致                      |
| 45 | `packages/opencode/test/cli/help/__snapshots__/help-snapshots.test.ts.snap` | 修  | `TILACODE_SERVER_PASSWORD`/`TILACODE_SERVER_USERNAME or 'tilacode'`（4 处）+ serve describe（1 处） | 与 attach/run/serve 实际输出一致，34 快照全过 |
| 46 | `packages/opencode/test/AGENTS.md`                                          | 修  | `tilacode.json` 文档引用（1 处）                                                                     | 与 fixture 实际行为一致                  |
| 47 | 此前会话已修的部署链测试（config/paths/skill 发现/插件安装/httpapi listen/auth 等 fixture）      | 修  | 详见 git 历史                                                                                     | 部署链测试恢复通过                         |

### 八、测试判定保留项（不修改，防误改）

- `Flag.OPENCODE_*` 属性访问（flag.ts 对象属性名未改，仅环境变量字符串已改）

- `@opencode-ai/*` 包名与 import

- `opencode.ai` / `models.opencode.ai` / `console.opencode.ai` URL 与 `$schema`

- provider ID `"opencode"`、模型 `opencode/mimo-v2.5-free`、`Integration.ID.make("opencode")`

- LLM 网关头 `x-opencode-session/request/client/project`、内部头 `x-opencode-ticket/sync/title`、`x-opencode-workspace`

- 内置主题名 `opencode`（DEFAULT\_THEMES 键、assets/opencode.json）、keymap 键 `opencode.mode`、代码标识符（OPENCODE\_BASE\_MODE 等）

- mDNS 服务名 `opencode-${port}`、`opencode.local` 默认域（src 现状一致，未纳入 ADR 范围）

- desktop/app 包源码与测试（Electron 应用，不在 ADR-013 部署链，未纳入改名范围）

## 验证记录

| 验证项                                                         | 结果                                                                                                                                                                                                                  | 时间         |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| `bun run typecheck`（30 tasks）                               | ✅ 30/30 通过                                                                                                                                                                                                          | 2026-09-02 |
| core 包定向测试（7 个修改文件）                                         | ✅ 50 pass / 0 fail                                                                                                                                                                                                  | 2026-09-02 |
| opencode 包定向测试（help-snapshots/editor-context×2/httpapi-sdk） | ✅ 24 pass / 0 fail（34 快照）                                                                                                                                                                                           | 2026-09-02 |
| core 包全量 `bun test`                                         | 1062 pass / 8 fail（均为环境限制：沙箱禁止写 `~/.local/state/tilacode/locks`、Git Bash 非标准路径、symlink 权限；skill 断言已修复后单测通过）                                                                                                         | 2026-09-02 |
| opencode 包全量 `bun test`                                     | 3161 pass / 78 fail（失败集中在 subprocess 类测试：沙箱内 `uv_spawn 'bun'` ENOENT，无法派生子进程；symlink 权限类；均为 Windows 沙箱环境限制，与改名无关——错误信息均为环境错误，无品牌断言失败）                                                                               | 2026-09-02 |
| tui/sdk-next 包测试                                            | 无需修改（匹配均为保留项：内部标识符、主题名、装饰性快照文本）                                                                                                                                                                                     | 2026-09-02 |
| location.ts / SDK / 仓库配置修复后复跑                               | ✅ httpapi-reference、project-copy、httpapi-session（persisted session dir）、httpapi-v2-location 等此前失败项恢复通过；typecheck 复跑 30/30 通过。剩余 DB/锁类超时与 subprocess/symlink 失败均为沙箱+Windows 环境限制（真实用户目录锁文件写入受限，旧命名同样受限），需 Linux 节点复验 | 2026-09-02 |
| linux-x64-baseline 二进制构建                                    | ✅ `bun run build --skip-embed-web-ui --target linux-x64-baseline` 成功，产物 147,495,040 字节                                                                                                                              | 2026-09-02 |
| 二进制迁移 + manifest                                            | ✅ 复制为 `static/binaries/tilacode/1.18.13/tilacode-linux-x64`，sha256=`ac3233927c359ca74641c1a5f1bcdab518213b8b8020d4e82180c1cc8e3be0e1`（size 不变系等长替换所致，hash 已变化）；中枢（PID 22752，端口 8000）manifest 与二进制 HEAD 200 验证通过     | 2026-09-02 |
| Agent 模板 tarball 重打包（0.4.3 原地，按 sha256 强制升级约定）              | ✅ sha256=`3c715f18ce89703832aa6bbe992d8602c7ec9df2804a7411dcfff1a7de9a9ccf`，size=66610；manifest 已更新（built\_at、changes、tarball 校验值）；安装脚本引用 0.4.3 无需改动                                                                | 2026-09-02 |
| Python 桥接层定向测试                                              | ✅ test\_workspace / test\_task\_bridge / test\_tilacode\_client 34 passed                                                                                                                                           | 2026-09-02 |

> subprocess / symlink / lock 类失败需在 Linux 开发节点或解除沙箱后复跑验证。

## 状态

- [x] tilacode L1/L2 目录与配置文件名

- [x] tilacode L3 环境变量

- [x] tilacode 协议标识（listening 行 / directory 头）

- [x] Python 桥接层

- [x] tilacode 自身 .opencode 目录重命名

- [x] tilacode 测试修复（部署链内全部通过；环境限制类失败待 Linux 复验）

- [x] Python 测试修复

- [x] typecheck / 全量 bun test（见验证记录）

- [x] linux-x64-baseline 二进制重新构建 + 中枢迁移 + manifest 更新

- [x] Agent 模板 tarball 重新打包 + manifest 更新

> 遗留：已部署开发节点需重新执行安装脚本完成升级（sha256 已变化，幂等强制更新）；协议端到端联调（新二进制 + 新 Python 层跑一个 plan 任务验证 `.tilacode/plans/` 产物）建议在 Linux 开发节点执行。

