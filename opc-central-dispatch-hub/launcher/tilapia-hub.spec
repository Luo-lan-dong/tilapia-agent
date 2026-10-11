# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：TilapiaHub 桌面版（ADR-021）。

用法（在 opc-central-dispatch-hub 目录，已激活含 desktop 依赖的 venv）：
    pyinstaller launcher/tilapia-hub.spec --noconfirm

产物：dist/tilapia-hub/tilapia-hub.exe（onedir 模式）
随包数据：alembic/、alembic.ini、static/、web/、assets/
注意：pgsql/ 便携版由 scripts/prepare_desktop_bundle.py 或 Inno Setup 单独放置。
"""

import os
from pathlib import Path

HUB_ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(HUB_ROOT / "alembic"), "alembic"),
    (str(HUB_ROOT / "alembic.ini"), "."),
    (str(HUB_ROOT / "static"), "static"),
    (str(HUB_ROOT / "assets"), "assets"),
]
web_dir = HUB_ROOT / "web"
if (web_dir / "index.html").is_file():
    datas.append((str(web_dir), "web"))

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "asyncpg",
    "alembic",
    # 中枢 PTY 终端（ADR-053）：winpty 为 try-import，需显式声明
    "winpty",
    # alembic env.py 依赖的应用模块与模型注册
    "app.core.config",
    "app.models.base",
    "app.models.agent_node",
    "app.models.llm_provider",
    "app.models.system_settings",
    "app.models.system_version",
    "app.models.task",
    "app.models.task_building_session",
]

a = Analysis(
    [str(HUB_ROOT / "launcher" / "__main__.py")],
    pathex=[str(HUB_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tilapia-hub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 桌面形态无控制台窗口（日志走 data/logs/ 文件）
    # console=True,  # 保留控制台便于首版排查；后续可切 windowed
    icon=str(HUB_ROOT / "assets" / "tilapia-icon.ico"),  # exe/任务栏/资源管理器图标
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="tilapia-hub",
)
