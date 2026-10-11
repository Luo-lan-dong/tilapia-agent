# -*- coding: utf-8 -*-
"""版本号 patch+1（UTF-8 无 BOM，自动读当前版本）。"""
import pathlib
import re

p = pathlib.Path("pyproject.toml")
c = p.read_text(encoding="utf-8")
m = re.search(r'^version = "(\d+)\.(\d+)\.(\d+)"', c, re.M)
assert m, "未找到版本行"
major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3)) + 1
new = f'version = "{major}.{minor}.{patch}"'
p.write_bytes(c.replace(m.group(0), new).encode("utf-8"))
print("新版本号:", new)
