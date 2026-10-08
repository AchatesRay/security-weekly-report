#!/usr/bin/env python3
"""网络安全周报系统 — 统一入口"""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

# 从项目根目录的 .env 文件加载环境变量（显式指定路径，
# 避免从其他工作目录启动时读不到 .env）
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_DIR / ".env")
except ImportError:
    pass


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python app.py --run [--skip-fetch]   # 运行完整管道")
        print("  python app.py server [port]           # 启动管理后台")
        return

    cmd = sys.argv[1]

    if cmd == "--run":
        sys.path.insert(0, str(PROJECT_DIR))
        from pipeline.orchestrator import run_pipeline

        skip_fetch = "--skip-fetch" in sys.argv
        ok = run_pipeline(skip_fetch=skip_fetch)
        # 以退出码反映成败，管理后台可据此如实显示（此前失败也返回 0）
        sys.exit(0 if ok else 1)

    elif cmd == "server":
        server_script = PROJECT_DIR / "server" / "config_server.py"
        port = sys.argv[2] if len(sys.argv) > 2 else "8090"
        args = [sys.executable, str(server_script), "--project-dir", str(PROJECT_DIR)]
        if len(sys.argv) > 2:
            args.append(sys.argv[2])
        print(f"[SERVER] 启动配置服务器 (端口 {port})")
        subprocess.Popen(args)
        print(f"[SERVER] 服务器已启动，访问 http://localhost:{port}")

    else:
        print(f"未知命令: {cmd}")
        print("用法: python app.py --run [--skip-fetch]")
        print("      python app.py server [port]")


if __name__ == "__main__":
    main()
