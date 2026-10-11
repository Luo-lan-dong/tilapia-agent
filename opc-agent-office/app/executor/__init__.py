"""办公 Agent 任务执行层（ADR-062）。

本包承载办公任务的桥接层（office_bridge）、LLM 工具循环（tool_loop）、
OpenAI 兼容客户端（llm_client）与产物清单扫描（file_scanner，复用编程节点）。

规范：本包的重 IO 函数（文件扫描、打包）均为同步实现，调用方**必须**经
``asyncio.to_thread`` 调用，避免阻塞事件循环。
"""
