"""M6 检索闭环单元测试（ADR-065）：解析器切换、引用↔段落映射、stealth 开关。

覆盖：
- UT-M6-1：DDG / Bing 固定样本 HTML → 正确的 title/url/snippet/source（含重定向还原）
- UT-M6-2：Scrapling 不可用/解析异常 → 显式降级正则并记录可观测事件；可用时走解析器分支
- UT-M6-3：map_citations 段落 → 来源映射（无误配、重复 URL、多来源、非法输入）
- 另：``OFFICE_ENABLE_STEALTH`` 默认关不调用 StealthyFetcher，置真才尝试；
  web_fetch 返回来源 URL + 带序号段落。

全部使用固定样本与 mock，不访问外网。
"""

import logging
import sys
import types
from pathlib import Path

import pytest

from app.config import AgentConfig
from app.tools import search_tool
from app.tools.fetch_tool import WebFetchTool, map_citations, split_paragraphs
from app.tools.search_tool import parse_bing_html, parse_ddg_html

#: DDG 结果页固定样本（含重定向链接、带标签标题、无摘要的裸锚）
DDG_SAMPLE = """
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.example.com%2Freport%3Fid%3D7&amp;rut=abc">
      <b>年度</b>报告
    </a>
  </h2>
  <a class="result__snippet" href="#">这是<b>摘要</b>一 &amp; 补充说明。</a>
</div>
<div class="result results_links web-result">
  <h2 class="result__title"><a class="result__a" href="https://news.example.org/b">第二条结果</a></h2>
  <a class="result__snippet" href="#">第二条摘要。</a>
</div>
<a class="result__a" href="https://third.example.net/c">第三条结果</a>
"""

#: Bing 结果页固定样本（含 javascript: 伪链接，应被跳过）
BING_SAMPLE = """
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://www.example.cn/one">Bing <strong>结果</strong> 一</a></h2>
    <div class="b_caption"><p class="b_lineclamp2">这是 <b>Bing</b> 摘要一。</p></div>
  </li>
  <li class="b_algo b_algoBorder">
    <h2><a href="https://two.example.com/x">结果二</a></h2>
    <div class="b_caption"><p>摘要二内容</p></div>
  </li>
  <li class="b_algo"><h2><a href="javascript:void(0)">非 http 结果</a></h2></li>
</ol>
"""

_DDG_URLS = [
    "https://docs.example.com/report?id=7",
    "https://news.example.org/b",
    "https://third.example.net/c",
]


def _install_fake_fetchers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetcher_html: str | None = None,
    stealthy_html: str | None = None,
) -> list[str]:
    """注入 ``scrapling.fetchers`` mock，返回通道调用序列。

    ``None`` 表示该通道抛错（如被反爬拦截 / 浏览器未安装）；同时切断 httpx
    通道，保证用例完全离线且确定。
    """
    calls: list[str] = []

    class FakePage:
        status = 200

        def __init__(self, html: str) -> None:
            self.html_content = html

    def fetcher_get(url, **kwargs):  # noqa: ANN001, ANN202
        calls.append("fetcher")
        if fetcher_html is None:
            raise RuntimeError("blocked")
        return FakePage(fetcher_html)

    def stealthy_fetch(url, **kwargs):  # noqa: ANN001, ANN202
        calls.append("stealthy")
        if stealthy_html is None:
            raise RuntimeError("no browser")
        return FakePage(stealthy_html)

    fetchers_mod = types.ModuleType("scrapling.fetchers")
    fetchers_mod.Fetcher = type("F", (), {"get": staticmethod(fetcher_get)})
    fetchers_mod.StealthyFetcher = type("S", (), {"fetch": staticmethod(stealthy_fetch)})
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", fetchers_mod)
    monkeypatch.setattr(WebFetchTool, "_fetch_httpx", lambda self, url: "")
    return calls


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    """取日志事件名列表（事件名即 logger 的 message）。"""
    return [record.getMessage() for record in caplog.records]


class TestSearchParsing:
    """UT-M6-1：固定样本解析出 title/url/snippet/source。"""

    def test_ddg_sample_parses_all_fields(self) -> None:
        results = parse_ddg_html(DDG_SAMPLE, max_results=5)
        assert len(results) == 3
        assert results[0] == {
            "title": "年度报告",
            "url": "https://docs.example.com/report?id=7",
            "snippet": "这是摘要一 & 补充说明。",
            "source": "duckduckgo",
        }
        assert results[1]["title"] == "第二条结果"
        assert results[1]["url"] == "https://news.example.org/b"
        assert results[1]["snippet"] == "第二条摘要。"
        assert results[1]["source"] == "duckduckgo"

    def test_ddg_redirect_link_unwrapped(self) -> None:
        """DDG 重定向链接还原为真实 URL，且不残留 uddg 参数。"""
        results = parse_ddg_html(DDG_SAMPLE, max_results=5)
        assert results[0]["url"] == "https://docs.example.com/report?id=7"
        assert "uddg" not in results[0]["url"]
        assert "duckduckgo.com" not in results[0]["url"]

    def test_ddg_missing_snippet_and_max_results(self) -> None:
        results = parse_ddg_html(DDG_SAMPLE, max_results=2)
        assert len(results) == 2
        full = parse_ddg_html(DDG_SAMPLE, max_results=5)
        assert full[2]["snippet"] == ""  # 裸锚无摘要：返回空串而非异常
        assert full[2]["url"] == "https://third.example.net/c"

    def test_bing_sample_parses_all_fields(self) -> None:
        results = parse_bing_html(BING_SAMPLE, max_results=5)
        assert len(results) == 2  # javascript: 伪链接被跳过
        assert results[0] == {
            "title": "Bing 结果 一",
            "url": "https://www.example.cn/one",
            "snippet": "这是 Bing 摘要一。",
            "source": "bing",
        }
        assert results[1]["title"] == "结果二"
        assert results[1]["snippet"] == "摘要二内容"
        assert results[1]["source"] == "bing"

    def test_bing_respects_max_results(self) -> None:
        assert len(parse_bing_html(BING_SAMPLE, max_results=1)) == 1

    def test_empty_html_returns_empty(self) -> None:
        """空页/无结果页返回空列表，不抛异常。"""
        assert parse_ddg_html("<html><body></body></html>") == []
        assert parse_bing_html("<html><body></body></html>") == []
        assert parse_ddg_html("") == []
        assert parse_bing_html("") == []

    def test_regex_fallback_shape_matches_scrapling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """正则兜底与 Scrapling 路径产出同形同值结果（统一结构契约）。"""
        primary = parse_ddg_html(DDG_SAMPLE, max_results=5)
        monkeypatch.setattr(search_tool, "_load_adaptor", lambda: None)
        fallback = parse_ddg_html(DDG_SAMPLE, max_results=5)
        assert fallback == primary
        assert set(primary[0]) == {"title", "url", "snippet", "source"}


class TestParserDegradation:
    """UT-M6-2：Scrapling 分支切换与显式降级（不静默）。"""

    def test_scrapling_available_uses_parser_branch(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Scrapling 可用：构造 Adaptor 走 CSS 解析分支，且不触发降级事件。"""
        real_parser = pytest.importorskip("scrapling.parser")
        constructed: list[int] = []

        class RecordingAdaptor:
            """代理真实 Adaptor，记录解析器分支确实被调用。"""

            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                constructed.append(1)
                self.__dict__["_inner"] = real_parser.Adaptor(*args, **kwargs)

            def __getattr__(self, name: str):  # noqa: ANN202
                return getattr(object.__getattribute__(self, "_inner"), name)

        fake_module = types.ModuleType("scrapling.parser")
        fake_module.Adaptor = RecordingAdaptor
        monkeypatch.setitem(sys.modules, "scrapling.parser", fake_module)

        with caplog.at_level(logging.INFO, logger="app.tools.search_tool"):
            results = parse_ddg_html(DDG_SAMPLE, max_results=5)

        assert constructed, "Scrapling 可用时必须走 Adaptor 解析分支"
        assert [item["url"] for item in results] == _DDG_URLS
        assert "search_parser_regex_fallback" not in _messages(caplog)

    def test_scrapling_unavailable_falls_back_to_regex(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Scrapling 导入失败（None in sys.modules）：显式降级正则并记录事件。"""
        monkeypatch.setitem(sys.modules, "scrapling.parser", None)

        with caplog.at_level(logging.INFO, logger="app.tools.search_tool"):
            results = parse_ddg_html(DDG_SAMPLE, max_results=5)

        assert [item["url"] for item in results] == _DDG_URLS
        assert results[0]["source"] == "duckduckgo"
        assert results[0]["snippet"] == "这是摘要一 & 补充说明。"
        events = _messages(caplog)
        assert "search_parser_regex_fallback" in events
        assert "search_parser_scrapling_unavailable" in events
        fallback = next(r for r in caplog.records if r.getMessage() == "search_parser_regex_fallback")
        assert fallback.levelno == logging.WARNING
        assert getattr(fallback, "reason", "") == "scrapling_unavailable"

    def test_scrapling_parse_error_falls_back_to_regex(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """解析器可用但抛异常：记录告警事件后仍由正则兜底出结果。"""
        broken_module = types.ModuleType("scrapling.parser")

        class BrokenAdaptor:
            def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
                raise RuntimeError("parser boom")

        broken_module.Adaptor = BrokenAdaptor
        monkeypatch.setitem(sys.modules, "scrapling.parser", broken_module)

        with caplog.at_level(logging.INFO, logger="app.tools.search_tool"):
            results = parse_bing_html(BING_SAMPLE, max_results=5)

        assert [item["url"] for item in results] == [
            "https://www.example.cn/one",
            "https://two.example.com/x",
        ]
        assert "search_parser_scrapling_failed" in _messages(caplog)


class TestMapCitations:
    """UT-M6-3：引用 ↔ 段落映射。"""

    PARAGRAPHS = [
        "背景：见 https://docs.example.com/report?id=7 的第三章。",
        "第二段没有引用任何来源。",
        "该结论来自 news.example.org 的报道。",
    ]
    SOURCES = [
        "https://docs.example.com/report?id=7",
        "https://news.example.org/b",
        "https://unused.example.net/x",
    ]

    def test_matches_full_url_and_domain(self) -> None:
        assert map_citations(self.PARAGRAPHS, self.SOURCES) == [
            {"paragraph_index": 0, "url": "https://docs.example.com/report?id=7"},
            {"paragraph_index": 2, "url": "https://news.example.org/b"},
        ]

    def test_no_match_returns_empty(self) -> None:
        assert map_citations(self.PARAGRAPHS, ["https://unused.example.net/x"]) == []
        assert map_citations(["没有引用的段落"], self.SOURCES) == []

    def test_dedupes_repeated_url(self) -> None:
        """同一来源重复出现（URL + 域名、或来源列表重复）只产出一条映射。"""
        paragraphs = ["见 https://docs.example.com/report?id=7 与 docs.example.com 的同一份报告。"]
        sources = [
            "https://docs.example.com/report?id=7",
            "https://docs.example.com/report?id=7",
        ]
        assert map_citations(paragraphs, sources) == [
            {"paragraph_index": 0, "url": "https://docs.example.com/report?id=7"}
        ]

    def test_multiple_sources_same_paragraph_keeps_source_order(self) -> None:
        paragraphs = ["a.example.com 与 b.example.org 都提到了。"]
        sources = ["https://a.example.com/1", "https://b.example.org/2"]
        assert map_citations(paragraphs, sources) == [
            {"paragraph_index": 0, "url": "https://a.example.com/1"},
            {"paragraph_index": 0, "url": "https://b.example.org/2"},
        ]

    def test_accepts_dict_sources_and_ignores_junk(self) -> None:
        """来源可为 web_search 结果字典；非法项被忽略而不抛异常。"""
        sources = [
            {"url": "https://news.example.org/b", "title": "报道", "source": "bing"},
            "",
            None,
            {},
            "不是URL",
        ]
        assert map_citations(self.PARAGRAPHS, sources) == [
            {"paragraph_index": 2, "url": "https://news.example.org/b"}
        ]

    def test_domain_match_respects_boundaries(self) -> None:
        """域名匹配带边界：notexample.com 不应命中 example.com。"""
        assert map_citations(["详见 notexample.com 的说明。"], ["https://example.com/a"]) == []

    def test_invalid_input_returns_empty(self) -> None:
        assert map_citations([], None) == []
        assert map_citations(None, self.SOURCES) == []  # type: ignore[arg-type]
        assert map_citations(self.PARAGRAPHS, "https://docs.example.com/report?id=7") == []


class TestParagraphSplitting:
    """web_fetch 分段能力（纯函数）。"""

    def test_split_paragraphs(self) -> None:
        assert split_paragraphs("第一段。\n\n第二段。\n\n\n第三段。") == [
            "第一段。",
            "第二段。",
            "第三段。",
        ]
        # html_to_markdown 的块级元素独占一行 → 每行为一段
        assert split_paragraphs("# 标题 \n第一段。 \n第二段。") == ["# 标题", "第一段。", "第二段。"]
        assert split_paragraphs("- 条目一\n- 条目二\n\n正文。") == ["- 条目一", "- 条目二", "正文。"]

    def test_split_paragraphs_handles_empty(self) -> None:
        assert split_paragraphs("") == []
        assert split_paragraphs("\n\n   \n\n") == []

    def test_mapped_citations_from_fetch_output(self) -> None:
        """端到端小闭环：分段正文 → 引用映射（供 M4 L1 判据使用）。"""
        markdown = "结论见 example.com 的数据。\n\n第二段为背景介绍。\n\n数据来源 https://two.example.org/x。"
        mapping = map_citations(
            split_paragraphs(markdown),
            ["https://example.com/report", "https://two.example.org/x"],
        )
        assert mapping == [
            {"paragraph_index": 0, "url": "https://example.com/report"},
            {"paragraph_index": 2, "url": "https://two.example.org/x"},
        ]


class TestStealthSwitch:
    """OFFICE_ENABLE_STEALTH 开关：默认关不碰 StealthyFetcher。"""

    def test_config_default_and_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_REGISTRATION_TOKEN", "test-token")
        monkeypatch.delenv("OFFICE_ENABLE_STEALTH", raising=False)
        assert AgentConfig(_env_file=None).office_enable_stealth is False

        monkeypatch.setenv("OFFICE_ENABLE_STEALTH", "true")
        assert AgentConfig(_env_file=None).office_enable_stealth is True

    def test_stealth_disabled_by_default_skips_stealthy(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        calls = _install_fake_fetchers(monkeypatch)
        monkeypatch.setenv("AGENT_REGISTRATION_TOKEN", "test-token")
        monkeypatch.delenv("OFFICE_ENABLE_STEALTH", raising=False)
        tool = WebFetchTool(tmp_path)
        assert tool._enable_stealth is False

        with caplog.at_level(logging.INFO, logger="app.tools.fetch_tool"):
            result = tool.execute({"url": "https://example.com/spa"})

        assert calls == ["fetcher"]  # 关闭时绝不触碰 StealthyFetcher
        assert "web_fetch_stealth_skipped" in _messages(caplog)
        skipped = next(r for r in caplog.records if r.getMessage() == "web_fetch_stealth_skipped")
        assert skipped.levelno == logging.INFO
        assert "已按配置跳过" in getattr(skipped, "reason", "")
        assert "抓取失败" in result

    def test_stealth_enabled_attempts_stealthy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _install_fake_fetchers(
            monkeypatch, stealthy_html="<p>" + "隐身渲染正文" * 30 + "</p>"
        )
        tool = WebFetchTool(tmp_path, enable_stealth=True)
        result = tool.execute({"url": "https://example.com/spa"})
        assert calls == ["fetcher", "stealthy"]
        assert "隐身渲染正文" in result
        assert "（经 stealthy）" in result

    def test_disabled_stealth_does_not_use_stealthy_html(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式关闭时即便隐身通道 mock 可用也不采用其结果。"""
        calls = _install_fake_fetchers(
            monkeypatch, stealthy_html="<p>" + "不该出现的隐身正文" * 30 + "</p>"
        )
        tool = WebFetchTool(tmp_path, enable_stealth=False)
        result = tool.execute({"url": "https://example.com/spa"})
        assert calls == ["fetcher"]
        assert "不该出现的隐身正文" not in result


class TestFetchNumberedParagraphs:
    """抓取结果 = 来源 URL + 带序号段落（M6 引用闭环的输入）。"""

    SAMPLE_HTML = "<html><body><h1>标题</h1><p>第一段正文。</p><p>第二段正文。</p></body></html>"

    def test_output_contains_source_url_and_numbered_paragraphs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_fetchers(monkeypatch, fetcher_html=self.SAMPLE_HTML)
        tool = WebFetchTool(tmp_path)
        result = tool.execute({"url": "https://example.com/page"})

        assert result.startswith("已抓取 https://example.com/page")
        assert "来源: https://example.com/page" in result
        assert "[1] # 标题" in result
        assert "[2] 第一段正文。" in result
        assert "[3] 第二段正文。" in result
