"""端口探测单元测试（Harness UT-1 / UT-2）。"""

import socket

import pytest

from launcher.errors import PortExhaustedError
from launcher.port import find_free_port


def test_first_free_port_returned() -> None:
    """范围内首个空闲端口直接返回。"""
    assert find_free_port(first=39999, last=40001) == 39999


def test_occupied_port_skipped() -> None:
    """首个端口被占用时返回下一个可用端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 39999))
        sock.listen(1)
        assert find_free_port(first=39999, last=40010) == 40000


def test_all_ports_occupied_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测范围内全部占用时抛 PortExhaustedError，并携带范围信息。"""
    monkeypatch.setattr("launcher.port.is_port_free", lambda *_a, **_kw: False)
    with pytest.raises(PortExhaustedError) as exc_info:
        find_free_port(first=8000, last=8002)
    assert exc_info.value.first == 8000
    assert exc_info.value.last == 8002
