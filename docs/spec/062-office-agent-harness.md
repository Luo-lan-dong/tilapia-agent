# 办公 Agent 节点 Harness（测试清单）

- 关联 ADR：[ADR-062](../adr/062-office-agent-node.md)
- Spec：[062-office-agent-spec.md](./062-office-agent-spec.md)

## 1. 测试金字塔

```
       E2E（1 个真实链路场景）
      集成（注册→派发→执行→结果；fake LLM/搜索/抓取）
     契约（事件 schema 与进度 stage 枚举一致性）
    单元（工具循环调度 / 四工具 / 凭据 / 降级分支）
```

## 2. 单元测试（office 节点侧）

| 编号 | 用例 | 断言要点 |
| --- | --- | --- |
| U-1 | 工具循环正常收敛 | fake LLM 按脚本调用工具→最终交付；轮次/token 预算内结束 |
| U-2 | write_docx | 含标题层级/段落/表格的 docx 落盘且可被 python-docx 重新打开 |
| U-3 | write_pptx | 指定页数 pptx 落盘，页标题与大纲一致 |
| U-4 | web_search 解析 | 固定 DDG HTML 样本解析出 URL+标题+摘要；空结果返回空列表不抛异常 |
| U-5 | web_fetch 正文提取 | 本地 HTML 样本转 Markdown；`Fetcher` 抛拦截异常时升级 `StealthyFetcher`（mock） |
| U-6 | 凭据缺失快速失败 | executor_model 无 api_key → `failed(reason=missing_executor_credentials)`，不发起 LLM 调用 |
| U-7 | LLM 重试 | 两次指数退避后第三次成功；三次均败 → `failed(llm_unavailable)` |
| U-8 | 降级路径 | 搜索无结果/抓取失败时任务不中断，summary 含「未联网核实」或失败 URL 清单 |
| U-9 | 预算超限 | 轮次超限 → 尽力产出或 `failed(tool_loop_budget_exceeded)` |
| U-10 | 静默期心跳 | 长工具执行期间按 `AGENT_PROGRESS_HEARTBEAT_SECONDS` 补发进度（fake sleep 需 yield，防止忙循环） |

## 3. 集成测试

| 编号 | 用例 | 断言要点 |
| --- | --- | --- |
| I-1 | 注册+心跳 | office 节点向 fake hub 注册（agent_type=office），心跳携带 status/load，断线重连后重新注册且保留名称 |
| I-2 | 派发→执行→结果 | fake hub 下发 `hub.task_dispatch`（含 executor_model）→ 节点产出 md → `agent.task_result` deliverables 正确 |
| I-3 | 拒单自评 | 资源低于 ADR-059 双门槛 → 拒单 reason=resource_busy，日志带真实读数 |
| I-4 | 凭据仅内存 | api_key 不出现在节点日志与落盘文件 |

## 4. 契约测试（中枢侧）

| 编号 | 用例 | 断言要点 |
| --- | --- | --- |
| C-1 | planning 枚举扩充 | `VALID_AGENT_TYPES` 含 office；办公类任务描述规划输出 `agent_type='office'`（mock DeepSeek 响应） |
| C-2 | 调度匹配 | task.agent_type='office' 时 eligible 节点仅含 office 节点（复用既有 `list_eligible_nodes` 测试模式） |
| C-3 | 安装脚本矩阵 | `SUPPORTED_INSTALL_SCRIPTS` 含 ("office","linux")；install 命令端点对 office 返回脚本 |
| C-4 | 进度 stage 白名单 | office 上报的 stage 全部落在既有封闭枚举内（中枢不做额外放行） |

## 5. E2E（真实链路）

| 编号 | 场景 | 步骤与预期 |
| --- | --- | --- |
| E2E-1 | 查资料并写分析报告 | 新建对话输入「调研 <主题> 并写一份分析报告 docx」→ 规划为 office → 派发至真实 office 节点 → 任务完成 → 交付物页可下载 docx + md，docx 含引用来源章节 |
| E2E-2 | 做 PPT（冒烟） | 输入「做一份 <主题> 汇报 PPT」→ 产出 pptx（≥5 页）可下载 |

## 6. CI 门禁

- 中枢：ruff + pytest（含新增 C-1~C-4）全绿；
- 节点：ruff + pytest（U-1~U-10、I-1~I-4）全绿；
- 行数限制：单业务文件 ≤300 行（超 250 审查拆分）；
- 文件长度检查与 lint/test 同级作为合并门禁。
