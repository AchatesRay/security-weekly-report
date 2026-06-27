"""
网络安全周报系统 — 主入口

用法:
    python main.py --run          # 执行完整管道: 抓取->解析->全文提取->去重->分类->翻译->摘要->生成周报
    python main.py --run --skip-fetch  # 跳过抓取，使用已有数据重新生成

注意：本文件为向后兼容的代理入口，实际逻辑在 pipeline/main.py 中
"""

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


def main():
    sys.path.insert(0, str(PROJECT_DIR))
    from pipeline.main import main as pipeline_main
    pipeline_main()


if __name__ == "__main__":
    main()
