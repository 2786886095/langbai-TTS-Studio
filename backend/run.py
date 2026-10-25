import os
import argparse
import socket

import uvicorn

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="langbai 本地共享配音任务服务")
    parser.add_argument("--headless", action="store_true", help="不打开桌面窗口，仅运行本机网页任务服务")
    parser.add_argument("--data-dir", help="与桌面端共用的数据目录")
    parser.add_argument("--port", type=int, default=int(os.getenv("LANGBAI_TTS_PORT", "18765")))
    args = parser.parse_args()
    os.environ["LANGBAI_TTS_PORT"] = str(args.port)
    if args.data_dir:
        os.environ["LANGBAI_TTS_DATA"] = args.data_dir
    if args.headless:
        os.environ["LANGBAI_STARTED_BY"] = "headless"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise SystemExit(f"端口 {args.port} 已有服务运行；请连接已有服务或先停止另一个版本")
    from app.main import create_app
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                          timeout_graceful_shutdown=5))
    def request_shutdown():
        server.should_exit = True
    app.state.request_shutdown = request_shutdown
    server.run()
