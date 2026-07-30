"""网络安全周报系统 — 数据处理管道

10 步流水线:
  1.  fetcher              RSS/API 信源并发抓取
  2.  parser               解析为统一数据结构
  3.  deduplicator         URL 精确去重 + 标题模糊去重
  4.  keyword_filter (stg1) 标题+前200字快速评分，<30 分提前丢弃
  5.  fulltext_extractor   短摘要文章全文抓取
  6.  keyword_filter (stg2) 完整评分+分类+内容类型+地域推断（≥80收录，50-79待复核）
  7.  llm_processor        AI 摘要（抽取式 / LLM API）
  8.  translator           英文摘要→中文翻译（腾讯云 TMT）
  9.  report_generator     Jinja2 HTML 报告生成
  10. mobile_converter     桌面→移动端转换（剥离详情 + 注入 CSS/JS）

主要入口: app.py --run
Web 管理:  app.py server [port]
"""

from .utils import atomic_write, precompress, load_secrets
from .orchestrator import run_pipeline

from .steps.fetcher import fetch_all
from .steps.parser import parse_all
from .steps.keyword_filter import run_stage1, run_stage2, init_default_keywords
from .steps.fulltext_extractor import run as extract_fulltext
from .steps.deduplicator import run as deduplicate
from .steps.translator import run as translate
from .steps.llm_processor import run as enhance
from .steps.report_generator import generate_report
from .steps.mobile_converter import run as convert_mobile

__all__ = [
    "atomic_write", "precompress", "load_secrets",
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
