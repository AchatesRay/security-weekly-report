"""网络安全周报系统 — 数据处理管道

10 步流水线（括号内为该步骤的独立产物文件）：

  1.  fetcher             RSS/API 信源并发抓取                  -> data/raw_items.json
  2.  parser              解析为统一数据结构（时间归一到 UTC）    -> data/parsed_items.json
  3.  deduplicator        URL 规范化去重 + 标题相似度去重 + 过期过滤
                                                               -> data/deduped_items.json
  4.  keyword_filter stg1 标题+前200字快速评分，<30 分提前丢弃     -> data/parsed_items.json
  5.  fulltext_extractor  短摘要文章抓取原文（原地增强，并发+SSRF 防护）
  6.  keyword_filter stg2 完整评分 + 分类 + 内容类型             -> data/classified_items.json
  7.  llm_processor       TextRank 抽取式摘要                    -> data/enhanced_items.json
  8.  translator          非中文内容 → 中文（腾讯云 TMT）          -> data/translated_items.json
  9.  report_generator    Jinja2 渲染 HTML 周报（桌面版）         -> reports/Security_Reports.html
 10.  mobile_converter    桌面 → 移动版（剥离详情，按需加载）      -> reports/Security_Reports_mobile.html

评分阈值（config/scoring_keywords.json 的 thresholds）：≥80 收录，50-79 待复核，<50 丢弃；
阶段1 预筛 <30 提前丢弃。阶段1 与阶段2 使用同一套评分口径，分值可直接比较。

每次运行会先清空全部中间产物，且任一步失败即中止（不生成报告、保留上一版周报）。

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
