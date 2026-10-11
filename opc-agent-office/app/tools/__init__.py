"""办公 Agent 工具层（ADR-062）。

四件套工具：write_docx / write_pptx / web_search / web_fetch。
工具为同步阻塞实现，调用方必须经 ``asyncio.to_thread`` 调用 ``execute``。
"""
