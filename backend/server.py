"""一条命令启动服务：python -m backend.server

默认监听 http://127.0.0.1:8000，同时托管 API 与 web/dist 静态页面。
"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="FuncGurad P5 图形界面服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    print(f"FuncGurad P5 服务启动: {url}")
    print("按 Ctrl+C 停止。")
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        "backend.api.app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
