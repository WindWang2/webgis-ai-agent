"""V3 Ecosystem Demo Pack 健康检查（worker 内同步执行，宿主带超时预算）。"""


def report() -> dict:
    return {"status": "healthy", "messages": ["synthetic providers ready"]}
