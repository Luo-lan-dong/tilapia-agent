"""steer 端点连通性验证（ADR-045）。

无活跃流应返回 409（统一格式 code=100xxx）；非法参数返回 422。
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8000/api/v1/tasks/build/chat/steer"


def post(payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    status, body = post({"session_id": "00000000-0000-0000-0000-000000000000", "content": "test"})
    print(f"[无活跃流] HTTP {status} -> {body}")
    assert status == 409, f"期望 409，实际 {status}"
    print("OK: steer 端点 409 语义验证通过")
