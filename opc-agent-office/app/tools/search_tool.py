"""web_search 工具：免 Key 检索入口（DuckDuckGo HTML，ADR-062 / ADR-065 M6）。

检索来源可插拔：第一期抓取 DuckDuckGo HTML 结果页（零依赖零 Key）；
后续可替换为商用搜索 API（博查/Tavily）或自建 SearXNG，不影响上层循环。

ADR-065 M6 检索闭环：结果页解析**优先**走 Scrapling 的 CSS 选择器引擎
（``scrapling.parser.Adaptor``），Scrapling 不可用或解析异常时**显式**降级到
手写正则兜底（原实现保留，降级事件写入日志，不静默）。解析函数是纯函数：
输入 HTML 字符串 → 输出 ``[{title, url, snippet, source}]``，可用固定样本离线测试。
"""

import logging
import os
import re
from html import unescape
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlsplit

import httpx

from app.tools.base import OfficeTool, require_str

logger = logging.getLogger(__name__)

#: DDG HTML 版结果页（POST 表单提交，比 GET 稳定）
_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
#: Scrapling 解析基准地址（还原相对链接 / 协议相对链接）
_DDG_BASE_URL = "https://html.duckduckgo.com/html/"
_BING_BASE_URL = "https://cn.bing.com/"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SNIPPET_TAG = re.compile(r"<[^>]+>")
#: 空白压缩：Scrapling 取文本与正则路径统一形态（HTML 换行/缩进不算内容）
_WHITESPACE = re.compile(r"\s+")
#: 结果来源标识（统一结构与 M4 引用核对使用）
_SOURCE_DDG = "duckduckgo"
_SOURCE_BING = "bing"
#: 非结果链接前缀（页面锚点、脚本、邮件等）
_SKIP_HREF_PREFIXES = ("#", "javascript:", "mailto:", "tel:")


def _unwrap_ddg_href(href: str) -> str:
    """还原 DDG 重定向链接中的真实 URL（``/l/?uddg=<encoded>``）。"""
    if "duckduckgo.com/l/" not in href:
        return href
    try:
        query = parse_qs(urlsplit(href).query)
        target = query.get("uddg", [""])[0]
        return unquote(target) or href
    except ValueError:
        return href


def _load_adaptor() -> Any | None:
    """延迟加载 Scrapling 解析器（``scrapling.parser.Adaptor``）。

    延迟导入是为了让 Scrapling 保持可选重依赖：未安装或导入失败（版本漂移、
    依赖残缺）时返回 None，由调用方显式降级到正则兜底（并写入可观测日志）。

    Returns:
        可用的 Adaptor 类；不可用时返回 None。
    """
    try:
        from scrapling.parser import Adaptor
    except Exception as exc:  # noqa: BLE001 — 可选依赖：任何导入失败都走正则兜底
        logger.warning(
            "search_parser_scrapling_unavailable",
            extra={"event": "search_parser_scrapling_unavailable", "error": str(exc)},
        )
        return None
    return Adaptor


def _normalize_text(text: str) -> str:
    """把文本中的连续空白压成单空格（Scrapling 取文本与正则路径统一形态）。"""
    return _WHITESPACE.sub(" ", text or "").strip()


def _element_text(element: Any) -> str:
    """取 Scrapling 元素的全部可见文本（含子节点；忽略 script/style）。

    使用 ``separator=""`` + ``strip=False``：保留源码里的空格（截断的文本节点），
    与正则路径「去标签」得到的文本一致，避免英文单词被粘连。
    """
    try:
        raw = element.get_all_text(separator="", strip=False)
    except Exception:  # noqa: BLE001 — 单个元素取文本失败不影响整页解析
        return ""
    return _normalize_text(str(raw))


def _absolute_href(element: Any, base_url: str) -> str:
    """取元素 href 并补全为绝对地址（协议相对链接补 https:）。

    Returns:
        可访问的绝对 URL；锚点/脚本/邮件等非结果链接返回空串。
    """
    href = str(element.attrib.get("href") or "").strip()
    if not href or href.lower().startswith(_SKIP_HREF_PREFIXES):
        return ""
    if href.startswith("//"):
        return f"https:{href}"
    return href if href.startswith("http") else urljoin(base_url, href)


def _ddg_block_snippet(anchor: Any) -> Any | None:
    """在最近的 DDG 结果块祖先内查找摘要元素。

    优先「块内配对」（同一 result 块内的 ``.result__snippet``），
    块结构缺失或异常时返回 None，由调用方退回按出现顺序（下标）配对。
    """
    try:
        for ancestor in anchor.iterancestors():
            classes = str(ancestor.attrib.get("class") or "").split()
            if "result" not in classes and "web-result" not in classes:
                continue
            snippets = ancestor.css("a.result__snippet")
            if snippets:
                return snippets[0]
    except Exception:  # noqa: BLE001 — 祖先遍历失败退化为按序配对
        return None
    return None


def _parse_ddg_page(adaptor: Any, html: str, max_results: int) -> list[dict[str, str]]:
    """用 Scrapling CSS 选择器解析 DDG 结果页。

    选择器：``a.result__a``（标题+链接）与 ``a.result__snippet``（摘要）。
    """
    page = adaptor(html, url=_DDG_BASE_URL)
    anchors = page.css("a.result__a")
    snippets = page.css("a.result__snippet")
    results: list[dict[str, str]] = []
    for index, anchor in enumerate(anchors):
        url = _unwrap_ddg_href(_absolute_href(anchor, _DDG_BASE_URL))
        if not url.startswith("http"):
            continue
        snippet_element = _ddg_block_snippet(anchor)
        if snippet_element is None and index < len(snippets):
            snippet_element = snippets[index]
        results.append(
            {
                "title": _element_text(anchor)[:200],
                "url": url,
                "snippet": _element_text(snippet_element) if snippet_element is not None else "",
                "source": _SOURCE_DDG,
            }
        )
        if len(results) >= max_results:
            break
    return _truncate(results)


def _parse_bing_page(adaptor: Any, html: str, max_results: int) -> list[dict[str, str]]:
    """用 Scrapling CSS 选择器解析 Bing 结果页。

    选择器：``li.b_algo`` 结果块 → ``h2 a``（标题+链接）与
    ``p.b_caption / p.b_lineclamp``（摘要）。
    """
    page = adaptor(html, url=_BING_BASE_URL)
    results: list[dict[str, str]] = []
    for block in page.css("li.b_algo"):
        anchors = block.css("h2 a")
        if not anchors:
            continue
        url = _absolute_href(anchors[0], _BING_BASE_URL)
        if not url.startswith("http"):
            continue
        snippet = ""
        for element in block.css("p.b_caption, p.b_lineclamp, .b_caption p, .b_lineclamp"):
            snippet = _element_text(element)
            if snippet:
                break
        results.append(
            {
                "title": _element_text(anchors[0])[:200],
                "url": url,
                "snippet": snippet,
                "source": _SOURCE_BING,
            }
        )
        if len(results) >= max_results:
            break
    return _truncate(results)


def _truncate(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """统一截断摘要长度（两条解析路径产出形态一致）。"""
    for item in results:
        item["snippet"] = item["snippet"][:300]
    return results


def _parse_ddg_regex(html: str, max_results: int) -> list[dict[str, str]]:
    """正则兜底解析 DDG 结果页（Scrapling 不可用时启用，原实现保留）。"""
    results: list[dict[str, str]] = []
    # 结果块：class="result__a" 的锚即标题与链接，同级 result__snippet 为摘要
    anchors = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        flags=re.DOTALL,
    )
    snippets = re.findall(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        html,
        flags=re.DOTALL,
    )
    for index, (href, title) in enumerate(anchors):
        url = _unwrap_ddg_href(href.strip())
        if not url.startswith("http"):
            continue
        snippet = _SNIPPET_TAG.sub("", snippets[index]).strip() if index < len(snippets) else ""
        results.append(
            {
                "title": unescape(_SNIPPET_TAG.sub("", title)).strip()[:200],
                "url": url,
                "snippet": unescape(snippet)[:300],
                "source": _SOURCE_DDG,
            }
        )
        if len(results) >= max_results:
            break
    return results


def _parse_bing_regex(html: str, max_results: int) -> list[dict[str, str]]:
    """正则兜底解析 Bing 结果页（Scrapling 不可用时启用，原实现保留）。"""
    results: list[dict[str, str]] = []
    # 结果块：li.b_algo 内 h2>a 为标题与链接，.b_caption p（或 .b_lineclamp）为摘要
    # （class 允许附加其他标记，如 b_algoBorder，与 Scrapling 路径选择器等价）
    blocks = re.findall(
        r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>.*?</li>', html, flags=re.DOTALL
    )
    for block in blocks:
        anchor = re.search(r'<h2[^>]*><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, flags=re.DOTALL)
        if not anchor:
            continue
        url = anchor.group(1).strip()
        if not url.startswith("http"):
            continue
        title = _SNIPPET_TAG.sub("", anchor.group(2)).strip()
        snippet_match = re.search(
            r'<p[^>]*class="[^"]*b_(?:caption|lineclamp)[^"]*"[^>]*>(.*?)</p>',
            block,
            flags=re.DOTALL,
        )
        snippet = _SNIPPET_TAG.sub("", snippet_match.group(1)).strip() if snippet_match else ""
        results.append(
            {
                "title": unescape(title)[:200],
                "url": url,
                "snippet": unescape(snippet)[:300],
                "source": _SOURCE_BING,
            }
        )
        if len(results) >= max_results:
            break
    return results


def _parse_result_page(
    html: str,
    max_results: int,
    *,
    source: str,
    css_parser: Callable[[Any, str, int], list[dict[str, str]]],
    regex_parser: Callable[[str, int], list[dict[str, str]]],
) -> list[dict[str, str]]:
    """统一解析入口：Scrapling CSS 解析优先，失败/不可用时显式降级正则。

    降级都会留下可观测日志（``search_parser_regex_fallback`` /
    ``search_parser_scrapling_failed``），不静默吞掉能力缺失。
    """
    adaptor = _load_adaptor()
    if adaptor is not None:
        try:
            results = css_parser(adaptor, html, max_results)
        except Exception as exc:  # noqa: BLE001 — 解析器异常降级正则兜底
            logger.warning(
                "search_parser_scrapling_failed",
                extra={
                    "event": "search_parser_scrapling_failed",
                    "source": source,
                    "error": str(exc),
                },
            )
        else:
            if results:
                return results
            logger.info(
                "search_parser_scrapling_empty",
                extra={"event": "search_parser_scrapling_empty", "source": source},
            )
    else:
        logger.warning(
            "search_parser_regex_fallback",
            extra={
                "event": "search_parser_regex_fallback",
                "source": source,
                "reason": "scrapling_unavailable",
            },
        )
    return regex_parser(html, max_results)


def parse_ddg_html(html: str, max_results: int = 8) -> list[dict[str, str]]:
    """解析 DuckDuckGo HTML 结果页（纯函数，便于用固定样本测试）。

    Scrapling CSS 选择器优先，不可用时降级手写正则（事件可观测）。

    Returns:
        ``[{title, url, snippet, source}]``（source = ``duckduckgo``）；
        无结果时返回空列表（不抛异常）。
    """
    return _parse_result_page(
        html,
        max_results,
        source=_SOURCE_DDG,
        css_parser=_parse_ddg_page,
        regex_parser=_parse_ddg_regex,
    )


def parse_bing_html(html: str, max_results: int = 8) -> list[dict[str, str]]:
    """解析 Bing 结果页（cn.bing.com，大陆网络可达；纯函数便于测试）。

    Scrapling CSS 选择器优先，不可用时降级手写正则（事件可观测）。

    Returns:
        ``[{title, url, snippet, source}]``（source = ``bing``）；
        无结果时返回空列表（不抛异常）。
    """
    return _parse_result_page(
        html,
        max_results,
        source=_SOURCE_BING,
        css_parser=_parse_bing_page,
        regex_parser=_parse_bing_regex,
    )


class WebSearchTool(OfficeTool):
    """联网检索：关键词 → 结果列表（标题/URL/摘要）。"""

    name = "web_search"
    description = (
        "联网搜索。返回与关键词相关的网页列表（标题、链接、摘要），"
        "需要网页详细内容时再对目标 URL 调用 web_fetch。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {
                "type": "integer",
                "description": "返回条数上限（默认 8，最大 15）",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        work_dir: Any,
        default_max_results: int = 8,
        timeout: float = 20.0,
        proxy: str | None = None,
    ) -> None:
        super().__init__(work_dir)
        self._default_max_results = default_max_results
        self._timeout = timeout
        self._proxy = proxy

    def execute(self, args: dict[str, Any]) -> str:
        """多后端检索（Bing→DDG 依序尝试），失败原因明确回传。"""
        query = require_str(args, "query", max_len=300)
        max_results = args.get("max_results")
        max_results = (
            max(1, min(15, int(max_results))) if isinstance(max_results, int) else self._default_max_results
        )
        failures: list[str] = []
        for backend, html in self._iter_backends(query):
            parser = parse_bing_html if backend == "bing" else parse_ddg_html
            if html is None:
                continue  # 该后端已在 _iter_backends 记录失败
            results = parser(html, max_results)
            if results:
                lines = [f"搜索「{query}」共 {len(results)} 条结果（来源：{backend}）："]
                for index, item in enumerate(results, start=1):
                    lines.append(
                        f"{index}. {item['title']}\n   链接: {item['url']}\n   摘要: {item['snippet']}"
                    )
                return "\n".join(lines)
            failures.append(f"{backend}：页面可达但未解析出结果")
        detail = "；".join(failures) if failures else "所有搜索后端均不可达"
        logger.warning("web_search_all_backends_failed", extra={"failures": detail})
        return (
            f"联网搜索不可用（{detail}）。请基于已有信息继续任务，"
            "并在总结中注明「未联网核实」。"
        )

    def _iter_backends(self, query: str):
        """依序产出 (后端名, HTML)；None 表示该后端请求失败（原因已记录）。"""
        backend_env = os.environ.get("OFFICE_SEARCH_BACKEND", "auto").strip().lower()
        backends = ["bing"] if backend_env == "bing" else ["ddg"] if backend_env == "ddg" else ["bing", "ddg"]
        for backend in backends:
            try:
                html = self._fetch_backend(backend, query)
            except Exception as exc:  # noqa: BLE001 — 单后端失败继续下一个
                logger.warning(
                    "search_backend_failed",
                    extra={"backend": backend, "error": str(exc)},
                )
                yield backend, None
                continue
            yield backend, html

    def _fetch_backend(self, backend: str, query: str) -> str:
        """请求单个搜索后端的结果页 HTML（Scrapling 优先，httpx 兜底）。"""
        if backend == "bing":
            url = f"https://cn.bing.com/search?q={quote_plus(query)}&mkt=zh-CN"
            method, data = "GET", None
        else:
            url = _DDG_HTML_URL
            method, data = "POST", {"q": query}
        try:
            from scrapling.fetchers import Fetcher  # 延迟导入：可选重依赖

            page = (
                Fetcher.get(url, stealthy_headers=True, timeout=self._timeout, proxy=self._proxy)
                if method == "GET"
                else Fetcher.post(
                    url, data=data, stealthy_headers=True, timeout=self._timeout, proxy=self._proxy
                )
            )
            if getattr(page, "status", 0) != 200:
                raise RuntimeError(f"HTTP {getattr(page, 'status', 0)}")
            body = getattr(page, "body_content", None) or getattr(page, "body", None) or ""
            if isinstance(body, bytes):
                body = body.decode("utf-8", errors="replace")
            return str(body)
        except ImportError:
            logger.info("scrapling_unavailable_fallback_httpx")
        except Exception as exc:  # noqa: BLE001 — Scrapling 失败降级 httpx
            logger.warning(
                "search_backend_scrapling_failed",
                extra={"backend": backend, "error": str(exc)},
            )
            raise RuntimeError(f"scrapling: {exc}") from exc
        try:
            response = httpx.request(
                method,
                url,
                data=data,
                headers={"User-Agent": _UA},
                timeout=self._timeout,
                follow_redirects=True,
                proxy=self._proxy,
            )
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")
            return response.text
        except httpx.HTTPError as exc:
            logger.warning(
                "search_backend_httpx_failed",
                extra={"backend": backend, "error": str(exc)},
            )
            raise RuntimeError(f"httpx: {exc}") from exc
