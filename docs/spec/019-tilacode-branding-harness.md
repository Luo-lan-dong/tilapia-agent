# tilacode 品牌化路径重命名 Harness

> 关联 ADR：[ADR-019](../adr/019-tilacode-branding-rename.md) · Spec：[019-tilacode-branding-spec.md](./019-tilacode-branding-spec.md)

## 测试金字塔

```
        ／ E2E ＼              协议端到端（Python 桥接 + tilacode server 联调）
      ／ 集成 ＼               tilacode 配置发现 / 插件安装 / skill 发现测试（既有测试改名修复）
    ／  单元  ＼               Python 桥接层单测（workspace / manager / client / task_bridge）
```

## 单元测试（Python，pytest）

| 用例 | 文件 | 验证点 |
|------|------|--------|
| prepare 创建 `.tilacode/` 子目录 | `tests/test_workspace.py` | 目录名改为 `.tilacode` |
| 写入 `.tilacode/tilacode.jsonc` 且已存在跳过 | `tests/test_workspace.py` | 新配置文件名 + 跳过逻辑不变 |
| read_plans 读取 `.tilacode/plans/*.md` | `tests/test_workspace.py` | 新 plans 路径 |
| plan prompt 包含 `.tilacode/plans/` | `tests/test_task_bridge.py` | prompt 文本更新 |
| client 携带 `x-tilacode-directory` 头 | `tests/test_tilacode_client.py` | 新头名 |
| manager 解析 `tilacode server listening` 行 | 既有解析测试 | 新输出行格式 |
| 注入 `TILACODE_SERVER_PASSWORD` | 既有环境变量测试 | 新变量名 |

## 集成测试（tilacode，bun test）

| 用例 | 文件 | 验证点 |
|------|------|--------|
| 配置文件发现 `tilacode.json(c)` | `packages/core/test/config/config.test.ts` | names 更名后仍按优先级合并 |
| `.tilacode` 目录发现与过滤 | `packages/core/test/config/config.test.ts`、`packages/opencode/test/config/*` | endsWith/basename 判断一致 |
| 插件安装到 `.tilacode/` | `packages/opencode/test/plugin/install.test.ts` | patchDir/patchName 新命名 |
| 全局路径 `~/.local/share/tilacode` | `packages/core/test/**`（global 相关） | app 名更名 |
| skill 版本标记 `.tilacode-version` | `packages/core/test/**`（skill 相关） | 版本文件名 |

## E2E 测试（至少 1 个）

**场景：plan 模式任务全链路**

1. 启动新构建的 tilacode server 二进制（Python 层注入 `TILACODE_SERVER_PASSWORD`）
2. Python 桥接层派发 plan 模式任务（工作目录为临时目录）
3. 断言：`{ws}/.tilacode/` 与 `{ws}/.tilacode/tilacode.jsonc` 被创建
4. 断言：任务产物出现在 `{ws}/.tilacode/plans/*.md` 且被读取上报
5. 断言：整个 `{ws}` 目录树中不存在 `.opencode/`
6. 断言：tilacode server 进程环境中无 `OPENCODE_*` 前缀变量

## CI 门禁

| 门禁 | 命令 | 要求 |
|------|------|------|
| tilacode 类型检查 | `bun run typecheck`（tilacode 根目录） | 0 错误 |
| tilacode 单测 | `bun test`（core/opencode 包） | 无新增失败 |
| tilacode 构建 | `bun run script/build.ts --skip-embed-web-ui`（按 ADR-013） | 产出二进制 |
| Python 测试 | `pytest`（opc-agent-programming） | 全量通过 |
| 品牌检查 | 工作目录产物 grep `.opencode` | 无残留（排除保留清单项） |

## 回归风险点

- `endsWith(".tilacode")` 与 `basename === ".tilacode"` 两种判断方式必须同步，漏改任一处会导致配置发现静默丢失
- flag.ts 更名后，所有 `Flag.OPENCODE_*` 读取方若漏改会在编译期暴露（属性不存在），但 `process.env["OPENCODE_*"]` 字符串引用**编译期不报错**，需 grep 复查
- desktop / app / enterprise 等 UI 包不在部署链路，其中 `OPENCODE_*` 引用允许暂缓，但不得反向修改 core 的命名迁就它们
