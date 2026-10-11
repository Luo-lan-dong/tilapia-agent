"""中枢桌面模式启动器（ADR-021）。

负责：单实例锁、端口探测、内嵌 PostgreSQL 生命周期、
数据库迁移与初始数据、uvicorn 服务线程、pywebview 桌面窗口。
"""
