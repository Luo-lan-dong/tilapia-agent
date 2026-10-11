"""节点环境签名测试（ADR-061 M6）：确定性、可覆盖、失败容错。"""

from app import env_signature


def test_signature_is_stable_and_short() -> None:
    """UT-M6-1：同一进程内签名稳定（缓存）且为 16 位十六进制。"""
    first = env_signature.compute_environment_signature()
    second = env_signature.compute_environment_signature()

    assert first is not None
    assert first == second
    assert len(first) == env_signature.SIGNATURE_LENGTH
    assert all(ch in "0123456789abcdef" for ch in first)


def test_override_env_wins(monkeypatch) -> None:
    """UT-M6-2：同构机器群可用环境变量强制统一签名（运维兜底）。"""
    env_signature.compute_environment_signature.cache_clear()
    monkeypatch.setenv(env_signature.OVERRIDE_ENV, "fleet-a")
    try:
        assert env_signature.compute_environment_signature() == "fleet-a"
    finally:
        env_signature.compute_environment_signature.cache_clear()


def test_probe_failure_returns_none(monkeypatch) -> None:
    """UT-M6-3：探测异常**返回 None**（心跳照发，不阻断节点）。"""
    env_signature.compute_environment_signature.cache_clear()
    monkeypatch.delenv(env_signature.OVERRIDE_ENV, raising=False)

    def _boom() -> str:
        raise RuntimeError("platform unavailable")

    monkeypatch.setattr(env_signature.platform, "system", _boom)
    try:
        assert env_signature.compute_environment_signature() is None
    finally:
        env_signature.compute_environment_signature.cache_clear()
