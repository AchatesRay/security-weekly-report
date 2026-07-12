#!/usr/bin/env python3
"""网络安全周报系统 — 统一入口"""

import sys
import subprocess
from pathlib import Path

# 从 .env 文件加载环境变量（如果存在）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROJECT_DIR = Path(__file__).resolve().parent


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python app.py --run [--skip-fetch]   # 运行完整管道")
        print("  python app.py server [port]           # 启动管理后台")
        return

    cmd = sys.argv[1]

    if cmd == "--run":
        sys.path.insert(0, str(PROJECT_DIR))
        from pipeline.main import run_pipeline

        skip_fetch = "--skip-fetch" in sys.argv
        run_pipeline(skip_fetch=skip_fetch)

    elif cmd == "server":
        server_script = PROJECT_DIR / "server" / "config_server.py"
        args = [sys.executable, str(server_script), "--project-dir", str(PROJECT_DIR)]
        if len(sys.argv) > 2:
            args.append(sys.argv[2])
        print(f"[SERVER] 启动配置服务器 (端口 {sys.argv[2] if len(sys.argv) > 2 else '8090'})")
        subprocess.Popen(args)
        print(f"[SERVER] 服务器已启动，访问 http://localhost:{sys.argv[2] if len(sys.argv) > 2 else '8090'}")

    else:
        print(f"未知命令: {cmd}")
        print("用法: python app.py --run [--skip-fetch]")
        print("      python app.py server [port]")


if __name__ == "__main__":
    main()
