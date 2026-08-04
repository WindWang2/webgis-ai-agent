"""WebGIS AI Agent - 服务启动入口"""
import uvicorn
# noqa: F401 — `app` is loaded by the uvicorn target string "main:app" below.
from app.main import app  # noqa: F401


def main():
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )


if __name__ == "__main__":
    main()
