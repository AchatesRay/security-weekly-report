"""网络安全周报系统 — 数据处理管道

9 步流水线:
  1.  fetcher              RSS/API 信源并发抓取
  2.  parser               解析为统一数据结构
  3.  deduplicator         URL 精确去重 + 标题模糊去重
  4.  scorer (stg1)       标题+前200字快速评分，<30 分提前丢弃
  5.  fulltext_extractor   短摘要文章全文抓取
  6.  scorer (stg2)        完整评分+分类+内容类型+地域推断（≥80收录，50-79待复核）
  7.  llm_processor        AI 摘要（抽取式 / LLM API）
  8.  translator           英文摘要→中文翻译（腾讯云 TMT）
  9.  report_generator     Jinja2 HTML 报告生成

主要入口: app.py --run
Web 管理:  app.py server [port]
"""

from .fetcher import fetch_all
from .parser import parse_all
from .keyword_filter import run_stage1, run_stage2, init_default_keywords
from .fulltext_extractor import run as extract_fulltext
from .deduplicator import run as deduplicate
from .translator import run as translate
from .llm_processor import run as enhance
from .report_generator import generate_report
from .main import run_pipeline

__all__ = [
    "fetch_all",
    "parse_all",
    "init_default_keywords", "run_stage1", "run_stage2",
    "extract_fulltext",
    "deduplicate",
    "translate",
    "enhance",
    "generate_report",
    "run_pipeline",
]
