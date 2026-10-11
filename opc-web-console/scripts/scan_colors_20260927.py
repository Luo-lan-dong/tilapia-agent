"""颜色字面量扫描：统计前端源码中硬编码颜色的文件/色值分布。"""

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path("src")
# 常见颜色形态：#hex(3/6/8)、rgba()/rgb()、命名色（少量）
PATTERNS = [
    re.compile(r"#[0-9a-fA-F]{3,8}\b"),
    re.compile(r"rgba?\([^)]+\)"),
]
SKIP_FILES = {"tokens.ts"}  # token 源本身允许定义颜色

distribution: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
for path in ROOT.rglob("*"):
    if path.suffix not in {".tsx", ".ts", ".css"}:
        continue
    rel = str(path).replace("\\", "/")
    if any(skip in rel for skip in SKIP_FILES):
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for pattern in PATTERNS:
        for match in pattern.findall(text):
            key = match.lower()
            distribution[rel][key] += 1

total = 0
per_file_lines = []
for file_path, colors in sorted(
    distribution.items(), key=lambda item: -sum(item[1].values())
):
    file_total = sum(colors.values())
    total += file_total
    top = ", ".join(f"{color}×{count}" for color, count in sorted(colors.items(), key=lambda c: -c[1])[:6])
    per_file_lines.append(f"| {file_path} | {file_total} | {top} |")

print(f"TOTAL: {total}")
print("| 文件 | 处数 | 主要色值 |")
print("| --- | --- | --- |")
for line in per_file_lines:
    print(line)
