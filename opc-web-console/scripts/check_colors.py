"""颜色字面量防回潮门禁（ADR-052）。

检查 opc-web-console/src 下 ts/tsx/css 文件不得出现颜色字面量
（#hex / rgb() / rgba()），白名单外出现即退出码 1。

白名单（唯一允许定义颜色的位置）：
- theme/tokens.ts：设计色板双态常量（单一事实源）
- index.css：主题语义 CSS 变量定义（:root / html[data-theme='light']）
- components/WindowControls.css：仅放行 Windows 关闭钮惯例红 #e81123 与 #fff
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src"

# 文件级白名单：相对 src 路径 -> 允许的颜色字面量模式（None 表示全部放行）
WHITELIST: dict[str, re.Pattern[str] | None] = {
    "theme/tokens.ts": None,
    "index.css": None,
    "components/WindowControls.css": re.compile(r"^#(e81123|fff)$"),
}

PATTERNS = [
    re.compile(r"#[0-9a-fA-F]{3,8}\b"),
    re.compile(r"rgba?\([^)]+\)"),
]

violations: list[str] = []
for path in sorted(ROOT.rglob("*")):
    if path.suffix not in {".tsx", ".ts", ".css"}:
        continue
    rel = path.relative_to(ROOT).as_posix()
    in_whitelist = False
    allowed_pattern: re.Pattern[str] | None = None
    for prefix, pattern in WHITELIST.items():
        if rel == prefix or rel.endswith("/" + prefix):
            in_whitelist = True
            allowed_pattern = pattern
            break
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern in PATTERNS:
            for match in pattern.findall(line):
                if not in_whitelist:
                    violations.append(f"{rel}:{lineno}: {match}")
                elif allowed_pattern is not None and not allowed_pattern.match(match.lower()):
                    violations.append(f"{rel}:{lineno}: {match}")

if violations:
    print("发现白名单外的颜色字面量（ADR-052 防回潮）：")
    for item in violations:
        print(f"  {item}")
    print("组件颜色请改用 var(--*) 语义变量；新增语义色先在 src/index.css 定义变量。")
    sys.exit(1)

print(f"颜色字面量检查通过（白名单 {len(WHITELIST)} 个文件）。")
