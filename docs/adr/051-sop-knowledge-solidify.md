# ADR-051：部署 SOP 动态固化与坑地图精简（知识从代码常量迁入记忆体系）

- **状态**：Accepted（2026-09-26 用户确认方案并同日实施）
- **日期**：2026-09-26
- **关联**：ADR-050（经验记忆体系，本决策是其"结论层"的延伸——SOP 成为第三类知识条目）、ADR-049（bt_deploy_guide 技能）、[排查记录 2026-09-26](../issues/2026-09-26-思考工具堆叠与宝塔静态站不可见-排查记录.md)

## 1. 问题背景

用户对 ADR-050 实施后的部署知识架构提出三点质疑：
1. **硬编码不可扩展**：五类 SOP（python/nodejs/go/html/php）全部写死在 `bt_deploy_prompts.py` 代码常量里，新项目类型（.NET/Java/容器化等）无法覆盖，每次扩展都要改代码；
2. **过度教学**：类型判定特征表（package.json→nodejs 等）是模型通用常识，写死反而框死模型、浪费 token；手把手流程步骤同理——模型靠 `bt_mcp_schema` + 工具目录本可自主编排；
3. **token 膨胀风险**：`bt_deploy_guide` 按需返回已 2.5K token，类型继续增加会到 4-5K。

## 2. 决策（Decision）

### 2.1 知识分层重构

| 层 | 载体 | 内容 | 变更 |
| --- | --- | --- | --- |
| 行为约束 | 代码常量（精简） | 规划先行、参数逐字一致、15 次上限、高危确认 | 保留精简 |
| 通用坑地图 | 代码常量（新增 `_PITFALLS`，~500 token） | 模型不可能自行知道的实测坑：参数名差异、端口写法、工具族互斥、prep_status 轮询、env 缺陷、绑定域名两步等 | 新增，**取代五份手把手 SOP** |
| 类型要点 | `sop_records` 表 seed（builtin） | 五类各 2-4 行关键差异点（html set_run_path、python analyze_only+轮询、nodejs env 缺陷、go 同步启动、php 版本/建库） | 迁移 seed |
| 场景/项目 SOP | `sop_records`（learned） | **跑通后固化**：AI 询问用户 → `sop_save` 技能入库 → 下次同场景命中 | 新闭环 |

类型判定：**模型自主**（解压产物看特征，判不准问用户），不再写特征映射表。

### 2.2 SOP 固化闭环（用户定义的产品形态）

```
部署流程跑通 → AI 询问"是否固化为 SOP？"
   → 用户同意 → AI 调用 sop_save(name, scene, content) 入库（source=learned）
   → 「记忆」页新增 SOP Tab 管理（查看/编辑/启停/删除）
   → 下次 bt_deploy_guide(project_type) 命中同场景 SOP → 注入 + hit_count+1
```

### 2.3 数据表 `sop_records`

id UUID PK / name VARCHAR(100)（唯一）/ scene VARCHAR(30)（场景标识，如 html/php/自定义）/ content TEXT（Markdown）/ source VARCHAR(16)（builtin/learned）/ enabled BOOLEAN / hit_count INTEGER / created_at / updated_at。

### 2.4 bt_deploy_guide 新返回结构

行为约束（精简）→ 库内命中 SOP（scene 精确匹配，enabled，hit_count+1；无命中提示"无固化 SOP，按 schema 与工具目录自主编排，跑通后可固化"）→ 通用坑地图 → 场景白名单 → 排障精要。

### 2.5 管理 API 与前端

`/api/v1/sops` CRUD（错误码 209xxx：SOP_NOT_FOUND/NAME_DUPLICATE）；「记忆」页改为 Tabs（经验记忆 | 部署 SOP）：SOP 列表（name/scene/来源/启用/hit_count）+ 编辑/启停/删除/新建。

## 3. 备选方案（Considered Options）

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| 维持五类 SOP 常量现状 | ❌ 否决 | 硬编码不可扩展、过度教学、token 膨胀（用户否决） |
| 只删判定表保留 SOP 常量 | ❌ 否决 | 未解决硬编码与固化诉求 |
| SOP 存 standard_docs（ADR-048 规则文档库） | ❌ 否决 | 定位不同：规则文档是开发规范下发节点；SOP 是中枢部署知识，且需 scene 命中/hit 统计/固化闭环 |
| 模型全自主（不留坑地图） | ❌ 否决 | 实测坑（IP:8080 报错/site_name 差异/env 缺陷）模型无法自行知道，坑地图是唯一不可替代层 |

## 4. 影响分析

- **中枢**：sop_records 表 + 迁移（含五类 seed）、sop_service、bt_deploy_guide 改造（读库）、sop_save 技能、/sops CRUD（209xxx）、bt_deploy_prompts 精简（删五份 SOP 常量与判定表）。
- **前端**：记忆页 Tabs 改造 + SOP 管理。
- **token**：bt_deploy_guide 从 ~2.5K → ~1.2K（坑地图+命中 SOP）；长尾类型零常驻成本。
- **风险**：learned SOP 质量参差——AI 固化前需用户确认内容；/memory 可编辑纠正。

## 5. 验收要点

1. bt_deploy_guide(html) 返回坑地图 + html 类型要点（seed 命中，hit_count+1）；不再有手把手流程与判定特征表。
2. 对话跑通部署后 AI 询问固化 → 用户同意 → sop_save 入库（learned）→ 下次命中注入。
3. /memory SOP Tab：列表/编辑/启停/删除/新建可用；builtin 可编辑，删除不限制（用户自治）。
4. 白名单软提示（SCENE_TOOL_WHITELISTS 代码防线）不受影响；php 场景仍放行 Site 族。
5. 迁移可升级可回滚（seed 随 downgrade 删除）。

## 6. 实际实施情况（2026-09-26~27 实施完成）

按决策全量落地（文件/函数级明细见 [051-sop-knowledge-solidify-changes.md](../spec/051-sop-knowledge-solidify-changes.md)）：

1. **知识分层重构**：bt_deploy_prompts.py 删五类手把手 SOP 与类型判定表，新增 `_PITFALLS` 通用坑地图（~1.2K token）与 `get_deploy_guide_text(project_type, scene_sop)`；
2. **存储层**：SopRecord 模型 + 迁移 20260926_sop_records（含五类 builtin seed，可升级可回滚）+ SopService（scene 命中计数 / 同名 209002 / CRUD）；
3. **固化闭环**：SopSaveSkill（技能注册 22 个）→ /sops CRUD（209001/209002）→ 前端记忆页 Tabs（经验记忆 | 部署 SOP：列表/搜索/编辑/启停/删除/新建，tsc+build 过）；
4. **后续实测修复（2026-09-27，本决策链条内）**：
   - Upload 工具描述误导（AI 读宝塔原文"必须本机执行 curl"后放弃上传并臆造结果）→ 目录附加中枢代执行环境说明 + 坑地图首条纠正；
   - **tool_call_logs 的 kind 白名单缺 `bt_mcp_call`，宝塔工具审计从未落库**（ADR-049 验收点 5 实为失效）→ 迁移 20260927_bt_mcp_call_kind 补 CheckConstraint；
   - **BtMcpCallSkill 的 maybe_run_local_command 调用点在本次大改中被顶掉**（Upload 代执行未生效根因，grep 磁盘取证确认）→ 补回并 grep+pytest 双重验证；
   - 工具 summary 补 `返回数据：{...}`（data 字段截断 1500，修复 AI 看不到命令输出/站点信息）；
   - 工具预算 15→40 次 + 明确 Bash 20s 转后台等待语义（BashStatus 阻塞等待，长任务须核验完成再推进）。

回归：测试 50/50（prompts 重写 / mcp 技能适配 / sop 新增 7 项）+ bt_call_guard 37/37（含 Upload 代执行 3 项）；迁移已应用；后端已重启。
