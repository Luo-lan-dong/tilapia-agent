"""宝塔部署知识（ADR-051）：行为约束 + 通用坑地图（代码常量）+ 场景白名单（防线）。

知识分层（ADR-051，取代 ADR-049 的五份手把手 SOP 常量）：
- 行为约束/通用坑地图/排障精要：代码常量——模型不可能自行知道的实测坑与
  不可妥协的行为约束；
- 类型/项目 SOP：``sop_records`` 表（builtin seed + 跑通后经 sop_save 固化的
  learned），``bt_deploy_guide`` 读库按需注入，「记忆 → SOP」Tab 管理；
- 项目类型判定：模型自主（解压产物看特征，判不准问用户），不写特征映射表；
- 场景工具白名单：``SCENE_TOOL_WHITELISTS`` 是 bt_mcp_call 软提示防线（代码），
  与知识无关，保留。
"""

from typing import Any

#: 部署行为约束（所有场景共享，置于指引开头）
_COMMON_RULES = """## 部署约束（必读）

1. **规划先行**：确认目标服务器后，第一轮**不执行任何变更类工具**——先向用户
   输出部署规划（目标服务器/项目类型/运行条件如是否建库/端口与域名方案——
   **必须询问用户对端口的要求**/防火墙放行/分步计划），确认后调用
   `deploy_plan` 把待办写入「任务清单」面板再逐步执行并更新勾选。
2. **项目类型自主判定**：解压产物后按产物特征自行判断项目类型（你的通用
   知识即可，判不准直接问用户），判定后调用 `bt_deploy_guide(project_type=
   <类型>)` 获取该场景要点；同一产物含多种特征时向用户确认主类型。
3. **参数逐字一致**：调用前 `bt_mcp_schema` 查工具入参，参数名照抄 schema，
   禁止凭记忆或近似名传参；查过 schema 也要逐字对照后再调用。
4. **先只读侦察再变更**：ServerIP/SystemInfo/SoftwareList 确认环境；创建
   成功 ≠ 启动成功——异步准备类项目必须轮询 Info 工具核验 run/listen。
5. **产物包上服务器（两条路，优先 Upload）**：
   - **优先**：`bt_mcp_call(tool="Upload", arguments={file_name, size})`——
     中枢会自动代执行本机上传（返回 hub_note 与服务器绝对 path）；文件落在
     服务器临时目录（root:root/0600），用 `Bash: mv` 到站点目录再解压；
   - 备选：附件占位里若有「下载地址」（需中枢 hub_public_url 已配置），在
     服务器 `Bash: curl -L '<地址>' -o /tmp/<文件名>` 拉取；
   - 解压：zip 用 `unzip -o`；tar.gz 用 `tar -xzf`；rar/7z 需先安装解压工具
     （建议用户改传 zip/tar.gz）。解压后确认产物结构再继续。
6. **限额与等待**：部署流程长，每任务工具调用预算 **40 次**（不要在 15 次附近
   提前收尾）；**后台任务会等待**——`Bash` 超 20 秒转后台返回 msg=done，从返回
   数据取 task_id，用 `BashStatus(task_id, wait=true)` 阻塞等待结果（这算一次
   调用，可多次调用直到任务收敛）；npm install / 构建 / pip 安装等长任务都要
   等到完成并核验结果后才能进行下一步，不要因等待而中断流程。删除/防火墙/
   重启类高危操作必须先把内容与影响告知用户（服务端 confirm 两段式）；不删
   项目源码、不读凭据文件。
"""

#: 通用坑地图（实测沉淀，模型无法自行知道的坑——部署指引的核心价值）
_PITFALLS = """## 实测坑清单（务必遵守）

- **Upload 工具描述的误导**：工具描述称"调用方必须能在客户端本机执行 curl"——
  在当前中枢环境**这不成立**：中枢会自动代执行本机上传。直接
  `bt_mcp_call(tool="Upload", arguments={file_name, size})` 调用即可（file_name
  用用户上传附件的原名），返回会带服务器落盘绝对路径；**不要因该描述放弃上传**。
- **工具族互斥**：HTML 静态站只用 `HtmlProject` 族；PHP 网站用 `Site` 族——
  两者是宝塔独立模块，面板查看位置分别是「网站 → HTML项目 / PHP项目」Tab；
  查不到 ≠ 站点不存在，先确认工具族再用。
- **端口写法坑**：`HtmlProjectCreate` 传「域名:端口」（如 `192.168.x.x:8080`）
  会报「端口范围不合法」——先以纯 IP/域名创建，再用 `HtmlProjectModify`
  调整监听端口。
- **参数名差异**：`HtmlProjectInfo` 入参是 `site_name`（不是 project_name）；
  MCP 的 `Write` 工具入参是 `file_path`（不是 path）。
- **绑定域名两步**：`add_domain` 只写域名记录，`bind_extranet` 才生成 nginx
  映射；path→port 反代（add_proxy）必须先开外网映射，否则 403。
- **伪静态生效**：改 nginx conf 后需 `SiteConfig(action=reload)`。
- **证书**：`SiteCertList` 查有效期；续期 `SiteSSLApply`（Let's Encrypt，http 验证）。
- **Bash 前台 20 秒上限**：命令超 20 秒自动转后台、返回 msg=done——此时取
  返回数据里的 task_id，用 `BashStatus(task_id, wait=true)` 查询真实输出；
  不要把 msg=done 当成命令没有输出。
"""

_TROUBLESHOOT = """## 通用排障要点（网站打不开/启动失败）

1. **定位层次**：域名解析 → 端口监听（Info 工具的 listen）→ 应用进程（run/pid）→ 应用日志 → nginx 反代配置 → 防火墙/安全组。
2. **日志优先**：站点访问日志 `SiteLogs`；错误日志经 log_files 路径用 Read 查看；容器 `ContainerLogs`。
3. **502/504**：应用没起或端口不对——先核验应用 run/listen，再查反代 proxy_port 是否指向应用端口。
4. **403**：绑了域名但没开外网映射/反代；或运行目录不对。
5. **服务异常**：`ServiceStatus` 查 nginx/mysql 状态；`ServiceControl` 重启前须向用户确认。
"""

TROUBLESHOOT_SOP = _TROUBLESHOOT
COMMON_RULES = _COMMON_RULES
PITFALLS = _PITFALLS

#: 各部署场景共享的基础设施工具（文件/命令/系统/数据库/服务/证书/日志/nginx 重载）。
#: 注意：Site 族中仅证书/日志/配置重载类属于通用能力；SiteList/SiteCreate/
#: SiteGetConfig/SiteDelete 属 PHP 网站管理，不进任何场景白名单（issues
#: 2026-09-26 问题二：AI 用网站族工具查 HTML 静态站导致"能访问但面板看不到"）。
_SHARED_INFRA_TOOLS: set[str] = {
    # 基础文件与命令
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "LS",
    "Bash",
    "BashStatus",
    "BashStop",
    "WebFetch",
    # 系统/软件/上传
    "ServerIP",
    "SystemInfo",
    "SoftwareList",
    "SoftwareInstall",
    "Upload",
    # 数据库（通用查询与建库）
    "DatabaseList",
    "DatabaseCreate",
    "DatabaseBackup",
    "MysqlQuery",
    # 服务与防火墙
    "ServiceStatus",
    "ServiceControl",
    "FirewallStatus",
    "FirewallPortList",
    "FirewallPortSet",
    # nginx 通用（证书/日志/配置重载）
    "SiteCertList",
    "SiteSSLApply",
    "SiteLogs",
    "SiteConfig",
    "SiteControl",
}

#: PHP 网站管理专有工具族（php 部署场景的核心工具；其他部署场景误用它们时
#: 触发软提示，issues 2026-09-26 问题二）
PHP_SITE_MGMT_TOOLS: set[str] = {
    "SiteList",
    "SiteCreate",
    "SiteGetConfig",
    "SiteDelete",
    "SiteBackup",
    "SiteTraffic",
    "DomainManage",
    "OneClickDeploy",
    "TrafficAnalysis",
}

#: 场景工具白名单（ADR-050 第一层：事前圈定）。
#: bt_deploy_guide 返回时附带；bt_mcp_call 对"出白名单的族工具"做软提示
#: （只提示不阻断，SOP 未覆盖的场景不能被白名单卡死）。
SCENE_TOOL_WHITELISTS: dict[str, set[str]] = {
    "python": _SHARED_INFRA_TOOLS
    | {
        "PythonVersion",
        "PythonEnv",
        "PythonProjectCreate",
        "PythonProjectInfo",
        "PythonProjectControl",
        "PythonProjectService",
        "PythonProjectModify",
    },
    "nodejs": _SHARED_INFRA_TOOLS
    | {
        "NodeVersion",
        "NodeProjectCreate",
        "NodeProjectInfo",
        "NodeProjectControl",
        "NodeProjectModify",
    },
    "go": _SHARED_INFRA_TOOLS
    | {
        "GoVersion",
        "GoProjectCreate",
        "GoProjectInfo",
        "GoProjectControl",
        "GoProjectModify",
    },
    "html": _SHARED_INFRA_TOOLS | {"HtmlProjectInfo", "HtmlProjectCreate", "HtmlProjectModify"},
    "php": _SHARED_INFRA_TOOLS | PHP_SITE_MGMT_TOOLS | {"SiteSSLDeploy"},
}

#: 场景标题（指引段落标题 + 会话内场景识别）
_SCENE_TITLES: dict[str, str] = {
    "python": "Python 项目部署",
    "nodejs": "NodeJS 项目部署",
    "go": "Go 项目部署",
    "html": "HTML 静态项目部署",
    "php": "PHP 网站部署",
}


def detect_scene_from_messages(messages: list[dict[str, Any]]) -> str | None:
    """从会话消息中识别当前部署场景（AI 是否读过某类指引）。

    bt_mcp_call 软提示用：检测 tool 消息结果中是否包含某场景标题，
    返回场景 key（如 "html"）；未读过时返回 None（不提示）。
    """
    for message in reversed(messages or []):
        text = str(message.get("content") or "")
        for scene, title in _SCENE_TITLES.items():
            if f"## {title}" in text:
                return scene
    return None


def whitelist_section(project_type: str | None) -> str:
    """生成场景工具白名单段落（附在对应指引之后）。"""
    if not project_type or project_type.lower() not in SCENE_TOOL_WHITELISTS:
        return ""
    scene = project_type.lower()
    tools = sorted(SCENE_TOOL_WHITELISTS[scene])
    other_families = {
        "python": "SiteList/SiteCreate（PHP 网站）、NodeProject*/GoProject*/HtmlProject*",
        "nodejs": "SiteList/SiteCreate（PHP 网站）、PythonProject*/GoProject*/HtmlProject*",
        "go": "SiteList/SiteCreate（PHP 网站）、PythonProject*/NodeProject*/HtmlProject*",
        "html": "SiteList/SiteCreate/SiteGetConfig（PHP 网站）、PythonProject*/NodeProject*/GoProject*",
        "php": "HtmlProject*/PythonProject*/NodeProject*/GoProject*（各运行时项目族）",
    }
    return (
        "## 本场景工具白名单\n\n"
        f"本场景（{project_type}）应使用的工具（其他场景的 Project 族/网站族工具"
        "不要串用，查不到本类项目）：\n"
        + "、".join(tools)
        + "\n\n不应使用（易串族）："
        + other_families[scene]
        + "。调用不在白名单内的工具时系统会提示注意工具族。\n"
    )


def get_deploy_guide_text(project_type: str | None, scene_sop: str | None = None) -> str:
    """组装部署指引（ADR-051）：约束 + 命中 SOP + 坑地图 + 白名单 + 排障。

    project_type 缺失时返回类型概览（AI 解压产物自主判型后再调用）。
    scene_sop 为 sop_records 命中的场景要点/固化 SOP（由技能层查库传入，
    命中时技能层已累计 hit_count）。
    """
    header = "以下为宝塔面板部署指引（工具均通过 bt_mcp_call 调用）。\n\n" + _COMMON_RULES
    if project_type and project_type.lower() in SCENE_TOOL_WHITELISTS:
        scene = project_type.lower()
        title = _SCENE_TITLES[scene]
        sop_block = (
            f"\n## {title}（场景要点）\n\n{scene_sop}\n"
            if scene_sop
            else (
                f"\n## {title}（场景要点）\n\n暂无该场景的固化 SOP——按工具目录"
                "（bt_mcp_tools）与各工具 schema 自主编排；跑通后可询问用户固化。\n"
            )
        )
        return (
            header
            + sop_block
            + "\n"
            + _PITFALLS
            + "\n"
            + whitelist_section(scene)
            + "\n"
            + _TROUBLESHOOT
        )
    overview = "\n".join(f"- {key}: {title}" for key, title in _SCENE_TITLES.items())
    return (
        header
        + "\n## 未指定项目类型\n"
        + "请先解压产物按特征判定项目类型（判不准问用户），然后再次调用本技能"
        "（project_type 参数）获取场景要点。已内置类型：\n"
        + overview
        + "\n其他类型（.NET/Java 等）同样支持：自主判定后传入 project_type"
        "（无固化要点时按坑地图与 schema 编排）。\n"
    )
