#!/usr/bin/env python3
"""尺寸字面量门禁（docs/standards/frontend-component-standards.md §3.2，ADR-065）。

禁止 ``src/**`` 出现裸写的圆角 / 内边距 / 间距 / 字号数值，避免"同一个组件
有的圆有的方"，并保证"改一处（token）全站生效"。

唯一数据源（白名单）：``src/theme/tokens.ts``、``src/index.css``、
``src/theme/components.ts``；其余文件如需豁免，在文件头注释写
``token-override: <理由>``。

用法::

    python scripts/check_tokens.py            # 报告（默认 exit 0，迁移期接入 CI 不阻断）
    python scripts/check_tokens.py --strict   # 迁移完成后启用：发现即 exit 1
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

#: 唯一数据源：这些文件里允许出现尺寸字面量
WHITELIST = {
    "src/theme/tokens.ts",
    "src/index.css",
    "src/theme/components.ts",
}

#: 文件头出现该标记即整文件豁免（需写明理由）
EXEMPT_MARK = "token-override:"

#: 检测规则；``0`` 视为重置（如 margin: 0），不算设计取值，故只匹配 >=1
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("radius", re.compile(r"borderRadius\s*:\s*['\"]?[1-9]")),
    ("spacing", re.compile(r"\b(?:padding|margin|gap|rowGap|columnGap)\w*\s*:\s*['\"]?[1-9]")),
    ("font-size", re.compile(r"fontSize\s*:\s*['\"]?[1-9]")),
)

SUFFIXES = {".ts", ".tsx", ".css"}


def iter_targets() -> list[Path]:
    """返回需要检查的文件（按路径排序，便于复现输出顺序）。"""
    return [
        path
        for path in sorted(SRC.rglob("*"))
        if path.is_file() and path.suffix in SUFFIXES
    ]


def main() -> int:
    """扫描并报告；``--strict`` 时发现即返回 1。"""
    strict = "--strict" in sys.argv
    hits: list[str] = []
    for path in iter_targets():
        relative = path.relative_to(ROOT).as_posix()
        if relative in WHITELIST:
            continue
        text = path.read_text(encoding="utf-8")
        if EXEMPT_MARK in text:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS:
                if pattern.search(line):
                    hits.append(f"{relative}:{lineno}: [{name}] {line.strip()[:120]}")

    if not hits:
        print("[check_tokens] 通过：未发现尺寸字面量")
        return 0

    for item in hits:
        print(item)
    print(
        f"\n[check_tokens] 共 {len(hits)} 处尺寸字面量：请改用 token 或语义 CSS 变量"
        "（见 docs/standards/frontend-component-standards.md §3.2）"
    )
    return 1 if strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
