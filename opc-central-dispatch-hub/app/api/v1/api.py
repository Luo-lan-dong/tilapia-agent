"""v1 API 路由聚合。"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agent_node_management,
    agent_node_ws,
    agent_nodes,
    agent_templates,
    attachments,
    bt_mcp,
    client_logs,
    dashboard,
    experience,
    health,
    logs,
    resource_settings,
    settings,
    skills,
    sops,
    standard_docs,
    task_artifacts,
    task_building,
    task_building_session,
    task_dispatch,
    tasks,
    terminal,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health")
api_router.include_router(client_logs.router, prefix="/client-logs")
api_router.include_router(dashboard.router, prefix="/dashboard")
api_router.include_router(logs.router, prefix="/logs")
api_router.include_router(settings.router, prefix="/settings")
api_router.include_router(resource_settings.router, prefix="/settings/resources")
api_router.include_router(skills.router, prefix="/skills")
api_router.include_router(standard_docs.router, prefix="/docs")
api_router.include_router(bt_mcp.router, prefix="/bt-mcp/servers")
api_router.include_router(experience.router, prefix="/experience")
api_router.include_router(sops.router, prefix="/sops")
api_router.include_router(terminal.router, prefix="/terminal")
api_router.include_router(tasks.router, prefix="/tasks")
api_router.include_router(task_building.router, prefix="/tasks/build")
api_router.include_router(task_building_session.router, prefix="/tasks/build")
api_router.include_router(task_dispatch.router, prefix="/tasks")
api_router.include_router(task_artifacts.router, prefix="/tasks/{task_id}/artifacts")
api_router.include_router(attachments.router, prefix="/sessions/{session_id}/attachments")
api_router.include_router(attachments.download_router, prefix="/tasks/{task_id}/attachments")
api_router.include_router(task_dispatch.subsystem_router, prefix="/subsystems")
api_router.include_router(agent_nodes.router, prefix="/agent-nodes")
api_router.include_router(agent_node_management.router, prefix="/agent-nodes")
api_router.include_router(agent_node_ws.router, prefix="/agent-nodes")
api_router.include_router(
    agent_templates.router,
    prefix="/agent-templates",
    tags=["agent-templates"],
)
