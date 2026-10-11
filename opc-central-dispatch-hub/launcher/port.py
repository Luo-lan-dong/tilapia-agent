"""端口探测（ADR-021：8000 被占时向后自动探测，最多到 8010）。"""

import socket

from launcher.errors import PortExhaustedError

FIRST_PORT = 8000
LAST_PORT = 8010


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """检测端口是否可绑定。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(first: int = FIRST_PORT, last: int = LAST_PORT) -> int:
    """在 [first, last] 范围内返回第一个可绑定端口，无可用端口抛 PortExhaustedError。"""
    for port in range(first, last + 1):
        if is_port_free(port):
            return port
    raise PortExhaustedError(first, last)
