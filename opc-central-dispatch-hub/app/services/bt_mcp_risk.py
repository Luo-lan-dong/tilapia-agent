"""宝塔 MCP 工具分类与风险等级映射（ADR-049）。

来源：宝塔官方《宝塔 MCP 工具说明》（98 个工具，2026-09 版）。
风险等级仅用于 AI 判断与前端展示，**权限管控在服务器端插件**（用户决策），
本映射不做本地拦截。
"""

#: 工具名 → 分类（官方文档分类）
TOOL_CATEGORIES: dict[str, str] = {
    # 基础
    "Read": "基础",
    "Edit": "基础",
    "Write": "基础",
    "Glob": "基础",
    "Grep": "基础",
    "LS": "基础",
    "Bash": "基础",
    "BashStatus": "基础",
    "BashStop": "基础",
    # 网站
    "SiteList": "网站",
    "SiteGetConfig": "网站",
    "SiteLogs": "网站",
    "SiteTraffic": "网站",
    "SiteCreate": "网站",
    "SiteDelete": "网站",
    "OneClickDeploy": "网站",
    "TrafficAnalysis": "网站",
    "SiteCertList": "网站",
    "SiteSSLDeploy": "网站",
    "SiteSSLApply": "网站",
    "DomainManage": "网站",
    "SiteConfig": "网站",
    "SiteControl": "网站",
    "SiteBackup": "网站",
    # 网络
    "WebFetch": "网络",
    "ServerIP": "网络",
    "Upload": "网络",
    # 数据库
    "DatabaseList": "数据库",
    "DatabaseCreate": "数据库",
    "DatabaseDelete": "数据库",
    "DatabaseBackup": "数据库",
    "MysqlQuery": "数据库",
    "MysqlExecute": "数据库",
    # 服务
    "ServiceStatus": "服务",
    "ServiceControl": "服务",
    # Docker
    "ContainerList": "Docker",
    "ContainerLogs": "Docker",
    "ContainerInspect": "Docker",
    "ComposeList": "Docker",
    "ImageList": "Docker",
    "VolumeList": "Docker",
    "NetworkList": "Docker",
    "ContainerControl": "Docker",
    "ContainerDelete": "Docker",
    "ImagePull": "Docker",
    # 系统 / 软件
    "SystemInfo": "系统",
    "SoftwareList": "软件",
    "SoftwareInstall": "软件",
    "SoftwareUninstall": "软件",
    # 防火墙
    "FirewallStatus": "防火墙",
    "FirewallPortList": "防火墙",
    "FirewallIpList": "防火墙",
    "FirewallPortSet": "防火墙",
    "FirewallIpSet": "防火墙",
    # Java
    "JavaJdk": "Java",
    "JavaProjectCreate": "Java",
    "JavaProjectInfo": "Java",
    "JavaProjectControl": "Java",
    "JavaProjectModify": "Java",
    "JavaProjectDelete": "Java",
    # NodeJS
    "NodeVersion": "NodeJS",
    "NodeProjectCreate": "NodeJS",
    "NodeProjectInfo": "NodeJS",
    "NodeProjectControl": "NodeJS",
    "NodeProjectModify": "NodeJS",
    "NodeProjectDelete": "NodeJS",
    # Python
    "PythonVersion": "Python",
    "PythonEnv": "Python",
    "PythonProjectCreate": "Python",
    "PythonProjectInfo": "Python",
    "PythonProjectControl": "Python",
    "PythonProjectService": "Python",
    "PythonProjectModify": "Python",
    "PythonProjectDelete": "Python",
    # Go
    "GoVersion": "Go",
    "GoProjectCreate": "Go",
    "GoProjectInfo": "Go",
    "GoProjectControl": "Go",
    "GoProjectModify": "Go",
    "GoProjectDelete": "Go",
    # 反向代理
    "ProxyProjectCreate": "反向代理",
    "ProxyProjectInfo": "反向代理",
    "ProxyProjectModify": "反向代理",
    "ProxyWriteConfig": "反向代理",
    "ProxyProjectDelete": "反向代理",
    # HTML
    "HtmlProjectInfo": "HTML",
    "HtmlProjectCreate": "HTML",
    "HtmlProjectModify": "HTML",
    "HtmlProjectDelete": "HTML",
    # SSH / 安全
    "SSHInfo": "SSH",
    "SSHConfig": "SSH",
    "SSHIntrusion": "SSH",
    "SecurityCheck": "安全",
    # 计划任务 / 通知
    "GetCrontab": "计划任务",
    "ManageCrontab": "计划任务",
    "GetChannel": "通知",
    "ManageChannel": "通知",
    "SendMessage": "通知",
}

#: high / medium 清单（见上）
_HIGH_TOOLS = (
    "Edit",
    "Write",
    "Bash",
    "SiteDelete",
    "DomainManage",
    "DatabaseDelete",
    "MysqlExecute",
    "ContainerDelete",
    "SoftwareUninstall",
    "FirewallPortSet",
    "FirewallIpSet",
    "SSHConfig",
    "JavaProjectDelete",
    "NodeProjectDelete",
    "PythonProjectDelete",
    "GoProjectDelete",
    "ProxyProjectDelete",
    "HtmlProjectDelete",
    "ManageCrontab",
    "ManageChannel",
)
_MEDIUM_TOOLS = (
    "SiteCreate",
    "OneClickDeploy",
    "SiteSSLDeploy",
    "SiteSSLApply",
    "SiteConfig",
    "SiteControl",
    "SiteBackup",
    "Upload",
    "DatabaseCreate",
    "DatabaseBackup",
    "ServiceControl",
    "ContainerControl",
    "ImagePull",
    "SoftwareInstall",
    "JavaJdk",
    "JavaProjectCreate",
    "JavaProjectControl",
    "JavaProjectModify",
    "NodeVersion",
    "NodeProjectCreate",
    "NodeProjectControl",
    "NodeProjectModify",
    "PythonVersion",
    "PythonEnv",
    "PythonProjectCreate",
    "PythonProjectControl",
    "PythonProjectService",
    "PythonProjectModify",
    "GoVersion",
    "GoProjectCreate",
    "GoProjectControl",
    "GoProjectModify",
    "ProxyProjectCreate",
    "ProxyProjectModify",
    "ProxyWriteConfig",
    "HtmlProjectCreate",
    "HtmlProjectModify",
    "SendMessage",
    "BashStop",
)

TOOL_RISKS: dict[str, str] = {
    # high：删除/覆盖/执行命令，服务端要求两段式 confirm
    **dict.fromkeys(_HIGH_TOOLS, "high"),
    # medium：有副作用（创建/配置/启停/备份/上传）
    **dict.fromkeys(_MEDIUM_TOOLS, "medium"),
    # low：已登记分类中的其余工具均为只读
    **{
        name: "low"
        for name in TOOL_CATEGORIES
        if name not in _HIGH_TOOLS and name not in _MEDIUM_TOOLS
    },
}

#: 未登记工具的兜底风险（新版本插件新增工具时按 medium 谨慎对待）
DEFAULT_RISK = "medium"


def get_tool_risk(name: str) -> str:
    """查询工具风险等级（未登记兜底 medium）。"""
    return TOOL_RISKS.get(name, DEFAULT_RISK)


def get_tool_category(name: str) -> str:
    """查询工具分类（未登记归入"其他"）。"""
    return TOOL_CATEGORIES.get(name, "其他")


def summarize_tools(tools: list[dict]) -> dict[str, int]:
    """按分类统计工具数量（测试连接响应展示用）。"""
    summary: dict[str, int] = {}
    for tool in tools:
        category = get_tool_category(tool.get("name", ""))
        summary[category] = summary.get(category, 0) + 1
    return dict(sorted(summary.items(), key=lambda item: -item[1]))
