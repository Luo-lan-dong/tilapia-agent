# SOP 动态固化 Harness（ADR-051）

## 单元测试

| 编号 | 文件 | 用例 | 断言 |
| --- | --- | --- | --- |
| UT-S1 | test_sop_service.py | seed 存在 | 五类 builtin 要点在库 |
| UT-S2 | test_sop_service.py | find_by_scene 命中 | enabled 优先，hit_count+1 |
| UT-S3 | test_sop_service.py | save 同名拒绝 | NAME_DUPLICATE |
| UT-S4 | test_sop_service.py | list 搜索过滤 | keyword/scene/source |
| UT-G1 | test_bt_deploy_prompts.py | 精简结构 | _PITFALLS 存在；DEPLOY_SOPS/_PYTHON_SOP 等手把手常量已删除；get_deploy_sop 返回含坑地图 |
| UT-G2 | test_bt_deploy_prompts.py | 无库命中 | 返回"无固化 SOP"提示 + 坑地图 |
| UT-G3 | test_bt_mcp_skills.py | guide 命中 seed | summary 含类型要点 + hit_count 递增 |
| UT-G4 | test_sop_skills.py | sop_save 成功/同名/缺参 | 入库 learned / 拒绝 / 指引 |
| UT-A1 | test_sop_api.py | CRUD + 404/重名 | 200/201/404(209001)/409(209002) |
| UT-R1 | test_bt_call_guard.py 回归 | 白名单防线不变 | php 放行 Site 族、html 拒 Site 族 |

## 集成/E2E

| 编号 | 用例 |
| --- | --- |
| IT-1 | Alembic upgrade/downgrade（sop_records + seed 建删） |
| E2E-1 | 部署跑通 → 固化询问 → 确认 → /memory SOP Tab 可见 → 下次部署命中 |

## CI 门禁

ruff format+check；新增测试全过；既有测试无回归；文件 ≤600 行。
