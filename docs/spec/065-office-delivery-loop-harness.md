# Harness 工程 Spec：办公节点交付闭环与能力增强（ADR-065）

> 关联决策：[ADR-065](../adr/065-office-delivery-loop.md)｜自然语言 Spec：[065-office-delivery-loop-spec.md](./065-office-delivery-loop-spec.md)
> 本文定义**测试金字塔、用例清单与 CI 门禁**；用例编号规则 `UT-*`（单元）/`IT-*`（集成）/`CT-*`（契约）/`E2E-*`（端到端），完成后回填结果列。

## 1. 测试金字塔

| 层 | 目标 | 工具 | 归属 |
|---|---|---|---|
| 单元 `UT-*` | 工具/验证器/预算闸的纯逻辑（含边界与降级） | `pytest`（`opc-agent-office/tests/`） | 节点 |
| 集成 `IT-*` | 工具循环 × 工具 × 工作目录（真实读写文件，不打桩 LLM） | `pytest` + 临时工作目录 | 节点 |
| 契约 `CT-*` | 节点 ↔ 中枢事件/端点契约（轨迹上报、阻断事件、交付物清单） | `pytest`（中枢 `tests/`）+ 节点侧请求体断言 | 双端 |
| 端到端 `E2E-*` | 真实派发办公任务 → 产物可打开且判据达标 → 中枢可见过程 | 中枢派发 + 节点真实执行 + 人工/脚本核验 | 三端 |

## 2. 用例清单

### 2.1 单元（`UT-*`）

| 编号 | 模块 | 验证点 | 预期 |
|---|---|---|---|
| UT-M1-1 | M1 素材读取 | `read_docx` 读含标题/段落/表格的 docx | 返回结构化文本，表格以可解析形式保留 |
| UT-M1-2 | M1 | `read_xlsx` 读多 sheet + 空单元格 | 逐 sheet 返回，空值不丢列对齐 |
| UT-M1-3 | M1 | 素材不存在 / 非目标格式 | 返回可读错误，不抛未捕获异常 |
| UT-M2-1 | M2 | `edit_docx` 在既有文档追加一节 | 产出新文件；**原文件字节不变** |
| UT-M2-2 | M2 | `write_xlsx` 写多 sheet + 表头 | 可被 openpyxl 重新打开且数据一致 |
| UT-M2-3 | M2 | `edit_xlsx` 追加一行汇总 | 新文件含新行；原文件字节不变 |
| UT-M3-1 | M3 | docx 目录/页码/页眉页脚生成 | 产物 XML 中存在对应元素 |
| UT-M3-2 | M3 | pptx 真图表替换占位 | 幻灯片含 chart 部件且数据点与输入一致 |
| UT-M4-1 | M4 | L0 结构闸：空文件/损坏文件 | 判不过并给出条目级原因 |
| UT-M4-2 | M4 | L1 内容闸：缺关键词/含 `TODO` 占位 | 判不过且指出缺失项 |
| UT-M4-3 | M4 | 回灌只修不达标项 | 已达标段落内容不变 |
| UT-M4-4 | M4 | 第 5 轮仍不过 | 进 `verify_blocked`，不再重试 |
| UT-M5-1 | M5 | 轨迹行 seq 单调、`status/reasoning/text/tool` 分类正确 | 顺序与分类符合契约 |
| UT-M5-2 | M5 | **轨迹上报失败** | 只记 warning，**任务照常完成** |
| UT-M6-1 | M6 | Scrapling 缺失 | 显式降级 + 上报，不静默 |
| UT-M6-2 | M6 | SERP 解析（Scrapling 解析器） | 固定样本解析出标题/URL/摘要 |
| UT-M7-1 | M7 | 预算 80% / 100% 阈值 | 80%：注入「收尾纪律」系统提示 + `status` 轨迹行 + `office_budget_soft_limit` warning，**任务继续**；100%：硬停循环（`stopped_reason="token_budget"`，不再调用 LLM）+ `office_budget_hard_stop` warning，桥接层上报 `budget_hard_exceeded` 原因（**不报 completed**）；两级各只触发一次（幂等） |

> **用例结果回填（M1–M7 全量，2026-10-05 实测）**：`UT-M1-1/2/3` → **✅ 通过**，见 `opc-agent-office/tests/test_read_tools.py`；`UT-M2-1/2/3` → **✅ 通过**，见 `tests/test_edit_tools.py`（含原文件 SHA256 不变断言）；`UT-M3-1/2/3` → **✅ 通过**，见 `tests/test_output_enhance.py`（目录/页码/页眉页脚解 XML 断言、pptx chart 部件与数据点、matplotlib PNG 嵌入 docx）；`UT-M4-1/2/3/4` → **✅ 通过**，见 `tests/test_verify_gate.py`；`UT-M5-1/2/3` → **✅ 通过**，见 `tests/test_agent_trace.py`；`UT-M6-1/2/3` → **✅ 通过**，见 `tests/test_search_quality.py`（固定样本 SERP 解析、Scrapling 缺失**显式**降级并上报、引用↔段落映射）；`UT-M7-1` → **✅ 通过**，见 `tests/test_budget_guard.py`（9 例：80% 软阈值注入收尾纪律 + `status` 轨迹行 + warning 且**任务继续完成**、重复跨越不重复注入（幂等）、100% 硬停 `stopped_reason="token_budget"` 且上报 `budget_hard_exceeded`（不假装成功）、`usage` 行字段齐全、上报失败只 warning、阈值可由 `OFFICE_LLM_BUDGET_SOFT_RATIO` 覆盖、跨回灌轮累计用量）。**实测 `pytest tests -q` 162 passed / 0 failed**（M0 基线 26 → M1 50 → M2 69 → M3 87 → M4 111 → M5 128 → M6 153 → **M7 162**）；`ruff check app tests` All checks passed。`IT-*`/`CT-*`/`E2E-*` 仍需真实节点环境执行，步骤见 [办公节点验收操作单](../ops/office-agent-acceptance-runbook.md)。

### 2.2 集成（`IT-*`）

| 编号 | 验证点 | 预期 |
|---|---|---|
| IT-1 | 工具循环端到端（打桩 LLM 返回固定工具调用序列） | 循环终止、产物落工作目录、`LoopOutcome` 字段正确 |
| IT-2 | 素材读取 → 产出 → 自验证 串起来 | 三层闸按序执行，失败即回灌 |
| IT-3 | 工作目录隔离（并发两任务） | 产物互不覆盖 |

### 2.3 契约（`CT-*`）

| 编号 | 验证点 | 预期 |
|---|---|---|
| CT-1 | 轨迹上报请求体符合 `POST /api/v1/tasks/{id}/agent-trace` | `{node_id, session_id, rows:[{seq,kind,payload}]}`；重复 seq 幂等 |
| CT-2 | 阻断事件名与 payload | 事件 `agent.task_verify_blocked`，payload 含"卡在哪一条"明细 |
| CT-3 | 交付物清单回传 | `agent.task_result` 含 docx/pptx/xlsx/md 文件名与路径 |

### 2.4 端到端（`E2E-*`）

| 编号 | 场景 | 步骤 | 通过标准 |
|---|---|---|---|
| **E2E-1** | **给素材出产物 + 网络引用** | ① 中枢派发"按附件 Excel 与 Word 素材，产出一份带目录页码的 PPT + 一份汇总报告，并给出带引用的结论" ② 节点读素材 → 检索抓取 → 产出 → 自验证 → 交付 | ① 产物可被 python-pptx/python-docx **真打开**；② 幻灯片数/章节数 ≥ 判据；③ **引用 URL 可访问且能对应到正文段落**；④ checklist 逐条通过且给出证据位置；⑤ **中枢时间线能看到 status/reasoning/tool**；⑥ 原素材文件 SHA256 未变 |
| E2E-2 | 阻断决策闭环 | 派发一份"必然缺项"的 checklist 任务 | 回灌 ≤5 轮 → 进 `verify_blocked` → 前端既有决策入口可见「卡在哪一条」→ 24h 兜底可置 failed |

## 3. CI 门禁

| 门禁 | 命令 | 通过标准 |
|---|---|---|
| 节点静态检查 | `cd opc-agent-office && python -m ruff check app tests` | 全过 |
| 节点测试 | `cd opc-agent-office && python -m pytest tests -q` | 全过（基线见 Changes §2） |
| 中枢静态/测试 | `cd opc-central-dispatch-hub && python -m ruff check app tests && python -m pytest tests -q -k office` | 全过 |
| 文档一致性 | `python scripts/check_docs.py`（Windows 需 `PYTHONIOENCODING=utf-8`） | **零错误** |
| 端到端 | 按 `e2e-dev-loop.md`：构建 patch+1 → 部署 → 派发真实办公任务 | `E2E-1` 通过 |

> 每个模块完成时，回填本文件对应用例的"结果"列，并在 [Changes](./065-office-delivery-loop-changes.md) 的进度记录里登记。
