"""WebGIS AI Agent - 服务启动入口"""
import uvicorn
from app.main import app


def main():
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
