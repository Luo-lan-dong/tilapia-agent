"""编程 Agent 任务执行层。

按 ADR-012/ADR-013，本包承载 tilacode server 进程管理与后续的
HTTP API 客户端、WebSocket ↔ HTTP 桥接等任务桥接组件。

ADR-055：本包的重 IO 函数（文件扫描、产物打包、配置读写）均为同步实现，
调用方**必须**经 ``asyncio.to_thread`` 调用，避免阻塞事件循环。
"""
