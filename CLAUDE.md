# SecurityInfo — 网络安全周报系统

从 80+ 信源自动抓取网络安全资讯，经 10 步流水线处理后生成 HTML 周报（桌面端 + 移动端）。

## 入口

```bash
python app.py --run              # 完整运行
python app.py --run --skip-fetch # 跳过抓取，重新生成
python app.py server [port]      # 启动管理后台 (默认 8090)
```

## 目录结构

```
app.py                    统一入口
CLAUDE.md                 本文件
pipeline/                 10 步数据处理管道
  main.py                 主入口（串联所有步骤）
  __init__.py             模块导出
  fetcher.py              并发 RSS/API 信源抓取
  parser.py               解析为统一数据结构
  keyword_filter.py       两阶段评分过滤（替代旧 classifier.py）
  scorer.py               评分逻辑（被 keyword_filter 调用）
  fulltext_extractor.py   短摘要文章原文抓取（右栏正文）
  deduplicator.py         URL 去重 + 标题模糊去重
  translator.py           英文→中文翻译（腾讯云 TMT API）
  llm_processor.py        摘要翻译与 LLM 增强（预留）
  report_generator.py     Jinja2 HTML 报告生成
  scraper.py              静态页面抓取辅助工具
  mobile_converter.py     桌面→移动端转换（移动端按需加载详情）
  mobile.css               移动端样式
  mobile.js                移动端交互
config/                   配置文件
  source_config.yaml      信源配置（~80 个信源）
  scoring_keywords.json   评分关键词配置
  keywords.json           关键词别名映射
  llm_config.yaml         LLM 配置（预留）
  settings.json           管理后台配置
templates/                模板
  weekly_report.html      周报模板
  config.html             管理后台模板
reports/                  生成的 HTML 周报
server/                   管理后台
docs/                     文档
data/                     中间数据（gitignored）
```

## 10 步管道

| # | 模块 | 职责 |
|---|------|------|
| 1 | fetcher | 并发抓取所有启用的 RSS/API/Scraper 信源 |
| 2 | parser | XML 解析为统一结构体 |
| 3 | deduplicator | URL 精确去重 + 标题模糊去重 |
| 4 | keyword_filter (stage1) | 标题+前200字快速评分，<30 分提前丢弃 |
| 5 | fulltext_extractor | 短摘要文章抓取原文（供右栏正文显示，上限20000字） |
| 6 | keyword_filter (stage2) | 完整评分+分类+内容类型+地域推断（≥80收录，50-79待复核） |
| 7 | llm_processor | TextRank 抽取式摘要 → ai_summary，英文摘要→中文翻译 |
| 8 | translator | 剩余英文摘要→中文翻译（腾讯云 TMT） |
| 9 | report_generator | Jinja2 → HTML 报告（桌面版） |
| 10 | mobile_converter | 桌面→移动端转换（剥离详情数据，注入 CSS/JS） |

### 移动端适配

- 服务端根据 `User-Agent` 自动判断：移动端返回 `Security_Reports_mobile.html`，桌面端返回 `Security_Reports.html`
- 移动版 HTML 仅内联列表字段（~70KB），详情内容从 `reports/data_<week>.json` 按需 fetch
- 三层交互：文章列表 → 左侧分类导航 → 右侧详情面板（卡片式覆盖层）

## 红线

- 不要直接运行 `pipeline/scraper.py` 或 `pipeline/main.py` — 始终通过 `app.py` 入口
- 不要在信源配置中硬编码 API 密钥 — 使用 `.env` 文件或环境变量
- 评分阈值（stage1: 30, stage2: 80）改动需谨慎，影响报告条数质量
- `config/source_config.yaml` 中 `enabled: false` 的信源不要删除，留作记录

## 环境变量

支持从项目根目录的 `.env` 文件加载，也支持直接设置环境变量。
参考 `.env.example` 创建你自己的 `.env` 文件（已 gitignored）。

| 变量 | 用途 |
|------|------|
| `TMT_SECRET_ID` | 腾讯云翻译 API 密钥 ID（旧名 `TENCENT_SECRET_ID` 兼容） |
| `TMT_SECRET_KEY` | 腾讯云翻译 API 密钥 Key（旧名 `TENCENT_SECRET_KEY` 兼容） |
| `CONFIG_USERNAME` | 管理后台 HTTP Basic Auth 用户名（留空则不启用认证） |
| `CONFIG_PASSWORD` | 管理后台 HTTP Basic Auth 密码（留空则不启用认证） |
| `SCHOLAR_API_KEY` | Semantic Scholar API 密钥（可选，无 key 有频率限制） |
| `GITHUB_TOKEN` | GitHub Personal Access Token（可选，无 token 有频率限制） |

## 深入文档

- [设计文档](docs/superpowers/specs/2026-06-22-security-weekly-report-design.md) — 分类体系、标签维度、布局规范
- [信源清单](docs/source_inventory.txt) — 完整信源全景
