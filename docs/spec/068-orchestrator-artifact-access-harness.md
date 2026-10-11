# 068 编排 AI 产物感知与文件访问 Harness

> 关联 ADR：[ADR-068](../adr/068-orchestrator-artifact-access.md) ｜ Spec：[068-orchestrator-artifact-access-spec.md](./068-orchestrator-artifact-access-spec.md) ｜ Changes：[068-orchestrator-artifact-access-changes.md](./068-orchestrator-artifact-access-changes.md)

## 1. 测试金字塔

```
        E2E（1）          唤醒轮部署场景端到端（真实产物 + 宝塔沙箱/跳过外传）
       ─────────────
      集成/契约（IT）     技能 × DB × 文件系统组合（tmp_path 产物目录）
     ───────────────────
    单元（UT）            路径解析 / 白名单 / 打包 / 解码判定 / hub_note 指引
   ───────────────────────
```

CI 门禁：UT + IT 全绿（`pytest tests/ -k "artifact_access or bt_call_guard or artifact_service"`），
ruff 通过；E2E 手动触发（依赖真实节点与宝塔环境）。

## 2. 单元测试清单（UT）

| ID | 对象 | 场景 | 期望 |
|---|---|---|---|
| UT-1 | list_artifacts | 会话无关联任务 | 失败 summary 含「无关联任务」指引 |
| UT-2 | list_artifacts | 产物目录不存在 | 失败 summary 含「无产物」指引 |
| UT-3 | list_artifacts | export.zip 存在 + manifest 3 文件 | summary 含 export.zip 大小 + 3 条清单 |
| UT-4 | list_artifacts | manifest 缺失但目录有散件 | 降级为目录扫描列举 |
| UT-5 | list_artifacts | 清单超上限 | 截断 + 提示总数 |
| UT-6 | list_artifacts | export.zip 缺失 + build_zip=true + 文件树有件 | 打包成功 + summary 含**不完整性警告** |
| UT-7 | list_artifacts | build_zip=true + 文件树为空 | 拒绝：无可打包缓存 |
| UT-8 | list_artifacts | export.zip 已存在 + build_zip=true | 不覆盖，提示已存在 |
| UT-9 | read_file | 产物内 README.md | 返回全文 |
| UT-10 | read_file | 产物内二进制（bytes 非法 UTF-8） | 「二进制文件不支持文本读取」 |
| UT-11 | read_file | 产物内 >2MB 文本 | 截断 + 提示 |
| UT-12 | read_file | 路径含 `..` / 绝对路径 / 反斜杠 | 拒绝（越界） |
| UT-13 | read_file | 白名单外路径（中枢配置文件等） | 「超出可读范围」+ 三类范围说明 |
| UT-14 | read_file | 会话附件目录内文件 | 返回全文 |
| UT-15 | read_file | output_dir 内相对路径 | 返回全文 |
| UT-16 | _resolve_artifact_path | file_name="export.zip" + 产物存在 | 命中产物 zip 绝对路径 |
| UT-17 | _resolve_artifact_path | file_name="src/main.py" + 文件树有件 | 命中文件树文件 |
| UT-18 | _resolve_artifact_path | 附件库命中（现状回归） | 仍优先附件库 |
| UT-19 | _resolve_artifact_path | 全部未命中 | None + hub_note 指引 list_artifacts |
| UT-20 | artifact_service.build_zip_from_cache | 散件打包 | zip 内含全部已同步文件 |
| UT-21 | artifact_service.build_zip_from_cache | 空缓存 | RuntimeError |
| UT-22 | 唤醒消息 | build_wake_message 含产物指引文本 | 含 list_artifacts / Upload 指引 |

## 3. 集成/契约测试清单（IT）

| ID | 对象 | 场景 | 期望 |
|---|---|---|---|
| IT-1 | BtMcpCallSkill × Upload | next_action + 产物命中 | 代执行成功（monkeypatch subprocess）+ hub_note 含服务器路径 |
| IT-2 | BtMcpCallSkill × Upload | next_action + 全部未命中 | 不执行 + hub_note 指引 |
| IT-3 | list_artifacts × SkillContext | session_id → session.task_id → 产物目录 | 全链路命中 |
| IT-4 | read_file × SkillContext | output_dir 未绑定时第三类跳过 | 前两类仍可用 |
| IT-5 | registry | 新技能注册 | to_openai_tools 含 list_artifacts / read_file |
| IT-6 | 打包兜底端内 | build_zip 后 get_cached_zip_path 命中 | Upload 可直接使用新包 |

## 4. E2E 测试场景

| ID | 场景 | 步骤 | 通过标准 |
|---|---|---|---|
| E2E-1 | 唤醒轮自主部署 | 真实任务完成（产物已同步）→ 触发唤醒 → 对 AI 说「部署到 <服务器>」→ 观察轨迹 | AI 依次出现 list_artifacts →（可选 read_file）→ bt_deploy_guide → Upload 调用；Upload 步骤 hub_note 含服务器落盘路径；无需用户提示产物位置 |

## 5. 回归范围

- `tests/` 既有 bt_mcp 全套（ADR-049/050/051）、artifact_service、orchestrator_wake 全绿；
- `ruff check` 涉及文件零告警。
