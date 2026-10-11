"""宝塔 MCP 建站 E2E 冒烟（2026-09-26，ADR-049 验收 E2E-1/E2E-3 的实测部分）。

流程：从开发库读取目标服务器配置 → 连接测试（initialize + tools/list）→
HtmlProjectCreate 创建测试静态站（测试域名，避开 IP 80 端口冲突）→
Write 写入 index.html → HtmlProjectInfo 核验 → HTTP（Host 头）访问验证。
全程脱敏输出（token/证书不打印）。

用法：python scripts/e2e_bt_site_20260926.py [--cleanup]
    --cleanup：测试结束后删除测试站点（默认保留供面板查看）
"""

import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.models.bt_mcp_server import BtMcpServer  # noqa: E402
from app.services.bt_mcp_client import BtMcpClient, BtMcpError, timed_call  # noqa: E402

TEST_DOMAIN = "e2e-check.local"
TEST_ROOT = "/www/wwwroot/opc-e2e-check"
INDEX_HTML = (
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<title>OPC E2E Check</title></head>"
    "<body><h1>OPC 宝塔 MCP 建站 E2E 测试成功</h1>"
    f"<p>site: {TEST_DOMAIN} / root: {TEST_ROOT}</p></body></html>"
)


def make_session_factory():
    """开发库会话工厂（独立 engine，脚本内自管理生命周期）。"""
    engine = create_async_engine(get_settings().database_url, echo=False)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def load_server(factory) -> BtMcpServer:
    """读取目标服务器（取第一台启用的宝塔 MCP 服务器）。"""
    async with factory() as session:
        result = await session.execute(select(BtMcpServer).where(BtMcpServer.enabled.is_(True)))
        server = result.scalars().first()
        if server is None:
            raise RuntimeError("没有已启用的宝塔 MCP 服务器，请先在设置页添加")
        # 脱离会话后仍需访问字段：显式提取
        session.expunge(server)
        return server


async def call_tool(client: BtMcpClient, tool: str, arguments: dict) -> dict:
    """调用工具并统一输出（宝塔 {status, msg, data} 结构；非结构化返回包装）。"""
    result, duration_ms = await timed_call(client, tool, arguments)
    if not isinstance(result, dict):
        print(f"  [RAW] {tool} ({duration_ms}ms) 返回非结构化响应：{str(result)[:160]}")
        return {"status": False, "msg": str(result)[:200], "raw": True}
    status = bool(result.get("status"))
    print(
        f"  [{'OK' if status else 'FAIL'}] {tool} ({duration_ms}ms) "
        f"msg={str(result.get('msg'))[:120]}"
    )
    return result


async def main() -> int:
    cleanup = "--cleanup" in sys.argv
    engine, factory = make_session_factory()
    try:
        server = await load_server(factory)
        print(f"目标服务器：{server.name}（{server.address_mode}）")
        client = BtMcpClient(
            url=server.local_url
            if server.address_mode == "local"
            else (server.public_url or server.local_url),
            api_token=server.api_token,
            ca_cert=server.ca_cert,
        )
        try:
            # 1. 连接测试
            info = await client.initialize()
            server_info = info.get("serverInfo", {})
            tools = await client.list_tools()
            print(
                f"1. 连接测试：{server_info.get('name')} v{server_info.get('version')}，"
                f"共 {len(tools)} 个工具"
            )
            tool_names = {t.get("name") for t in tools}

            # 2. 建站前置：确认 HTML 族工具存在
            required = {"HtmlProjectCreate", "HtmlProjectInfo", "Write"}
            missing = required - tool_names
            if missing:
                print(f"2. 缺少必要工具：{missing}，中止")
                return 1
            print("2. 工具族检查：HtmlProjectCreate/HtmlProjectInfo/Write 齐备")

            # 3. 防线联动：先用错误参数名验证硬校验会被服务端拒绝（预期失败）
            bad = await call_tool(client, "HtmlProjectInfo", {"project_name": TEST_DOMAIN})

            # 4. 创建测试站点（测试域名，避开 IP 80 端口冲突）
            if cleanup:
                print("3. cleanup 模式：先删除遗留测试站点")
                await call_tool(
                    client, "HtmlProjectDelete", {"site_name": TEST_DOMAIN, "confirm": True}
                )
            created = await call_tool(
                client,
                "HtmlProjectCreate",
                {"domains": [TEST_DOMAIN], "path": TEST_ROOT},
            )
            if not created.get("status") and "已存在" not in str(created.get("msg", "")):
                print("   建站失败，中止")
                return 1

            # 5. 写入 index.html（内容验证；Write 工具入参为 file_path）
            written = await call_tool(
                client,
                "Write",
                {"file_path": f"{TEST_ROOT}/index.html", "content": INDEX_HTML},
            )

            # 6. 核验（正确参数名 site_name）
            site_info = await call_tool(client, "HtmlProjectInfo", {"site_name": TEST_DOMAIN})
            info_data = site_info.get("data") or {}
            print(f"   站点信息：{str(info_data)[:300]}")

            # 7. HTTP 访问验证（Host 头直连 80 端口）
            import httpx

            async with httpx.AsyncClient(timeout=15, verify=False) as http:
                resp = await http.get(
                    f"http://{server.local_url.split('//')[1].split(':')[0]}/",
                    headers={"Host": TEST_DOMAIN},
                )
                body_ok = "OPC 宝塔 MCP 建站 E2E 测试成功" in resp.text
                print(f"7. HTTP 访问：status={resp.status_code}，内容匹配={body_ok}")

            print("\n===== E2E 汇总 =====")
            print(f"连接测试：通过（{len(tools)} 工具）")
            print(
                f"错误参数名拦截：{'服务端拒绝（防线外的预期失败）' if not bad.get('status') else '异常：竟然成功'}"
            )
            print(f"建站（{TEST_DOMAIN}）：{'成功' if created.get('status') else '已存在/失败'}")
            print(f"写入首页：{'成功' if written.get('status') else '失败'}")
            print(f"HTTP 访问验证：{'通过' if body_ok else '未通过'}")
            print(f"面板查看位置：「网站 → HTML项目」Tab，站点名 {TEST_DOMAIN}")
            return 0
        finally:
            await client.close()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except BtMcpError as exc:
        print(f"MCP 调用失败：{exc}")
        raise SystemExit(1) from exc
