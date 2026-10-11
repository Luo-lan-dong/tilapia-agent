"""Agent 模板服务：扫描静态目录并返回模板版本列表。"""

import logging
from pathlib import Path

from structlog import contextvars

from app.schemas.agent_template import AgentTemplateListResponse, AgentTemplateVersion

logger = logging.getLogger(__name__)

# 静态目录挂载前缀，与 main.py 中 StaticFiles(directory="static") 保持一致
_STATIC_ROOT = "static"
_AGENT_TEMPLATES_DIR = "agent-templates"
_STATIC_URL_PREFIX = "/static"


def _bind_request_id() -> None:
    """将当前上下文的 request_id 绑定到日志上下文。"""
    request_id = contextvars.get_contextvars().get("request_id")
    if request_id:
        contextvars.bind_contextvars(request_id=request_id)


class AgentTemplateService:
    """Agent 模板扫描服务。

    扫描 ``static/agent-templates/{agent_type}/{version}/`` 目录结构，
    返回每个版本对应的模板下载路径。
    """

    def list_templates(
        self,
        agent_type: str | None = None,
    ) -> AgentTemplateListResponse:
        """扫描模板目录并返回版本列表。

        Args:
            agent_type: 可选的 Agent 类型过滤，为 None 时返回全部类型。

        Returns:
            模板版本列表响应。
        """
        templates_root = Path(_STATIC_ROOT) / _AGENT_TEMPLATES_DIR
        logger.info(
            "agent_template_list_started",
            extra={
                "event": "agent_template_list_started",
                "agent_type": agent_type or "all",
                "templates_root": str(templates_root),
            },
        )

        if not templates_root.is_dir():
            logger.warning(
                "agent_template_dir_not_found",
                extra={
                    "event": "agent_template_dir_not_found",
                    "templates_root": str(templates_root),
                },
            )
            return AgentTemplateListResponse(templates=[])

        # 确定需要扫描的 Agent 类型子目录
        if agent_type:
            type_dirs: list[Path] = [
                templates_root / agent_type,
            ]
        else:
            type_dirs = [
                entry
                for entry in templates_root.iterdir()
                if entry.is_dir() and not entry.name.startswith(".")
            ]

        templates: list[AgentTemplateVersion] = []
        for type_dir in type_dirs:
            if not type_dir.is_dir():
                continue
            current_agent_type = type_dir.name
            for version_dir in sorted(type_dir.iterdir()):
                if not version_dir.is_dir() or version_dir.name.startswith("."):
                    continue
                download_path = self._resolve_download_path(version_dir)
                if download_path is None:
                    continue
                templates.append(
                    AgentTemplateVersion(
                        version=version_dir.name,
                        download_path=download_path,
                        agent_type=current_agent_type,
                    )
                )

        logger.info(
            "agent_template_list_completed",
            extra={
                "event": "agent_template_list_completed",
                "agent_type": agent_type or "all",
                "count": len(templates),
            },
        )
        return AgentTemplateListResponse(templates=templates)

    @staticmethod
    def _resolve_download_path(version_dir: Path) -> str | None:
        """在版本目录中查找第一个 .tar.gz 文件并返回其静态 URL 路径。

        Args:
            version_dir: 版本目录路径。

        Returns:
            形如 ``/static/agent-templates/{type}/{version}/{file}`` 的下载路径；
            若目录中无 .tar.gz 文件则返回 None。
        """
        archive_files = sorted(version_dir.glob("*.tar.gz"))
        if not archive_files:
            return None
        relative_path = archive_files[0].relative_to(Path(_STATIC_ROOT))
        return f"{_STATIC_URL_PREFIX}/{relative_path.as_posix()}"
