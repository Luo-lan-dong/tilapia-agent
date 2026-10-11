"""SOP 知识条目表 + 五类场景要点 seed（ADR-051）。

Revision ID: 20260926_sop
Revises: 20260926_experience
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260926_sop"
down_revision = "20260926_experience"
branch_labels = None
depends_on = None

#: 内置场景要点（builtin seed）：仅放模型无法自行推导/实测差异点，
#: 通用流程交给模型自主编排
_SEED_SOPS = [
    (
        "HTML 静态站要点",
        "html",
        '- SPA 应用需 `HtmlProjectModify(action=set_run_path, run_path="/dist")` 指向构建产物子目录\n'
        "- 访问 404 优先查运行目录与伪静态\n"
        "- 产物目录不存在时 HtmlProjectCreate 会自动创建",
    ),
    (
        "Python 项目要点",
        "python",
        "- 先 `PythonProjectCreate(analyze_only=true)`（只读）获取 framework/rfile/call_app/xsgi/port\n"
        "- 运行方式：Django/Flask→stype=gunicorn(wsgi)；FastAPI/Sanic→gunicorn+xsgi=asgi；无框架→stype=command\n"
        "- 创建后轮询 `PythonProjectInfo` 至 prep_status=complete；failure 立即读 log_files 环境准备日志\n"
        "- 虚拟环境必须先于项目创建（PythonEnv can_use_directly 才能建项目）",
    ),
    (
        "NodeJS 项目要点",
        "nodejs",
        "- 先 `NodeProjectCreate(analyze_only=true)` 获取 scripts/engines\n"
        "- 启动方式：nodejs 用 project_script；general 用 project_file+project_args；pm2 用 project_file+cluster；nodejs_version 均必填\n"
        "- **nodejs 类型 env 存了不生效**：需要环境变量改用 general 类型\n"
        "- 创建即自动启动，NodeProjectInfo 核验 run/listen",
    ),
    (
        "Go 项目要点",
        "go",
        "- Go 为编译产物：确认 linux-amd64 二进制/监听端口/启动命令后直接 GoProjectCreate\n"
        "- 创建即同步启动（nohup+pid，无守护进程）\n"
        "- `GoProjectModify(action=config)` 改配置自动重启\n"
        "- 未启动时 stop/restart 返回「项目未启动」属正常语义",
    ),
    (
        "PHP 网站要点",
        "php",
        "- `SiteCreate` 需 PHP 版本：先 SoftwareList 确认已装版本，缺则 SoftwareInstall\n"
        "- 需数据库先 `DatabaseCreate`，凭据写入站点配置（Edit，勿硬编码进发布产物）\n"
        "- `SiteGetConfig` 核验配置；`SiteConfig` 调整伪静态；日志 `SiteLogs`\n"
        "- HTTP 访问验证（Host 头直连）；404/500 优先查 PHP 版本匹配与入口文件",
    ),
]


def upgrade() -> None:
    op.create_table(
        "sop_records",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="主键 ID",
        ),
        sa.Column("name", sa.String(length=100), nullable=False, comment="SOP 名称（唯一）"),
        sa.Column(
            "scene",
            sa.String(length=30),
            nullable=False,
            comment="场景标识（如 html/php/python 或自定义项目标识）",
        ),
        sa.Column("content", sa.Text(), nullable=False, comment="SOP 正文（Markdown，要点式）"),
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default="learned",
            comment="来源：builtin（内置 seed）/ learned（对话固化）",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="是否启用（禁用后 bt_deploy_guide 不命中）",
        ),
        sa.Column(
            "hit_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="命中次数（衡量 SOP 价值）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="更新时间",
        ),
        sa.UniqueConstraint("name", name="uk_sop_records_name"),
        comment="SOP 知识条目（ADR-051）：builtin 场景要点 + 对话固化的项目 SOP",
    )
    op.create_index("idx_sop_records_scene", "sop_records", ["scene"])
    for name, scene, content in _SEED_SOPS:
        op.execute(
            sa.text(
                "INSERT INTO sop_records (name, scene, content, source, enabled, hit_count)"
                " VALUES (:name, :scene, :content, 'builtin', true, 0)"
            ).bindparams(name=name, scene=scene, content=content)
        )


def downgrade() -> None:
    op.drop_index("idx_sop_records_scene", table_name="sop_records")
    op.drop_table("sop_records")
