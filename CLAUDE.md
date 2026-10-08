# SecurityInfo — 网络安全周报系统

从 141 个信源（当前启用 107 个）自动抓取网络安全资讯，经 10 步流水线处理后生成 HTML 周报（桌面端 + 移动端）。

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
  __init__.py             模块导出
  orchestrator.py         管道编排器（串联所有步骤）
  steps/                  各步骤模块
    fetcher.py            并发 RSS/API 信源抓取
    parser.py             解析为统一数据结构
    keyword_filter.py     两阶段评分过滤（替代旧 classifier.py）
    scorer.py             评分逻辑（被 keyword_filter 调用）
    fulltext_extractor.py 短摘要文章原文抓取（右栏正文）
    deduplicator.py       URL 规范化去重 + 标题相似去重 + 过期过滤
    translator.py         非中文→中文翻译（腾讯云 TMT API）
    llm_processor.py      TextRank 抽取式摘要（LLM API 预留）
    report_generator.py   Jinja2 HTML 报告生成
    mobile_converter.py   桌面→移动端转换（移动端按需加载详情）
  utils/                  工具函数
    __init__.py           项目绝对路径、原子写入、预压缩、密钥加载、时间归一、SSRF 防护
    scraper.py            静态页面抓取辅助工具
  assets/                 静态资产
    mobile.css            移动端样式
    mobile.js             移动端交互
config/                   配置文件
  source_config.yaml      信源配置（141 条，启用 107 条）
  scoring_keywords.json   评分关键词与阈值配置
  keywords.json           关键词别名映射
  llm_config.yaml         LLM 配置（预留）
  settings.json           管理后台配置（只存非敏感项，密钥不写入此文件）
server/                   管理后台
  config_server.py        配置服务器
  config.html             管理后台页面
scripts/                  运维脚本
  server.sh               Server 管理脚本
templates/                模板
  weekly_report.html      周报模板
reports/                  生成的 HTML 周报
docs/                     文档
data/                     中间数据（gitignored）
```

## 10 步管道

**任一步骤失败都会中止整个运行，且不生成报告**（保留上一版周报），并以非零退出码结束。开跑前会清空全部中间产物，因此不会出现“某步失败后用上一轮旧数据重发周报”。运行状态记录在 `data/pipeline_run.json`。

| # | 模块 | 产物 | 职责 |
|---|------|------|------|
| 1 | fetcher | `raw_items.json` | 并发抓取所有启用的 RSS/API/Scraper 信源；连续失败进入指数退避重试 |
| 2 | parser | `parsed_items.json` | XML 解析为统一结构体；时间统一归一到 UTC 无时区 |
| 3 | deduplicator | `deduped_items.json` | URL 规范化去重 + 标题相似去重 + 过期过滤（>7 天） |
| 4 | keyword_filter (stage1) | `parsed_items.json` | 标题+前200字快速评分，<30 分提前丢弃 |
| 5 | fulltext_extractor | 原地增强 `parsed_items.json` | 短摘要文章抓取原文（并发 8，上限20000字，含 SSRF 防护） |
| 6 | keyword_filter (stage2) | `classified_items.json` | 完整评分+分类+内容类型（≥80收录，50-79待复核，<50丢弃） |
| 7 | llm_processor | `enhanced_items.json` | TextRank 抽取式摘要 → ai_summary（不做翻译） |
| 8 | translator | `translated_items.json` | 非中文的标题/摘要/AI摘要 → 中文（腾讯云 TMT），失败逐条标记 |
| 9 | report_generator | `Security_Reports.html` + 分类 JSON | Jinja2 → HTML 报告（渲染成功后才写数据文件） |
| 10 | mobile_converter | `Security_Reports_mobile.html` | 按模板标记剥离详情面板，注入 CSS/JS |

阶段1 与阶段2 使用同一套评分口径，两阶段分值可直接比较。

### 移动端适配

- 服务端根据 `User-Agent` 自动判断：移动端返回 `Security_Reports_mobile.html`，桌面端返回 `Security_Reports.html`
- 移动版 HTML 仅内联列表字段（~70KB），详情内容从 `reports/data_<week>.json` 按需 fetch
- 三层交互：文章列表 → 左侧分类导航 → 右侧详情面板（卡片式覆盖层）

## 红线

- 不要直接运行 `pipeline/utils/scraper.py` 或 `pipeline/orchestrator.py` — 始终通过 `app.py` 入口
- **任何密钥都不得写入 `config/` 下的配置文件，也不得提交进版本库** — 腾讯云密钥只从 `.env` 或 `config/secrets.json` 读取（管理后台的翻译页已不再提供密钥输入框）
- 评分阈值（stage1: 30, stage2: 80）改动需谨慎，影响报告条数质量
- `config/source_config.yaml` 中 `enabled: false` 的信源不要删除，留作记录
- `templates/weekly_report.html` 里的 `<!--DETAIL_PANEL_START-->` / `<!--DETAIL_PANEL_END-->` 标记供移动版转换定位详情面板，不要删除
- `Output/`（本地交付物与备份目录）已加入 `.gitignore`，其中可能含修复前的仓库备份，**不要从版本库里移除这条忽略规则**

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

## 远端部署（Hermes 服务器）

- 部署信息（含 SSH 凭据）见 [docs/server_deployment.md](docs/server_deployment.md)（已 gitignore，不提交）
- 项目路径: `/home/ubuntu/.hermes/profiles/zhuanjia/workspace/hub/cybersec/`
- 入口: `venv/bin/python app.py --run`（Python 3.11 venv，已装全部依赖）
- 报告输出: `reports/Security_Reports.html`（桌面版）+ `Security_Reports_mobile.html`（移动版）
- 管理后台: `scripts/server.sh {start|stop|restart|status}`，默认端口 8090
- Hermes 定时任务: `网络安全周报自动更新`，每周一 08:00（cron: `0 8 * * 1`），AI Agent 模式，结果推送微信
- 部署方式: 本地打包 `tar`（排除 `.git/.claude/data/reports/.env`）→ scp → 解压到 `cybersec/`；`data/`、`reports/` 由远端运行生成，`.env`、`venv` 保留远端版本

### 部署注意事项

- 远端 `scripts/server.sh` 使用系统 `python3`（3.12），若未装依赖需改为 `venv/bin/python3`
- `hub/` 下遗留孤儿文件 `server_with_ua.py`（旧版，不再使用）
- 远端 `.env` 中管理后台密码仍为默认 `you_should_change_this`，建议修改
- Hermes Agent 运行后会在项目根生成 `cybersec_weekly_<日期>.html` 冗余副本
- 不要修改 Hermes 服务器自身配置（`~/.hermes/` 下的配置由 Hermes 管理）

## 深入文档

- [设计文档](docs/superpowers/specs/2026-06-22-security-weekly-report-design.md) — 分类体系、标签维度、布局规范
- [API 采集设计](docs/superpowers/specs/2026-06-30-api-collector-design.md) — API 采集通道设计
