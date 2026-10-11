"""办公节点工具直测脚本：绕过中枢派发，本地验证各项能力（省 token）。

用法（在 opc-agent-office 目录，.env 已配置 WORKSPACE_ROOT 等）：
    python scripts/test_tools.py search "DuckDuckGo 特点"        # 联网搜索（零 LLM token）
    python scripts/test_tools.py fetch https://yydsfeng.com/     # 抓取指定页面（零 LLM token）
    python scripts/test_tools.py docx                            # 生成测试 docx（零 LLM token）
    python scripts/test_tools.py pptx                            # 生成测试 pptx（零 LLM token）
    python scripts/test_tools.py loop "调研 https://yydsfeng.com/ 并写一篇简介文档" \\
        --api-key sk-xxx --base-url https://api.deepseek.com --model deepseek-chat
    # loop = 完整工具循环（与中枢派发的执行路径一致；消耗 LLM token，
    # 凭据从中枢「系统设置→模型管理」复制，或用环境变量
    # TEST_LLM_API_KEY / TEST_LLM_BASE_URL / TEST_LLM_MODEL 提供）

退出码：0=全部通过；1=存在失败。
"""

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

# 保证以任意工作目录启动都能导入 app 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import AgentConfig  # noqa: E402
from app.tools.docx_tool import DocxTool  # noqa: E402
from app.tools.fetch_tool import WebFetchTool  # noqa: E402
from app.tools.pptx_tool import PptxTool  # noqa: E402
from app.tools.search_tool import WebSearchTool  # noqa: E402


def _load_config() -> AgentConfig:
    """加载节点配置（.env 缺 AGENT_REGISTRATION_TOKEN 时注入占位值）。"""
    os.environ.setdefault("AGENT_REGISTRATION_TOKEN", "local-test")
    return AgentConfig(_env_file=None)


def _work_dir() -> Path:
    """测试产物目录：WORKSPACE_ROOT/_tool_test。"""
    config = _load_config()
    work_dir = Path(config.workspace_root) / "_tool_test"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def cmd_search(args: argparse.Namespace) -> bool:
    """直测联网搜索（多后端）。"""
    tool = WebSearchTool(_work_dir(), 8, timeout=20.0, proxy=_load_config().office_proxy_url)
    start = time.monotonic()
    result = tool.execute({"query": args.query})
    print(f"[web_search {time.monotonic() - start:.1f}s]\n{result}")
    return "不可用" not in result[:60] or "找到" in result


def cmd_fetch(args: argparse.Namespace) -> bool:
    """直测网页抓取。"""
    tool = WebFetchTool(_work_dir(), timeout=45.0, proxy=_load_config().office_proxy_url)
    start = time.monotonic()
    result = tool.execute({"url": args.url})
    print(f"[web_fetch {time.monotonic() - start:.1f}s]\n{result[:1500]}")
    ok = result.startswith("已抓取")
    print(f"\n==> {'通过' if ok else '失败'}")
    return ok


def cmd_docx(_: argparse.Namespace) -> bool:
    """直测 docx 生成（含中文与表格）。"""
    tool = DocxTool(_work_dir())
    result = tool.execute(
        {
            "filename": "直测样例.docx",
            "title": "工具直测样例文档",
            "blocks": [
                {"type": "heading", "level": 1, "text": "第一章 编写网页抓取简介"},
                {"type": "paragraph", "text": "验证中文字体渲染：开源网页抓取、开发者维护、许可协议。"},
                {"type": "bullet", "text": "要点：样式与特殊字符"},
                {"type": "table", "rows": [["维度", "能力"], ["元素定位", "CSS/XPath"], ["自适应", "auto-match"]]},
            ],
        }
    )
    print(f"[write_docx]\n{result}")
    ok = "异常字符" not in result
    print(f"\n==> {'通过' if ok else '存在异常字符（LLM 侧需重写）'}")
    return ok


def cmd_pptx(_: argparse.Namespace) -> bool:
    """直测 pptx 生成。"""
    tool = PptxTool(_work_dir())
    result = tool.execute(
        {
            "filename": "直测样例.pptx",
            "title": "工具直测演示",
            "subtitle": "office agent 工具链验证",
            "slides": [
                {"title": "背景", "bullets": ["验证中文字体", "验证版式"], "notes": "备注样例"},
                {"title": "总结", "bullets": ["生成成功"]},
            ],
        }
    )
    print(f"[write_pptx]\n{result}")
    ok = "异常字符" not in result
    print(f"\n==> {'通过' if ok else '存在异常字符'}")
    return ok


def cmd_loop(args: argparse.Namespace) -> bool:
    """直测完整工具循环（与中枢派发执行路径一致；消耗 LLM token）。"""
    api_key = args.api_key or os.environ.get("TEST_LLM_API_KEY")
    base_url = args.base_url or os.environ.get("TEST_LLM_BASE_URL")
    model = args.model or os.environ.get("TEST_LLM_MODEL")
    if not (api_key and base_url and model):
        print(
            "缺少 LLM 凭据：请传 --api-key/--base-url/--model，"
            "或设置环境变量 TEST_LLM_API_KEY/TEST_LLM_BASE_URL/TEST_LLM_MODEL\n"
            "（从中枢「系统设置 → 模型管理」复制当前激活模型的 Key 与 BaseURL）"
        )
        return False

    from app.executor.llm_client import LLMClient
    from app.executor.tool_loop import ToolLoopRunner, default_registry

    config = _load_config()
    work_dir = Path(config.workspace_root) / "_tool_test"
    work_dir.mkdir(parents=True, exist_ok=True)
    llm = LLMClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=config.office_llm_timeout_seconds,
        max_retries=config.office_llm_max_retries,
    )

    async def run() -> bool:
        steps: list[str] = []

        async def on_progress(progress: int, stage: str, message: str) -> None:
            steps.append(f"  [{progress:3d}% {stage}] {message}")
            print(f"  [{progress:3d}% {stage}] {message}")

        async def on_sources(entries: list[str]) -> None:
            for entry in entries:
                print(f"  📎 来源：{entry}")

        runner = ToolLoopRunner(
            config,
            llm,
            default_registry(work_dir, config),
            on_progress=on_progress,
            on_sources=on_sources,
        )
        print(f"工具循环开始（model={model}）：{args.instruction}")
        outcome = await runner.run("local-test", args.instruction, asyncio.Event())
        print(f"\n==> 轮次 {outcome.rounds}，tokens {outcome.prompt_tokens}+{outcome.completion_tokens}，"
              f"结束原因 {outcome.stopped_reason}")
        if outcome.warnings:
            print("警告：" + "；".join(outcome.warnings))
        print(f"总结：{outcome.summary[:600]}")
        return outcome.summary != ""

    return asyncio.run(run())


def main() -> int:
    parser = argparse.ArgumentParser(description="办公节点工具直测（绕过中枢）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_search = sub.add_parser("search", help="联网搜索")
    p_search.add_argument("query")
    p_fetch = sub.add_parser("fetch", help="抓取指定 URL")
    p_fetch.add_argument("url")
    sub.add_parser("docx", help="生成测试 docx")
    sub.add_parser("pptx", help="生成测试 pptx")
    p_loop = sub.add_parser("loop", help="完整工具循环（耗 LLM token）")
    p_loop.add_argument("instruction")
    p_loop.add_argument("--api-key")
    p_loop.add_argument("--base-url")
    p_loop.add_argument("--model")
    args = parser.parse_args()

    handlers = {
        "search": cmd_search,
        "fetch": cmd_fetch,
        "docx": cmd_docx,
        "pptx": cmd_pptx,
        "loop": cmd_loop,
    }
    try:
        ok = handlers[args.command](args)
    except Exception as exc:  # noqa: BLE001 — 直测脚本异常直接打印退出
        print(f"执行异常：{type(exc).__name__}: {exc}")
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
