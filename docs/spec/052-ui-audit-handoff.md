# B 线交接说明：前端统一规范与全站页面结构（给新会话）

> 本文件是**自包含交接说明**。新会话读完本文件即可接手 B 线，无需其他背景。
> 主关联决策：[ADR-052 前端亮色主题与颜色收敛](../adr/052-light-theme.md)｜规范：[frontend-component-standards.md](../standards/frontend-component-standards.md)
> B 线与 A 线（办公节点 ADR-065）**互不依赖**，可并行。

## 1. 背景（一句话）

这是 OPC 中枢调度系统的前端控制台 `opc-web-console`（React 18 + **antd 5.29** + Vite + vitest/playwright）。
用户要解决两个痛点，并**明确要求：先只出文档、按域分批、每批他审核后才继续；本阶段不改任何代码**：

1. **没有统一的设计规则**：同一种组件全站有的圆有的方；间距/内边距/控件高度/图标大小各处手写；想"把方按钮改成椭圆"要改很多地方。
2. **没有梳理过页面结构**：需要哪些页面、每页放什么、页面之间怎么跳转、操作路径是否顺畅（入口好不好找、返回/中断/失败重试三条回边是否清楚）、哪些组件该共用，全都没有成文。

## 2. 已完成（截至 2026-10-05）

| 项 | 产出 | 证据 |
|---|---|---|
| ① 设计 token 规范 | `docs/standards/frontend-component-standards.md` **§3.2 设计 token：圆角/间距/尺寸** | 三层 token（primitive→semantic→component）、唯一数据源 `src/theme/tokens.ts` 两路输出（antd ConfigProvider token + `src/index.css` CSS 变量） |
| ① 门禁脚本 | `opc-web-console/scripts/check_tokens.py` | 与既有 `check_colors.py` 同款白名单机制；默认**只报告不阻断**（`--strict` 才报错）；首跑实测全站 **334 处**尺寸字面量待迁移 |
| ② 任务域盘点 | `docs/spec/052-ui-audit-task-domain.md` | 路径清单 6 条（P1–P6）/ 往返流程图 ASCII / 页面清单表（`/tasks`、`/tasks/new`、详情层）/ 共用组件清单（22 域内 + 9 跨域 + 7 缺失基础件） |
| 登记 | `docs/INDEX.md` 1.1 与 `docs/changelog/2026-10.md` | `python scripts/check_docs.py` **零错误** |

## 3. 用户已确认的设计决策（**不要再问**）

| 项 | 取值 |
|---|---|
| 视觉基调 | **圆润**：`radius.md=8`（保持现状）、小控件 `sm=4`、容器 `lg=12`；同种组件全站只用同一档 |
| 密度 | **默认 32**（`control.heightMd`）；紧凑用 `size="small"`=24；重要操作 40 |
| 间距基数 | **4pt 基数 + 8pt 主档** |
| 页面能否覆盖 token | **默认禁止**；确需覆盖须文件头 `token-override: <理由>` 并登记 |
| 图标/控件档 | `icon{14/16/20}`、`control{24/32/40}` |

## 4. B 线的产出格式（用户指定，每域一批都必须含这四件）

1. **路径清单**：每条 = 入口 / 目标 / **成功** / **失败** / **返回** / **中断** / **重试**（三条回边必须显式写）
2. **往返流程图（ASCII）**：看清"入口 → 页面 → 子页/抽屉 → 回到哪"
3. **页面清单表**：字段 = 主对象 / 主要区块 / 入口来源 / 主操作 / 空态 / 加载态 / 异常态 / 形态（独立页 or 抽屉），并逐页判定"**是否围绕主对象展开**"
4. **共用组件清单**：已有 / 需抽出 / 需统一规格，三态标注

**可核查性要求**：每条要么标【依据】（文件:行/文件名），要么标【待审核】（推断），**不得把推断写成事实**。

## 5. 批次顺序与当前进度

| 批次 | 域 | 状态 |
|---|---|---|
| ① | 设计 token 规范 + 门禁 | ✅ 完成 |
| ②-1 | **任务域**（`TaskQueue` 70 行、`TaskNew` 760 行 + 22 组件 + 16 hooks） | ✅ 文档已出，**⏳ 等用户审核 Q1–Q5** |
| ②-2 | 节点域（`Nodes` 173、`NodeMetrics` 312、`NodeDetailDrawer`/`NodeDetailInfo`/`NodeCluster`/`NodeStatusTag`/`InstallCommandModal`；hooks `useNodeDetail`/`useNodeMetricsPolling`/`useAgentNodeList`） | ⬜ 下一批 |
| ②-3 | 设置与资源（`Settings`/`Changelog`/`ResourceManage` + `LLMModelsPanel`/`BtMcpPanel`/`SopPanel`/`InstallCommandModal`） | ⬜ |
| ②-4 | 技能与文档（`Skills`/`Docs` + `SkillStepList`/`MarkdownView`） | ⬜ |
| ②-5 | 日志与记忆（`Logs` 618 行、`Memory`、`Dashboard` + `dashboard/` 4 组件） | ⬜ |

**全站路由清单（`src/App.tsx:114-127`，共 12 条）**：`/`Dashboard、`nodes`、`nodes/:id/metrics`、`tasks`、`tasks/new`、`skills`、`docs`、`logs`、`memory`、`settings`、`settings/changelog`、`settings/resources`。
注：**全站没有 `tasks/:id`**（任务详情是抽屉）——这是任务域文档 Q1 的核心议题。

## 6. 任务域文档里待用户审核的 5 个问题（Q1–Q5）

| # | 问题 | 建议 |
|---|---|---|
| Q1 | 任务详情用**独立页 `tasks/:id`** 还是继续抽屉？ | 独立页（可分享链接/可刷新/返回语义与 `nodes/:id/metrics` 一致） |
| Q2 | Dashboard 任务表点击落点 | 直接进详情，并保留回列表筛选 |
| Q3 | `TaskSessionDrawer` 与 `TaskDetailDrawer` 职责边界 | 会话历史独立成段，不与任务信息混层 |
| Q4 | 列表/轨迹/产物的失败态是否统一"错误 + 重试" | 要（纳入 `ErrorState` 标准件） |
| Q5 | `TaskNew` 右栏三区（规范文档/任务摘要/方案）形态 | 保持并列但统一容器与空态；工具面板收起状态持久化 |

## 7. 新会话上手步骤

1. 读本文件 → 读 `docs/spec/052-ui-audit-task-domain.md`（格式范本，下一批照它的结构与标注方式写）→ 读 `docs/standards/frontend-component-standards.md §3.2` 与 `.trae/rules/{decision-documents,coding-standards,e2e-dev-loop}.md`；
2. **先问用户**：任务域 Q1–Q5 是否确认？确认后出**节点域**文档；未确认也可先出（文档里保留【待审核】标注）；
3. 产出后登记：`docs/INDEX.md` **1.1 表**追加一行（示例格式：模块名列写「前端 UI 盘点：节点域（继承 ADR-052…）」，Spec 列写该文档的 markdown 链接，Harness 列写「—（附属文档，无独立 harness）」）+ `docs/changelog/2026-10.md` 追加一条；
4. 跑校验：`$env:PYTHONIOENCODING="utf-8"; python scripts/check_docs.py` → 必须**零错误**（若报 `W1 spec 未在 INDEX 登记`，就是第 3 步漏登记）。

## 8. 已知坑（务必遵守）

| 坑 | 说明 |
|---|---|
| `check_docs.py` 在 Windows GBK 控制台会崩 | 必须 `PYTHONIOENCODING=utf-8`，否则只看到 `UnicodeEncodeError`，误判为"文档有问题" |
| 新文档必须登记 INDEX 1.1 | 否则 `check_docs.py` 报 `W1 spec 未在 INDEX 登记`（附属文档同样要登记） |
| **不要改代码** | 用户明确"不动代码，只出文档，我要审核"；审核通过后若要改，走 `.trae/rules/e2e-dev-loop.md`（前端：`tsc -b` + `vitest run` + **浏览器实测**，不构建节点二进制） |
| `Markdown.tsx` 是**手写且刻意免疫 XSS** 的渲染器 | 明确**不支持表格/嵌套列表**；若将来换 `react-markdown`，**必须加 `rehype-sanitize`**，否则等于降低安全性（AI 输出不可信） |
| 全站 334 处尺寸字面量是"待迁移清单" | 不要一次性大改；按域迁移，迁移完成的域可把该项纳入 `check_tokens.py --strict` |
| 编号会撞 | 索引里已有 ADR-066/067（另一会话推进中）。新建文档前先看 `docs/INDEX.md` 1.1/1.2，或 `Glob docs/**/0NN-*` |

## 9. 与另一条线（A 线/办公节点）的关系

A 线是 `opc-agent-office` 节点的能力与交付闭环（ADR-065，M1–M7），**与前端盘点没有代码耦合**。唯一交集是两处，B 线做盘点时要一并考虑：
1. 办公节点也要把过程展示进前端（ADR-065 M5 轨迹镜像）→ 复用 `NodeTraceBlock`；
2. 任务域文档提到的"7 个缺失基础件"（`PageHeader`/`EmptyState`/`LoadingState`/`ErrorState`/`FilterBar`/`ConfirmAction`/通用 `StatusTag`）是后续所有域共同的骨架。
