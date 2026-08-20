"""WebGIS AI Agent - 服务启动入口"""
import uvicorn

# #663-A：env 加载归启动器所有。app 代码不再有 import 期副作用（import
# app.main 不改写 os.environ），本地 .env 在这里加载后随进程传给 app。
from dotenv import load_dotenv

load_dotenv()

# `app` is loaded by the uvicorn target string "main:app" below.
from app.main import app  # noqa: F401, E402


def main():
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=18000,
        reload=True,
    )


if __name__ == "__main__":
    main()
