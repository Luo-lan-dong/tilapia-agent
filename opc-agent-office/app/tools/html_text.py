"""HTML → Markdown 轻量转换（ADR-062 web_fetch 正文提取）。

基于标准库 ``html.parser`` 的流式转换：标题/段落/列表/代码块映射为
Markdown 近似结构；script/style/nav 等噪音剔除。纯函数实现，可测。
"""

import re
from html import unescape
from html.parser import HTMLParser

#: 噪音标签：整块跳过（不产出文本）
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "iframe", "head"})
#: 弱噪音标签（页面骨架，通常非正文）
_NOISE_TAGS = frozenset({"nav", "header", "footer", "aside", "form"})
_HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
_BLOCK_TAGS = frozenset({"p", "div", "section", "article", "li", "blockquote", "tr"})
#: 提取结果字符上限（喂给 LLM 的正文截断）
MAX_MARKDOWN_CHARS = 20_000


class _MarkdownExtractor(HTMLParser):
    """流式 HTML → Markdown 提取器。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._noise_depth = 0
        self._list_stack: list[str] = []
        self._list_index = 0
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _NOISE_TAGS:
            self._noise_depth += 1
            return
        if tag in _HEADING_TAGS:
            self._parts.append(f"\n{_HEADING_TAGS[tag]} ")
        elif tag in ("ul", "ol"):
            self._list_stack.append(tag)
            self._list_index = 0
        elif tag == "li" and self._list_stack:
            self._list_index += 1
            marker = f"{self._list_index}. " if self._list_stack[-1] == "ol" else "- "
            self._parts.append(f"\n{marker}")
        elif tag == "blockquote":
            self._parts.append("\n> ")
        elif tag == "pre":
            self._parts.append("\n```\n")
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []
        elif tag == "br":
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in _NOISE_TAGS and self._noise_depth:
            self._noise_depth -= 1
            return
        if tag in _HEADING_TAGS:
            self._parts.append("\n")
        elif tag in ("ul", "ol") and self._list_stack:
            self._list_stack.pop()
            self._parts.append("\n")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")
        elif tag == "pre":
            self._parts.append("\n```\n")
        elif tag == "a" and self._href is not None:
            text = "".join(self._link_text).strip()
            if text and self._href.startswith("http") and not self._noise_depth:
                self._parts.append(f"[{text}]({self._href})")
            elif text:
                self._parts.append(text)
            self._href = None
            self._link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth or self._noise_depth:
            return
        text = data.strip()
        if not text:
            if self._href is not None:
                self._link_text.append(" ")
            return
        if self._href is not None:
            self._link_text.append(data)
            return
        self._parts.append(text + " ")

    def markdown(self) -> str:
        """合并片段并压缩空白。"""
        text = "".join(self._parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return unescape(text).strip()


def html_to_markdown(html: str) -> str:
    """将 HTML 转为 Markdown 近似文本；超过上限时截断。"""
    if not html:
        return ""
    extractor = _MarkdownExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:  # noqa: BLE001 — 畸形 HTML 尽力提取已产出内容
        pass
    markdown = extractor.markdown()
    if len(markdown) > MAX_MARKDOWN_CHARS:
        markdown = markdown[:MAX_MARKDOWN_CHARS] + "\n\n（内容过长已截断）"
    return markdown
