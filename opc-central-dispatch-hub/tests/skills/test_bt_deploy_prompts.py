"""部署指引与场景白名单测试（ADR-051 精简结构 + ADR-050 防线）。"""

import app.skills.bt_deploy_prompts as prompts
from app.services.bt_mcp_risk import TOOL_CATEGORIES
from app.skills.bt_deploy_prompts import (
    PITFALLS,
    SCENE_TOOL_WHITELISTS,
    detect_scene_from_messages,
    get_deploy_guide_text,
    whitelist_section,
)


def test_whitelists_cover_all_scenes() -> None:
    """UT-W1：五场景白名单齐备，各含本族工具。"""
    assert set(SCENE_TOOL_WHITELISTS) == {"python", "nodejs", "go", "html", "php"}
    assert "PythonProjectCreate" in SCENE_TOOL_WHITELISTS["python"]
    assert "NodeProjectCreate" in SCENE_TOOL_WHITELISTS["nodejs"]
    assert "GoProjectCreate" in SCENE_TOOL_WHITELISTS["go"]
    assert "HtmlProjectCreate" in SCENE_TOOL_WHITELISTS["html"]
    assert "SiteCreate" in SCENE_TOOL_WHITELISTS["php"]
    assert "DatabaseCreate" in SCENE_TOOL_WHITELISTS["php"]


def test_php_scene_allows_site_family_only_for_php() -> None:
    """php 场景白名单含 PHP 网站管理族；其余场景仍排除（回归）。"""
    for scene in ("python", "nodejs", "go", "html"):
        for banned in ("SiteList", "SiteCreate", "SiteGetConfig", "SiteDelete"):
            assert banned not in SCENE_TOOL_WHITELISTS[scene], scene


def test_whitelist_tools_known_in_catalog() -> None:
    """UT-W3：白名单工具均存在于工具目录映射（防手写错名）。"""
    for scene, tools in SCENE_TOOL_WHITELISTS.items():
        unknown = tools - set(TOOL_CATEGORIES)
        assert not unknown, f"场景 {scene} 含未知工具：{unknown}"


def test_guide_slimmed_structure() -> None:
    """UT-G1（ADR-051）：手把手 SOP 与判定表已删除，坑地图存在。"""
    for legacy in ("_PYTHON_SOP", "_NODEJS_SOP", "_GO_SOP", "_HTML_SOP", "_PHP_SOP", "DEPLOY_SOPS"):
        assert not hasattr(prompts, legacy), legacy
    assert "工具族互斥" in PITFALLS
    assert "端口范围不合法" in PITFALLS
    assert "site_name" in PITFALLS


def test_guide_with_scene_sop_hit() -> None:
    """UT-G2：命中场景（库内 SOP 传入）→ 场景要点 + 坑地图 + 白名单。"""
    text = get_deploy_guide_text("html", "- SPA 需 set_run_path 指向 dist")
    assert "## HTML 静态项目部署（场景要点）" in text
    assert "set_run_path" in text
    assert "实测坑清单" in text
    assert "本场景工具白名单" in text
    assert "部署约束" in text
    assert "项目类型自主判定" in text
    # 精简后不再有判定特征表（模型自主判型）
    assert "package.json→nodejs" not in text


def test_guide_without_scene_sop() -> None:
    """UT-G2b：场景无固化 SOP → 提示自主编排并引导固化。"""
    text = get_deploy_guide_text("html", None)
    assert "暂无该场景的固化 SOP" in text
    assert "跑通后可询问用户固化" in text
    assert "实测坑清单" in text


def test_guide_overview_without_type() -> None:
    """未指定类型：返回概览并引导模型自主判型。"""
    text = get_deploy_guide_text(None)
    assert "未指定项目类型" in text
    assert "自主" in text
    assert whitelist_section(None) == ""


def test_detect_scene_from_messages() -> None:
    """场景识别：从会话消息中的指引标题判断当前部署场景。"""
    messages = [
        {"role": "tool", "content": "…\n## HTML 静态项目部署（场景要点）\n…"},
        {"role": "assistant", "content": "好的"},
    ]
    assert detect_scene_from_messages(messages) == "html"
    assert detect_scene_from_messages([{"role": "user", "content": "部署"}]) is None
