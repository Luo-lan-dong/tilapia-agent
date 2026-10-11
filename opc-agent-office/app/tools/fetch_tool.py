"""web_fetch 工具：网页抓取与正文提取（ADR-062 / ADR-065 M6）。

Scrapling Fetcher（curl_cffi，浏览器 TLS 指纹）为主；被反爬拦截时升级
StealthyFetcher（隐身浏览器，需 ``OFFICE_ENABLE_STEALTH=true``，默认关闭）；
正文经 html_to_markdown 转换后返回给 LLM。遵守目标站 robots 意识：仅按任务
需要抓取有限页面。

ADR-065 M6 检索闭环：抓取结果附带来源 URL 与**带序号的段落**，
并提供纯函数 :func:`map_citations` 把「引用」映射回具体段落，
供上层（M4 的 L1 判据「引用数/引用可核对」）使用。
"""

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit

from app.tools.base import OfficeTool, ToolError, require_str
from app.tools.html_text import html_to_markdown

logger = logging.getLogger(__name__)

#: 隐身抓取开关的环境变量名（与 AgentConfig.office_enable_stealth 的 validation_alias 一致）
_STEALTH_ENV = "OFFICE_ENABLE_STEALTH"
#: 视为「开启」的环境变量取值
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
#: 段落切分：换行（含空行）为界；html_to_markdown 每个块级元素独占一行
_PARAGRAPH_SPLIT = re.compile(r"\n+")
#: 域名匹配边界：避免 example.com 命中 notexample.com / example.com.cn
_DOMAIN_BOUNDARY_LEFT = r"(?<![\w.-])"
_DOMAIN_BOUNDARY_RIGHT = r"(?![\w.-])"


def _resolve_stealth_enabled() -> bool:
    """解析隐身抓取开关（``AgentConfig.office_enable_stealth``）。

    优先读运行配置（安装脚本写入的 ``.env`` 或进程环境变量）；
    配置不可用（例如未注入 ``AGENT_REGISTRATION_TOKEN``）时退回直接读环境变量，
    两种情况都默认**关闭**：隐身浏览器体积大、耗时长，按需开启。
    """
    try:
        from app.config import AgentConfig

        return bool(AgentConfig().office_enable_stealth)
    except Exception:  # noqa: BLE001 — 配置不可用时退回环境变量，默认关闭
        pass
    return os.environ.get(_STEALTH_ENV, "").strip().lower() in _TRUE_VALUES


def split_paragraphs(markdown: str) -> list[str]:
    """把正文切成段落列表（纯函数）。

    ``html_to_markdown`` 的每个块级元素（标题/段落/列表项）独占一行，
    因此按换行切分即得到「块级段落」；上层可用段落序号标注引用出处。

    Returns:
        段落列表（已去首尾空白、丢空段）；输入为空时返回空列表。
    """
    if not markdown:
        return []
    return [block.strip() for block in _PARAGRAPH_SPLIT.split(markdown) if block.strip()]


def _normalize_sources(sources: Any) -> list[str]:
    """把来源归一化为去重（保序）的 URL 列表；非法项直接忽略。"""
    if sources is None or isinstance(sources, (str, bytes)):
        return []
    urls: list[str] = []
    for item in sources:
        url = item.get("url") if isinstance(item, dict) else item
        url = str(url or "").strip()
        if not url.startswith("http") or url in urls:
            continue
        urls.append(url)
    return urls


def _paragraph_cites(paragraph: str, url: str) -> bool:
    """判断段落是否引用了该来源（完整 URL 命中，或裸域名/带 www 域名命中）。"""
    if url in paragraph:
        return True
    host = urlsplit(url).hostname or ""
    if not host:
        return False
    candidates = [host]
    if host.startswith("www."):
        candidates.append(host[4:])
    else:
        candidates.append(f"www.{host}")
    return any(
        re.search(f"{_DOMAIN_BOUNDARY_LEFT}{re.escape(name)}{_DOMAIN_BOUNDARY_RIGHT}", paragraph)
        for name in candidates
    )


def map_citations(paragraphs: list[str], sources: Any) -> list[dict[str, Any]]:
    """把「引用」映射到具体段落（纯函数，供引用核对使用）。

    按段落中出现的 URL 或域名匹配来源列表，返回段落序号与来源 URL 的对应关系。

    Args:
        paragraphs: 分段正文（如 :func:`split_paragraphs` 的输出）。
        sources: 来源列表，元素为 URL 字符串或含 ``url`` 键的字典
            （如 web_search 的结果项）。

    Returns:
        ``[{"paragraph_index": int, "url": str}]``，按段落序号升序；
        输入非法或无任何匹配时返回空列表，不抛异常。
    """
    try:
        blocks = [str(item or "") for item in (paragraphs or [])]
        urls = _normalize_sources(sources)
    except Exception:  # noqa: BLE001 — 非法输入按「无匹配」处理
        return []
    mapping: list[dict[str, Any]] = []
    for index, paragraph in enumerate(blocks):
        if not paragraph:
            continue
        for url in urls:
            if _paragraph_cites(paragraph, url):
                mapping.append({"paragraph_index": index, "url": url})
    return mapping


def validate_url(url: str) -> str:
    """校验 LLM 给出的 URL（仅 http/https，拒绝本机地址）。"""
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError(f"仅支持 http/https 链接: {url}")
    host = parsed.hostname
    if host in ("localhost", "127.0.0.1", "0.0.0.0") or host.endswith(".local"):
        raise ToolError(f"拒绝访问本机地址: {url}")
    return url.strip()


class WebFetchTool(OfficeTool):
    """抓取单个网页并返回 Markdown 正文。"""

    name = "web_fetch"
    description = (
        "抓取指定网页并返回正文（Markdown 近似格式）。用于获取 web_search 结果"
        "中某个页面的详细内容；一次抓取一个页面，优先抓取最权威来源。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "目标网页 URL（http/https）"},
        },
        "required": ["url"],
    }

    def __init__(
        self,
        work_dir: Any,
        timeout: float = 45.0,
        proxy: str | None = None,
        enable_stealth: bool | None = None,
    ) -> None:
        super().__init__(work_dir)
        self._timeout = timeout
        self._proxy = proxy
        # None = 跟随配置（AgentConfig.office_enable_stealth，默认关）
        self._enable_stealth = _resolve_stealth_enabled() if enable_stealth is None else enable_stealth

    def execute(self, args: dict[str, Any]) -> str:
        """抓取 → 提取正文（分段编号，便于上层做引用核对）。"""
        url = validate_url(require_str(args, "url", max_len=2000))
        html, via = self._fetch(url)
        if not html:
            return (
                f"抓取失败（已尝试常规与隐身抓取）：{url}。"
                "请跳过该来源继续任务，不要反复重试同一 URL。"
            )
        markdown = html_to_markdown(html)
        if not markdown:
            return f"页面无可提取正文：{url}（可能需要 JS 渲染），请换其他来源。"
        paragraphs = split_paragraphs(markdown)
        numbered = "\n\n".join(
            f"[{index}] {text}" for index, text in enumerate(paragraphs, start=1)
        )
        preview = f"（经 {via}）" if via else ""
        return (
            f"已抓取 {url}{preview}，正文如下（来源: {url}，共 {len(paragraphs)} 段，"
            f"段首 [n] 为段落序号，引用核对时可用）：\n\n{numbered}"
        )

    #: 正文过短阈值：低于此值视为 SPA 占位页（如「正在加载首页」），升级渲染通道
    _MIN_BODY_CHARS = 150

    def _fetch(self, url: str) -> tuple[str, str]:
        """多级抓取并按正文长度择优：Scrapling → httpx → StealthyFetcher。

        SPA 站点静态抓取只能拿到加载占位页（正文极短），需继续升级到
        隐身浏览器渲染，取 markdown 正文最长的一路（2026-09-29 实测）。
        隐身通道受 ``OFFICE_ENABLE_STEALTH`` 开关约束，默认关闭。
        """
        candidates: list[tuple[str, str]] = []
        html = self._fetch_scrapling(url)
        if html:
            candidates.append((html, ""))
        html = self._fetch_httpx(url)
        if html:
            candidates.append((html, "httpx"))
        best_html, best_via = candidates[0] if candidates else ("", "")
        best_len = len(html_to_markdown(best_html))
        if best_len < self._MIN_BODY_CHARS:
            stealth_html, stealth_via = self._fetch_stealthy(url)
            if stealth_html:
                candidates.append((stealth_html, stealth_via or "stealthy"))
                if len(html_to_markdown(stealth_html)) > best_len:
                    best_html, best_via = stealth_html, stealth_via or "stealthy"
        return best_html, best_via

    def _fetch_httpx(self, url: str) -> str:
        """httpx 兜底通道（Scrapling 未装/失败时仍有基本抓取能力）。"""
        import httpx

        try:
            response = httpx.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124.0"},
                timeout=self._timeout,
                follow_redirects=True,
            )
            if response.status_code != 200:
                return ""
            return response.text
        except httpx.HTTPError as exc:
            logger.info("web_fetch_httpx_failed", extra={"url": url, "error": str(exc)})
            return ""

    def _fetch_scrapling(self, url: str) -> str:
        """常规通道：curl_cffi TLS 指纹伪装。"""
        try:
            from scrapling.fetchers import Fetcher

            kwargs: dict[str, Any] = {"stealthy_headers": True, "timeout": self._timeout}
            if self._proxy:
                kwargs["proxy"] = self._proxy
            page = Fetcher.get(url, **kwargs)
            if getattr(page, "status", 0) != 200:
                logger.warning(
                    "web_fetch_non_200", extra={"url": url, "status": getattr(page, "status", 0)}
                )
                return ""
            return self._page_html(page)
        except ImportError as exc:
            # 区分未安装与依赖残缺（curl_cffi 缺失同属此类），日志明示修复方向
            logger.warning(
                "scrapling_fetcher_unavailable",
                extra={"event": "scrapling_fetcher_unavailable", "error": str(exc)},
            )
            return ""
        except Exception as exc:  # noqa: BLE001 — 抓取失败升级兜底通道
            logger.info("web_fetch_fetcher_failed", extra={"url": url, "error": str(exc)})
            return ""

    def _fetch_stealthy(self, url: str) -> tuple[str, str]:
        """隐身通道：Patchright 浏览器（需开关开启且已安装可选依赖）。

        ``OFFICE_ENABLE_STEALTH`` 未开启时直接跳过（记录一次 info，
        说明「stealth 已按配置跳过」），避免拉取浏览器体积与额外耗时。
        """
        if not self._enable_stealth:
            logger.info(
                "web_fetch_stealth_skipped",
                extra={
                    "event": "web_fetch_stealth_skipped",
                    "url": url,
                    "reason": f"stealth 已按配置跳过（{_STEALTH_ENV} 未开启）",
                },
            )
            return "", ""
        try:
            from scrapling.fetchers import StealthyFetcher
        except ImportError:
            return "", ""
        try:
            page = StealthyFetcher.fetch(
                url, headless=True, network_idle=True, timeout=self._timeout * 2
            )
            if getattr(page, "status", 0) != 200:
                return "", ""
            return self._page_html(page), "stealthy"
        except Exception as exc:  # noqa: BLE001 — 隐身通道失败按最终失败处理
            logger.warning("web_fetch_stealthy_failed", extra={"url": url, "error": str(exc)})
            return "", ""

    @staticmethod
    def _page_html(page: Any) -> str:
        """从 Scrapling 响应对象提取 HTML 字符串。"""
        body = getattr(page, "body_content", None) or getattr(page, "html_content", None)
        if body is None and hasattr(page, "html"):
            body = page.html
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        return body if isinstance(body, str) else str(body or "")
