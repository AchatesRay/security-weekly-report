"""网络安全周报系统 — 数据处理管道

10 步流水线:
  1.  fetcher              RSS/API 信源并发抓取
  2.  parser               解析为统一数据结构
  3.  deduplicator         URL 精确去重 + 标题模糊去重
  4.  scorer (stg1)       标题+前200字快速评分，<30 分提前丢弃
  5.  fulltext_extractor   短摘要文章全文抓取
  6.  scorer (stg2)        完整评分+分类+内容类型+地域推断（≥80收录，50-79待复核）
  7.  llm_processor        AI 摘要（抽取式 / LLM API）
  8.  translator           英文摘要→中文翻译（腾讯云 TMT）
  9.  report_generator     Jinja2 HTML 报告生成
  10. mobile_converter     桌面→移动端转换（剥离详情 + 注入 CSS/JS）

主要入口: app.py --run
Web 管理:  app.py server [port]
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


SECRETS_PATH = Path("config/secrets.json")


def load_secrets() -> dict:
    """从 config/secrets.json 加载 API 密钥，文件不存在则返回空字典"""
    if SECRETS_PATH.exists():
        try:
            with open(SECRETS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def atomic_write(path: str | Path, data: Any, **json_kwargs):
    """原子写入 JSON 文件：先写临时文件，再 rename 覆盖目标路径。

    管道各阶段共享 parsed_items.json 作为中间数据，普通写入若在
    写入中途崩溃会导致 JSON 截断/损坏。本函数确保写入要么完全
    成功，要么完全不改变目标文件。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, **json_kwargs)
        os.replace(tmp, path)
    except Exception:
        # 清理临时文件
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


from .fetcher import fetch_all
from .parser import parse_all
from .keyword_filter import run_stage1, run_stage2, init_default_keywords
from .fulltext_extractor import run as extract_fulltext
from .deduplicator import run as deduplicate
from .translator import run as translate
from .llm_processor import run as enhance
from .report_generator import generate_report
from .mobile_converter import run as convert_mobile
from .main import run_pipeline

__all__ = [
    "atomic_write",
    "fetch_all",
    "parse_all",
    "init_default_keywords", "run_stage1", "run_stage2",
    "extract_fulltext",
    "deduplicate",
    "translate",
    "enhance",
    "generate_report",
    "convert_mobile",
    "run_pipeline",
]
