"""AgentConfig 单元测试（ADR-016）。

覆盖：
- ``_generate_name`` 走 os-release 路径
- ``_generate_name`` 回退 platform.platform
- ``_generate_name`` 全部失败回退 hostname
- ``_generate_client_id`` 仍按 {hostname}-8位hex 格式
- 默认 agent_type=code
"""

from unittest.mock import mock_open, patch

from app.config import AgentConfig, _generate_client_id, _generate_name, _read_os_release


def test_generate_name_uses_os_release_ubuntu() -> None:
    """os-release 解析为 Ubuntu 22.04。"""
    fake = 'NAME="Ubuntu"\nVERSION="22.04.3 LTS (Jammy Jellyfish)"\nVERSION_ID="22.04"\nID=ubuntu\n'
    with patch("builtins.open", mock_open(read_data=fake)):
        assert _generate_name() == "Ubuntu 22.04"


def test_generate_name_uses_os_release_centos() -> None:
    """os-release 解析为 CentOS 7（无 VERSION_ID 时仅返回 NAME）。"""
    fake = 'NAME="CentOS Linux"\nVERSION_ID="7"\n'
    with patch("builtins.open", mock_open(read_data=fake)):
        assert _generate_name() == "CentOS Linux 7"


def test_generate_name_falls_back_to_platform_when_no_os_release() -> None:
    """os-release 不存在时回退 platform.platform()，且不含 -programming- 前缀。"""
    with (
        patch("builtins.open", side_effect=FileNotFoundError),
        patch("platform.platform", return_value="Linux 5.15.0-test"),
    ):
        name = _generate_name()
    assert name == "Linux 5.15.0-test"
    assert "-programming-" not in name


def test_generate_name_falls_back_to_hostname_when_all_fail() -> None:
    """os-release / platform 都失败时回退 hostname。"""
    with (
        patch("builtins.open", side_effect=OSError("denied")),
        patch("platform.platform", side_effect=RuntimeError("nope")),
        patch("socket.gethostname", return_value="my-box"),
    ):
        name = _generate_name()
    assert name == "my-box"
    assert "-programming-" not in name


def test_generate_name_name_only_when_no_version_id() -> None:
    """os-release 仅有 NAME 无 VERSION_ID 时返回 NAME。"""
    fake = 'NAME="Alpine"\n'
    with patch("builtins.open", mock_open(read_data=fake)):
        assert _generate_name() == "Alpine"


def test_generate_client_id_unchanged() -> None:
    """_generate_client_id 必须保持原样：{hostname}-8位hex。"""
    with patch("socket.gethostname", return_value="box-a"):
        cid = _generate_client_id()
    assert cid.startswith("box-a-")
    suffix = cid.split("-")[-1]
    assert len(suffix) == 8
    assert all(c in "0123456789abcdef" for c in suffix)


def test_read_os_release_returns_none_on_missing_file() -> None:
    """非 Linux 平台（macOS / Windows）无 os-release，返回 None。"""
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert _read_os_release() is None


def test_default_agent_type_is_code() -> None:
    """未设置 AGENT_TYPE 时默认为 'code'（ADR-016）。"""
    config = AgentConfig(agent_registration_token="dummy")
    assert config.agent_type == "code"


def test_resolved_name_prefers_explicit_agent_name() -> None:
    """AGENT_NAME 设置时优先于 os-release 兜底。"""
    config = AgentConfig(
        agent_registration_token="dummy",
        agent_name="my-laptop",
    )
    assert config.resolved_name == "my-laptop"
