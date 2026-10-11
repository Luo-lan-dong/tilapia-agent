"""安全相关工具函数。"""

import secrets


def generate_registration_token() -> str:
    """生成 32 字节（64 字符）hex 注册令牌。"""
    return secrets.token_hex(32)
