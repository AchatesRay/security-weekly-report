# SecurityInfo — 网络安全态势周报系统

从 80+ 信源（安全媒体、厂商、CERT、政府机构、AI 厂商等）自动抓取网络安全资讯，经 10 步流水线处理评分后，生成 HTML 周报（桌面端 + 移动端自适应）。

## 功能特性

- **80+ 信源** — RSS、API、HTTP 爬虫三种采集方式，覆盖国内外主流安全情报源
- **两阶段评分过滤** — 先快速筛掉无关内容，再完整评分分类，确保报告质量
- **六维分类体系** — 威胁情报、AI 安全、漏洞态势、政策法规、产业动态、数据隐私
- **AI 摘要生成** — 内置 TextRank 抽取式摘要，无需外部 API 即可生成中文摘要
- **自动翻译** — 英文内容自动翻译为中文（腾讯云 TMT API）
- **双端自适应** — 桌面端完整版 + 移动端轻量版，服务端根据 UA 自动切换
- **管理后台** — Web 界面管理信源、评分关键词、分类排序、管道启停
- **预压缩** — HTML 和数据文件自动 gzip 预压缩，减少传输体积

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（参考 .env.example）
cp .env.example .env
# 编辑 .env，填入 TMT_SECRET_ID / TMT_SECRET_KEY

# 3. 完整运行（抓取 + 处理 + 生成报告）
python app.py --run

# 4. 启动管理后台（默认 8090 端口）
python app.py server 8090
```

### 其他命令

```bash
# 跳过抓取，用已有数据重新生成（调试时常用）
python app.py --run --skip-fetch

# 管理脚本
./scripts/server.sh start   # 后台启动
./scripts/server.sh stop    # 停止
./scripts/server.sh status  # 查看状态
```

---

## 目录结构

```
SecurityInfo/
├── app.py                        # 统一 CLI 入口
├── requirements.txt              # Python 依赖
├── .env                          # 环境变量（gitignored）
├── .env.example                  # 环境变量模板
├── .gitignore
│
├── pipeline/                     # 核心数据管道
│   ├── __init__.py               # 模块导出，暴露所有步骤的入口函数
│   ├── orchestrator.py           # 管道编排器：串联 10 步，错误收集，评分周对比
│   ├── steps/                    # 10 个步骤模块
│   │   ├── fetcher.py            # [步骤1] 并发 RSS/API/Scraper 信源抓取
│   │   ├── parser.py             # [步骤2] XML/HTML 解析为统一数据结构
│   │   ├── deduplicator.py       # [步骤3] URL 精确去重 + 标题模糊去重
│   │   ├── keyword_filter.py     # [步骤4+6] 两阶段评分过滤（调用 scorer）
│   │   ├── scorer.py             # 评分引擎：词级加权 + 位置加成 + 组合校验
│   │   ├── fulltext_extractor.py # [步骤5] 短摘要文章原文抓取（BS4 解析）
│   │   ├── llm_processor.py      # [步骤7] AI 摘要（TextRank / LLM API）
│   │   ├── translator.py         # [步骤8] 英文→中文翻译（腾讯云 TMT）
│   │   ├── report_generator.py   # [步骤9] Jinja2 HTML 报告生成
│   │   └── mobile_converter.py   # [步骤10] 桌面→移动端转换
│   ├── utils/                    # 工具函数
│   │   ├── __init__.py           # atomic_write, precompress, load_secrets
│   │   └── scraper.py            # HTTP 静态页面爬虫（被 fetcher/parser 调用）
│   └── assets/                   # 移动端静态资产
│       ├── mobile.css            # 移动端样式
│       └── mobile.js             # 移动端交互（按需加载、分类导航、详情面板）
│
├── server/                       # 管理后台
│   ├── config_server.py          # REST API + 静态文件服务（SimpleHTTPRequestHandler）
│   └── config.html               # 管理后台 Web 界面
│
├── config/                       # 配置文件（全部位于此目录，平铺管理）
│   ├── source_config.yaml        # 信源配置（~80 个信源，含 RSS/API/Scraper 三种类型）
│   ├── scoring_keywords.json     # 评分关键词（强/中/弱三级 + 分类 + 内容类型）
│   ├── keywords.json             # 关键词别名映射（兼容旧版 API）
│   ├── llm_config.yaml           # LLM 摘要配置（抽取式/API 模式切换）
│   ├── settings.json             # 管理后台全局设置
│   └── secrets.json              # API 密钥（gitignored，建议改用 .env）
│
├── templates/                    # Jinja2 模板
│   └── weekly_report.html        # 周报 HTML 模板
│
├── scripts/                      # 运维脚本
│   └── server.sh                 # 管理后台启停脚本（start/stop/restart/status）
│
├── reports/                      # 生成的周报（gitignored）
│   ├── Security_Reports.html     # 桌面端完整周报
│   ├── Security_Reports_mobile.html  # 移动端轻量版
│   ├── Security_Reports.html.gz      # 预压缩版本
│   ├── Security_Reports_mobile.html.gz
│   └── data_2026W31*.json        # 按分类拆分的详情数据（移动端按需加载）
│
├── data/                         # 管道中间数据（gitignored）
│   ├── raw_items.json            # 原始抓取结果
│   ├── parsed_items.json         # 解析后统一结构
│   ├── deduped_items.json        # 去重后
│   ├── classified_items.json     # 评分分类后
│   ├── enhanced_items.json       # AI 摘要后
│   ├── translated_items.json     # 翻译完成后
│   ├── fetch_status.json         # 信源抓取状态
│   ├── source_health.json        # 信源健康记录
│   ├── scoring_stats.json        # 评分统计
│   └── scoring_history/          # 评分统计归档（用于周对比）
│
└── docs/                         # 文档
    └── superpowers/specs/
        ├── 2026-06-22-security-weekly-report-design.md  # 设计文档
        └── 2026-06-30-api-collector-design.md           # API 采集设计
```

---

## 10 步流水线详解

管道由 `pipeline/orchestrator.py` 统一编排，任何步骤失败不影响后续流程（`skip_ok=True`）。

| # | 模块 | 输入 | 输出 | 职责 | 容错 |
|---|------|------|------|------|------|
| 1 | fetcher | 80+ 信源配置 | `raw_items.json` | 并发抓取 RSS/API/Scraper 信源，自动轮换 UA，信源健康监测 | 单信源失败不阻断 |
| 2 | parser | `raw_items.json` | `parsed_items.json` | XML→统一 dict，HTML 去标签，Scraper 类型特殊解析 | ✅ |
| 3 | deduplicator | `parsed_items.json` | `deduped_items.json` | URL 精确去重 + rapidfuzz 标题模糊去重（阈值 75%），过期过滤（>7天） | ✅ |
| 4 | keyword\_filter (stage1) | `deduped_items.json` | `parsed_items.json`(更新) | 标题+前200字快速评分，**<30 分提前丢弃**，减少后续处理量 | ✅ |
| 5 | fulltext\_extractor | `parsed_items.json` | `parsed_items.json`(更新) | 摘要 <300 字的文章用 httpx+BS4 抓取全文（上限 20000 字） | ✅ |
| 6 | keyword\_filter (stage2) | `parsed_items.json` | `classified_items.json` | 完整评分 + 领域分类 + 内容类型 + 地域推断。**≥80 收录，50-79 待复核，<50 丢弃** | ✅ |
| 7 | llm\_processor | `classified_items.json` | `enhanced_items.json` | TextRank 抽取式摘要（默认）或 LLM API 摘要。英文摘要自动翻译 | ✅ |
| 8 | translator | `enhanced_items.json` | `translated_items.json` | 调用腾讯云 TMT API，将英文摘要翻译为中文。单条超时 8s | ✅ |
| 9 | report\_generator | `translated_items.json` | `Security_Reports.html` + 分类 JSON | Jinja2 渲染 HTML，按分类拆分数据文件，gzip 预压缩 | ✅ |
| 10 | mobile\_converter | `Security_Reports.html` | `Security_Reports_mobile.html` | 剥离详情字段（按需加载），注入 mobile.css/mobile.js | ✅ |

### 评分机制

评分引擎 `scorer.py` 采用词级加权累加模型：

- **三级权重**：强(30分) / 中(15分) / 弱(5分)
- **位置加成**：标题命中 ×2，前200字 ×1.5，正文 ×1
- **组合校验**：命中 2+ 关键词且总分≥30 时额外 +20%
- **分类推断**：关键词携带分类元数据，评分同时完成分类
- **内容类型**：漏洞披露、研究报告、威胁情报、政策法规、技术文章、安全事件
- **地域推断**：基于关键词匹配判断是否涉及中国/美国/欧盟/全球

评分关键词配置位于 `config/scoring_keywords.json`。

---

## 移动端适配

三层交互架构，专为手机浏览优化：

```
文章列表 (首屏可见)
  └─ 点击文章 → 左侧分类导航抽屉（侧滑展开）
      └─ 切换分类 → 右侧详情面板（全屏覆盖层）
          └─ 包含：标题、摘要、评分匹配关键词、原文链接
```

核心技术细节：
- **服务端 UA 检测**：`config_server.py` 根据 `User-Agent` 自动返回对应版本
- **按需加载**：移动版 HTML 仅 ~70KB（仅列表数据），详情通过 `fetch('/reports/data_<week>_cat_<N>.json')` 动态加载
- **分类文件拆分**：`report_generator.py` 按分类拆分数据文件，前端只下载当前分类（避免一次下载全部数据）
- **预压缩**：所有 HTML 和数据文件自动生成 `.gz` 版本，服务端优先返回

---

## 配置参考

### 信源配置 (`config/source_config.yaml`)

支持三种信源类型：

```yaml
- source_name: "安全内参"           # 显示名称
  enabled: true                    # 是否启用
  type: rss                        # rss / api / scraper
  feed_url: "https://example.com/rss"
  category: "国内信源"              # 信源分组（用于健康告警）
  site_url: "https://example.com"
```

API 类型额外支持 `api_params`（请求参数、分页、JSON 路径提取）。
Scraper 类型额外支持 `scraper_config`（CSS 选择器、分页规则）。

### 评分关键词 (`config/scoring_keywords.json`)

```json
{
  "强": [{"word": "CVE-2024-", "score": 30, "category": "③ 漏洞态势...", "content_type": "漏洞披露"}],
  "中": [{"word": "ransomware", "score": 15, "category": "② 威胁情报..."}],
  "弱": [{"word": "security", "score": 5}]
}
```

每个关键词可选字段：`word`(匹配词), `score`(权重), `category`(分类), `content_type`(内容类型), `region`(地域)。

### 全局设置 (`config/settings.json`)

```json
{
  "dedup": {
    "similarity_threshold": 75,    // 标题模糊去重阈值（0-100）
    "max_days": 7                  // 文章过期天数
  },
  "translate": {
    "timeout": 8                   // 单条翻译超时（秒）
  },
  "category_order": [
    "① AI/LLM 安全",
    "② 威胁情报与攻防对抗",
    "③ 漏洞态势与供应链安全",
    "④ 政策法规与标准框架",
    "⑤ 产业动态与技术趋势",
    "⑥ 数据安全与隐私保护",
    "未分类"
  ]
}
```

### LLM 配置 (`config/llm_config.yaml`)

```yaml
mode: extractive          # extractive（TextRank 抽取式，默认）/ api（调用外部 LLM）
model: claude-sonnet-4-6  # LLM 模式下的模型名
api_key: ""               # API 密钥（建议用环境变量）
```

---

## 环境变量

支持从项目根目录 `.env` 文件加载，也支持直接设置系统环境变量。

| 变量 | 用途 | 必填 |
|------|------|------|
| `TMT_SECRET_ID` | 腾讯云翻译 API 密钥 ID（兼容旧名 `TENCENT_SECRET_ID`） | **是**（翻译步骤） |
| `TMT_SECRET_KEY` | 腾讯云翻译 API 密钥 Key（兼容旧名 `TENCENT_SECRET_KEY`） | **是**（翻译步骤） |
| `CONFIG_USERNAME` | 管理后台 HTTP Basic Auth 用户名 | 否（留空不启用认证） |
| `CONFIG_PASSWORD` | 管理后台 HTTP Basic Auth 密码 | 否（留空不启用认证） |
| `SCHOLAR_API_KEY` | Semantic Scholar API 密钥 | 否（无 key 有频率限制） |
| `GITHUB_TOKEN` | GitHub Personal Access Token | 否（无 token 有频率限制） |

---

## 架构决策

- **模块化管道**：10 个步骤通过 JSON 文件传递数据，任意步骤可独立重启。文件位于 `data/` 目录
- **原子写入**：`utils.atomic_write()` 先写临时文件再 rename，防止写入崩溃导致 JSON 截断
- **容错设计**：每个步骤失败只记错不阻断（`skip_ok=True`），最终在摘要中汇总错误
- **评分替代分类器**：`scorer.py` 的词级评分模型替代了旧版的规则分类器，评分同时完成分类
- **两阶段过滤**：stage1 用标题+前200字快速预筛（减少全文抓取量），stage2 完整评分
- **预压缩**：`utils.precompress()` 在生成报告时同时生成 `.gz` 版本，减小传输体积
- **移动端轻量化**：详情按分类拆分 JSON 按需加载，初始 HTML 仅 ~70KB

---

## 开发指南

### 添加新信源

编辑 `config/source_config.yaml`，添加一条信源记录。RSS 类型最简配置只需 `name`、`type: rss`、`feed_url`。

### 修改评分关键词

编辑 `config/scoring_keywords.json`，或通过管理后台 Web 界面操作。

### 调试管道

```bash
# 跳过耗时步骤，快速验证报告生成
python app.py --run --skip-fetch

# 查看中间数据
cat data/parsed_items.json | python3 -m json.tool | head -50
```

### 红线

- 不要直接运行 `pipeline/utils/scraper.py` 或 `pipeline/orchestrator.py` — 始终通过 `app.py` 入口
- 不要在信源配置中硬编码 API 密钥 — 使用 `.env` 文件或环境变量
- 评分阈值（stage1: 30, stage2: 80）改动需谨慎，影响报告条数质量
- `config/source_config.yaml` 中 `enabled: false` 的信源不要删除，留作记录

---

## 深入文档

- [设计文档](docs/superpowers/specs/2026-06-22-security-weekly-report-design.md) — 分类体系、标签维度、布局规范
- [API 采集设计](docs/superpowers/specs/2026-06-30-api-collector-design.md) — API 采集通道设计
