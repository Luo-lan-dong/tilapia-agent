# 开发规则文档库与可选规则包下发 Spec（ADR-048）

## 1. 功能描述

中枢提供"规则文档库"，用户在文档管理页维护开发规范文档（预置两份节点裁剪版模板，也可上传/编辑）。创建任务时用户可选择若干文档绑定到任务（版本锁定），派发时随 payload 下发一次性下载信息；Agent 节点下载后在任务工作目录落盘 `.tilacode/rules/` 并生成 `AGENTS.md` 按需加载索引，tilacode 执行时自动注入。**不选择则完全不下发，节点行为与现状一致。**

## 2. 用户故事

- **US-1 维护文档**：作为用户，我在侧边栏"文档"页可以新建、编辑（Markdown）、上传 .md、删除规范文档；系统预置 coding-standards（节点版）与 decision-documents（子 Agent 裁剪版）两份模板可直接编辑。
- **US-2 按需绑定**：作为用户，我创建任务时可以选择"本次任务需要遵守哪些规范文档"；不选则节点自由发挥。
- **US-3 版本锁定**：作为用户，我修改了文档后，已创建的任务仍按创建时的版本执行，改派/重跑一致。
- **US-4 节点按需加载**：作为节点上的执行 AI，我开工时 AGENTS.md 会告诉我有哪些规范、各自"何时必须加载"，我只读本次动工相关的那几份。
- **US-5 AI 感知**：作为中枢对话 AI，我可以通过 `list_standard_docs` 技能查询文档库清单，在编排时向用户介绍可用规范。

## 3. 核心流程

```
┌─────────────── 中枢 ───────────────┐      ┌──────────── 节点（Agent）────────────┐
│                                    │      │                                      │
│ [文档管理页] 新建/编辑/上传/删除      │      │                                      │
│      │ standard_docs 表（固化本地） │      │                                      │
│      ▼                            │      │                                      │
│ [创建任务] 选 rule_doc_ids          │      │                                      │
│      │ tasks.rule_docs=[{doc_id,  │      │                                      │
│      │   version}] 版本锁定        │      │                                      │
│      ▼                            │      │                                      │
│ [派发 scheduler]                   │      │                                      │
│   读锁定版本内容 → sha256           │      │                                      │
│   payload.rule_docs=[{id,name,     │─────►│ hub_client 收到派发                   │
│     file_name,version,apply_when,  │      │   │ 下载 GET /tasks/dispatch/rule-docs│
│     url(token),sha256,size}]       │      │   │ sha256 校验（失败重试1次→warning） │
│                                    │      │   ▼                                  │
│ [下载端点] 校验 token 签名          │◄─────│ task_bridge：workspace 落盘           │
│   读 DB 返回文件流（即用即焚语义）    │      │   .tilacode/rules/<file_name>        │
│                                    │      │   AGENTS.md（索引：file_name+        │
│                                    │      │     apply_when，按需加载指令）        │
│                                    │      │   ▼                                  │
│                                    │      │ tilacode 自动注入 AGENTS.md 执行任务  │
└────────────────────────────────────┘      └──────────────────────────────────────┘
```

## 4. 数据与接口设计

### 4.1 `standard_docs` 表

> 实施修订：`tasks.rule_docs` 采用**内容快照**（含全文），版本锁定语义更强，见 4.2。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID PK | |
| name | VARCHAR(100) NOT NULL UNIQUE | 文档名（同时是展示名） |
| category | VARCHAR(50) NULL | 分类（如 coding-standard / decision / custom） |
| description | VARCHAR(500) NULL | 用途说明（AI 介绍时使用） |
| apply_when | VARCHAR(500) NULL | 何时必须加载（节点端索引用） |
| content | TEXT NOT NULL | Markdown 全文 |
| version | INT NOT NULL DEFAULT 1 | 每次更新 +1 |
| is_builtin | BOOLEAN DEFAULT FALSE | 预置模板标记 |
| created_at / updated_at | TIMESTAMP | |

### 4.2 `tasks` 表扩展

- `rule_docs JSONB NULL`：`[{"doc_id": "...", "name": "...", "version": 1, "apply_when": "...", "content": "全文"}]`；NULL/空 = 不下发。
- **内容快照**：创建任务时锁定全文快照，文档后续迭代/删除不影响已建任务，派发零回查。

### 4.3 中枢 API

| 端点 | 说明 |
| --- | --- |
| `GET /api/v1/docs` | 文档列表（元数据，不含 content） |
| `GET /api/v1/docs/{id}` | 文档详情（含 content） |
| `POST /api/v1/docs` | 创建（JSON：name/category/description/apply_when/content） |
| `PUT /api/v1/docs/{id}` | 更新（version+1） |
| `DELETE /api/v1/docs/{id}` | 删除（已建任务使用内容快照不受影响） |
| `POST /api/v1/tasks`（扩展） | 新增可选 `rule_doc_ids: [UUID]`，创建时锁定版本 |
| `GET /api/v1/docs/download/rule-doc?token=` | 下载端点（token + TTL 600s，**TTL 内可重复下载**支持 Agent 重试），返回文件流，非统一响应格式 |

### 4.4 派发 payload 扩展

```json
"rule_docs": [
  {
    "id": "uuid",
    "name": "coding-standards",
    "file_name": "coding-standards.md",
    "version": 3,
    "apply_when": "每次写/改代码前",
    "url": "/api/v1/tasks/dispatch/rule-docs",
    "sha256": "...",
    "size": 4096
  }
]
```

- `file_name` 由中枢按 name 净化生成（仅 `[A-Za-z0-9._-]`），Agent 端落盘时再取 basename 双重防越界。
- 运行时字段 `rule_doc_blobs`（不序列化、不进日志），仿 `attachment_blobs`。

### 4.5 节点端 AGENTS.md 模板（Python 生成）

```markdown
# 项目开发规则（中枢下发，禁止手工改写本文件）

## 核心原则
本次任务附带开发规范文档，存放于 `.tilacode/rules/`。
**动工任何部分前，先按下方索引判断需要加载哪些文档，只读相关的那几份，不要全量阅读。**

## 规范索引
| 文档 | 何时必须加载 |
| --- | --- |
| coding-standards.md | 每次写/改代码前 |
| ... | ... |

## 加载流程
1. 识别本次要动工的部分 → 对照索引确定文档
2. 读取并遵守对应规范
3. 若下发了 decision-documents：重大偏离需上报并留痕简版决策文档至 docs/；小修小补不写决策文档
```

## 5. 降级链路

| 场景 | 处理 |
| --- | --- |
| 规则文档下载失败/校验失败 | 重试 1 次（TTL 内 token 可重复下载）→ 仍失败 warning 日志 + task_progress 提示，**任务照常执行** |
| 快照后绑定的文档被更新/删除 | 无影响（内容快照语义），派发直接读任务行 |
| 旧版节点收到 `rule_docs` 字段 | Pydantic 忽略未知字段，行为不变 |
| 下载端点 token 过期/无效 | 401 |
| tilacode 不支持 AGENTS.md 注入（实施验证失败） | 回退方案：`_build_prompt` 文本中注入"先读 AGENTS.md 索引"指令（不改协议） |

## 6. 验收标准

1. 文档管理页全流程可用（增删改查+预览+上传），预置两份模板存在。
2. 不选规则包 → payload 无 `rule_docs`，节点工作目录无规则文件（回归）。
3. 选规则包 → 版本锁定；文档更新后已建任务派发仍用旧版本内容。
4. Agent 端 `.tilacode/rules/` 落盘 sha256 一致，`AGENTS.md` 含全部文档索引。
5. 下载失败 → warning + 任务继续不失败。
6. `list_standard_docs` 技能在对话中可调用并返回清单。
7. 存量测试全过（hub + agent），新增测试覆盖上表流程。
