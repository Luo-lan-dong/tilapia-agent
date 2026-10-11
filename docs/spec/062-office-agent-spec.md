# 办公 Agent 节点（opc-agent-office）Spec

- 关联 ADR：[ADR-062](../adr/062-office-agent-node.md)
- Harness：[062-office-agent-harness.md](./062-office-agent-harness.md)
- Changes：[062-office-agent-changes.md](./062-office-agent-changes.md)

## 1. 功能描述

新增办公 Agent 节点类型（`agent_type='office'`），承担三类任务：

1. **写文档**：根据任务描述/大纲生成 `.docx`（标题层级、段落、表格，必要处配图占位）；
2. **做 PPT**：根据主题/大纲生成 `.pptx`（封面、目录、内容页、总结页）；
3. **查资料**：联网搜索 → 抓取正文 → 提炼汇总（可独立成任务，也作为文档/PPT 任务的前置环节）。

Markdown（`.md`）作为中间稿/源稿附带产出。任务由中枢任务规划（DeepSeek）判定 `agent_type=office` 后进入与编程任务相同的派发/执行/交付链路。

## 2. 用户故事

- 作为用户，我在新建对话输入「帮我调研 XX 并写一份分析报告（docx）」，任务被规划为 office 类型并派发给办公节点，完成后我在任务详情/交付物页下载 docx 与 md。
- 作为用户，我输入「做一份 XX 主题的 PPT」，office 节点产出 pptx 交付物。
- 作为用户，我在任务详情实时看到 office 节点的当前阶段（规划大纲/查资料/编写中/汇总提炼）与进度，无活动时也有静默期心跳。
- 作为运维，我在节点管理页看到办公节点的类型徽标、负载与状态，安装方式与编程节点一致（安装命令一键复制）。

## 3. 核心流程

```
用户输入办公任务（新建对话）
        │
        ▼
中枢任务规划（DeepSeek，agents/planning.py）
  判定 agent_type='office'（提示词新增判据）
        │
        ▼
调度器（零改动）：task.agent_type='office'
  → list_eligible_nodes 匹配在线 office 节点（ADR-032 双门槛预筛）
  → 候选唯一直接派发；多个走 AI 白名单决策（ADR-033）
        │
        ▼
WS hub.task_dispatch
  payload: task_id/description/specification/output_dir
           + executor_model{model, api_key, base_url}（ADR-029，仅内存）
        │
        ▼
office 节点执行器（工具循环，LLM 函数调用）
  ┌────────────────────────────────────────────┐
  │ 1. 规划大纲（planning）                     │
  │ 2. 需要资料 → web_search（DDG HTML，免Key） │
  │    → web_fetch（Scrapling 抓正文→Markdown）│
  │    → 提炼（summarizing，循环直至资料充分）  │
  │ 3. 编写产物（coding）：write_docx / write_pptx │
  │ 4. 产物写入 output_dir + 交付物清单         │
  └────────────────────────────────────────────┘
        │  agent.task_progress（stage 枚举 + 静默期心跳，ADR-055）
        ▼
agent.task_result：deliverables[{name, path, size}] + summary
        │
        ▼
中枢：任务完成落库 → 交付物聚合页/任务详情展示（增量产物同步 ADR-059）
```

## 4. 降级链路

| 场景 | 行为 |
| --- | --- |
| executor_model 无 api_key（中枢未配 Key） | 节点直接 `failed`，reason=`missing_executor_credentials`，用户侧可见明确原因；不重试 |
| LLM 单次调用失败 | 指数退避重试 2 次；仍失败 → `failed`，reason=`llm_unavailable` |
| web_search 无结果/被限流 | 换检索页重试一次；仍无结果 → 告知 LLM 无外部资料，仅凭任务描述产出（summary 注明「未联网核实」） |
| web_fetch 被反爬拦截 | Fetcher 失败 → 升级 StealthyFetcher（若已安装）；仍失败 → 跳过该 URL 继续，交付物 summary 列出失败清单 |
| 工具循环超轮次/token 预算 | 终止循环，以已有中间稿尽力产出；无法产出 → `failed`，reason=`tool_loop_budget_exceeded` |
| 节点资源不足 | ADR-059 准入自评（可用内存>1G 且 load_score<80）拒单，reason=`resource_busy`，中枢 5 分钟回避后改派 |
| 无在线 office 节点 | 任务 pending 排队（10s 重扫，ADR-059）；超时阈值后标记失败 |

## 5. 边界与第一期不做

- 不做 PDF/Excel 产出（python-docx/pptx 之外的第二期评估）；
- 不做 Windows 安装脚本（Linux 先行）；
- 不做手动指定 agent_type 的前端 UI（任务类型由规划判定）；
- 不做第三方接入协议正式化 / protocol_version（用户决策暂缓）；
- 图片素材：文档/PPT 第一期不生成真实图片，仅文字排版（视觉模型配图后续评估）。

## 6. 验收标准

1. 中枢节点管理页可见 `office` 类型节点（中文名「办公代理」），注册/心跳/负载展示正常；
2. 新建对话输入办公任务（写文档/做PPT/查资料各一例），规划结果 `agent_type='office'` 且自动派发到 office 节点；
3. 三类任务各产出约定交付物（docx / pptx / 汇总 md），可从交付物页下载且内容与任务要求相符；
4. 任务详情实时展示 stage/progress/message，无卡死假象（静默期心跳生效）；
5. 凭据缺失场景任务快速失败且原因可见；搜索/抓取降级场景任务不中断；
6. 全链路 pytest（中枢）+ 节点单测/集成测试通过，E2E 场景（Harness §E2E-1）跑通。
