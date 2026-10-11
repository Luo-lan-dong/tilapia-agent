# SOP 动态固化 Spec（ADR-051）

> 部署知识从代码常量迁入记忆体系：坑地图常量打底 + 类型要点 seed + 跑通后固化的动态 SOP。

## 1. 功能描述

`bt_deploy_guide` 返回精简为「行为约束 + 库内命中 SOP + 通用坑地图 + 场景白名单」；部署流程跑通后 AI 询问用户是否固化，同意后经 `sop_save` 技能入库（learned）；「记忆」页新增 SOP Tab 管理全部 SOP（builtin/learned）。

## 2. 用户故事

- **US-1 轻量指引**：AI 部署前读到的指引 ≤1.5K token（坑地图+命中 SOP），无手把手流程与判定表，模型按 schema/工具目录自主编排。
- **US-2 自主判型**：AI 解压产物后按特征自行判定项目类型（判不准问用户），不依赖硬编码映射。
- **US-3 跑通固化**：部署成功后 AI 询问"是否固化为 SOP"，用户同意则 `sop_save` 入库。
- **US-4 SOP 管理**：用户在记忆页 SOP Tab 查看/搜索/编辑/启停/删除/新建 SOP。
- **US-5 命中复用**：同场景下次部署自动命中该 SOP 并累计 hit_count。

## 3. 流程

```text
bt_deploy_guide(project_type)
   ├─ 行为约束常量（精简版）
   ├─ sop_records 命中（scene 精确 + enabled）→ 注入 content + hit_count+1
   ├─ 通用坑地图 _PITFALLS（常量）
   └─ 场景白名单（防线，保留）

部署跑通 → AI 询问固化 → 用户同意 → sop_save(name, scene, content)
   → 库（source=learned，同名拒绝）→ /memory SOP Tab 可见可管理
```

## 4. 数据模型（`sop_records`）

id UUID PK / name VARCHAR(100) UNIQUE / scene VARCHAR(30) / content TEXT / source VARCHAR(16) builtin|learned / enabled BOOLEAN default true / hit_count INTEGER default 0 / created_at / updated_at。迁移 seed 五条 builtin 类型要点（html/python/nodejs/go/php）。

## 5. 接口（`/api/v1/sops`，错误码 209001 NOT_FOUND / 209002 NAME_DUPLICATE）

GET 列表（keyword/scene/source 过滤）/ POST 创建 / GET /{id} / PATCH / DELETE。

技能：`sop_save`（name/scene/content，同名拒绝）；`bt_deploy_guide` 改造读库。

## 6. 验收标准

1. bt_deploy_guide(html)：含坑地图 + html 类型要点 seed，hit_count+1；不含手把手流程与判定特征表；总长 ≤1.5K token 量级。
2. sop_save 同名拒绝、成功入库 learned；下次 guide 命中注入。
3. /memory SOP Tab 五项操作可用；builtin 也可编辑/删除（用户自治）。
4. SCENE_TOOL_WHITELISTS 防线行为不变（php 放行 Site 族）。
5. 迁移可升级可回滚（seed 随降级删除）。
