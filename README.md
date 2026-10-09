# SecurityInfo — 网络安全态势周报系统

从 141 个信源（当前启用 107 个；安全媒体、厂商、CERT、政府机构、AI 厂商等）自动抓取网络安全资讯，经 10 步流水线处理评分后，生成 HTML 周报（桌面端 + 移动端自适应）。

## 功能特性

- **141 个信源（启用 107）** — RSS、API、HTTP 爬虫三种采集方式，覆盖国内外主流安全情报源
- **两阶段评分过滤** — 先快速筛掉无关内容，再完整评分分类，确保报告质量
- **六维分类体系** — 威胁情报、AI 安全、漏洞态势、政策法规、产业动态、数据隐私
- **AI 摘要生成** — 抽取式摘要（先洗掉网页噪声，再摘取最值得看的**一整段连续内容**，按句边界收尾），无需外部 API；长度可按分类分档，并标注「自动提炼 / 原文节选」
- **自动翻译** — 非中文的**标题与摘要**自动翻译为中文（腾讯云 TMT API；**正文保持原文**）
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
│   │   ├── fulltext_extractor.py # [步骤5] 短摘要文章原文抓取 + 正文清洗（BS4 解析）
│   │   ├── llm_processor.py      # [步骤7] 摘要（清洗噪声 + 最优连续窗口抽取；LLM 分支预留）
│   │   ├── translator.py         # [步骤8] 非中文标题与摘要→中文（腾讯云 TMT）
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
│   ├── keywords.json             # 历史遗留，当前代码未读取（保留备查）
│   ├── llm_config.yaml           # LLM 摘要配置（抽取式/API 模式切换）
│   ├── settings.json             # 管理后台全局设置
│   └── secrets.json              # API 密钥（gitignored，建议改用 .env）
│
├── templates/                    # Jinja2 模板
│   └── weekly_report.html        # 周报 HTML 模板
│
├── scripts/                      # 运维与检查脚本
│   ├── server.sh                 # 管理后台启停脚本（start/stop/restart/status）
│   └── summary_quality_report.py # 摘要质量体检 / 改动前后回归对比
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

管道由 `pipeline/orchestrator.py` 统一编排。**任一步骤失败都会中止整个运行，且不会生成报告**（保留上一版周报），并以非零退出码结束；开跑前会清空全部中间产物，因此不存在“用上一轮旧数据重发周报”的情况。每次运行的步骤状态记录在 `data/pipeline_run.json`。

| # | 模块 | 输入 | 输出 | 职责 | 失败处理 |
|---|------|------|------|------|------|
| 1 | fetcher | 141 个信源配置 | `raw_items.json` | 并发抓取 RSS/API/Scraper 信源，API 密钥按平台下发，信源健康监测 | 单信源失败不阻断；连续失败进入指数退避重试 |
| 2 | parser | `raw_items.json` | `parsed_items.json` | XML→统一 dict，HTML 去标签，时间统一归一到 UTC | 中止 |
| 3 | deduplicator | `parsed_items.json` | `deduped_items.json` | URL 规范化去重（剥追踪参数）+ rapidfuzz 标题相似去重（阈值 75%），过期过滤（>7 天） | 中止 |
| 4 | keyword\_filter (stage1) | `deduped_items.json` | `parsed_items.json` | 标题+前200字快速评分，**<30 分提前丢弃**，减少全文抓取量 | 中止 |
| 5 | fulltext\_extractor | `parsed_items.json` | `parsed_items.json`(原地增强) | 摘要 <300 字的文章抓取全文（并发 8，上限 20000 字），**含 SSRF 防护**；正文清洗为**先定位正文容器、再删模板**（删除时跳过正文容器及其祖先），只在块级标签边界换行以免把句子切碎 | 中止（单条失败仅记录状态） |
| 6 | keyword\_filter (stage2) | `parsed_items.json` | `classified_items.json` | 完整评分 + 领域分类 + 内容类型。**≥80 收录，50-79 待复核，<50 丢弃** | 中止 |
| 7 | llm\_processor | `classified_items.json` | `enhanced_items.json` | 抽取式摘要：先洗掉网页噪声（导航/署名/分享/日期/聚合壳），再摘取**最值得看的连续一段**，按句边界收尾；长度按分类分档（`summary` 段），结果附来源标记 `ai_summary_kind` | 中止 |
| 8 | translator | `enhanced_items.json` | `translated_items.json` | 腾讯云 TMT，把**非中文的标题与摘要**翻译为中文；**正文不翻译**（范围见 settings 的 `translate.fields`）；翻译失败逐条标记 | 中止（无密钥时跳过并记录状态） |
| 9 | report\_generator | `translated_items.json` | `Security_Reports.html` + 分类 JSON | Jinja2 渲染 HTML；**渲染成功后才写数据文件**，避免数据与页面不一致 | 中止（保留上一版报告） |
| 10 | mobile\_converter | `Security_Reports.html` | `Security_Reports_mobile.html` | 按模板标记剥离详情面板，注入 mobile.css/mobile.js | 中止（保留上一版报告） |

### 评分机制

评分引擎 `scorer.py` 采用「词级加权 + 位置加成 + 分类梯度」模型：

- **三级权重**：强(30分) / 中(15分) / 弱(5分)
- **位置加成**：标题 ×2，前200字 ×1.5，正文 ×1，尾部 ×0.5（同一关键词取最高加成，只计一次）
- **分类梯度（pairing_rules）**：某分类内命中强词则中/弱词按 100% 计入（tier_a）；
  仅有核心中词为 60%/30%（tier_b）；≥2 个普通中词为 50%/20%（tier_c）；
  否则中弱词不计分（tier_d）。**同一关键词被标注到多个分类时只计一次**，
  取其所属分类中最高的梯度系数
- **分类推断**：关键词携带分类元数据，评分同时完成分类（并列时按名称升序取确定值）
- **内容类型**：从命中关键词与全文模式推断，实际取值为
  `漏洞披露` / `攻击活动报告` / `工具发布` / `行业分析` / `法规/标准发布` /
  `研究报告/白皮书` / `综合`
- **负向过滤**：命中行业排除词（安全生产、食品安全等传统行业）且无强词时，总分封顶 29

#### 两个分数：门槛分与证据强度

- `confidence_score`：**截顶到 100**，用于与阈值比较（语义是"达到门槛"）
- `raw_score`：**不截顶**的原始证据强度，用于报告内排序

> 为什么需要两个：阈值 80 下大量条目截顶后同为 100 分（实测约占收录的 78%），
> 分数无法区分条目之间的强弱；报告因此按 `raw_score` 降序排列，并在详情栏
> 于超过满分时额外标注「证据强度」。

#### 阈值（`config/scoring_keywords.json` 的 `thresholds`）

| 键 | 当前值 | 含义 |
|---|---|---|
| `accept_threshold` | 80 | ≥ 此分收录 |
| `review_threshold` | 50 | ≥ 此分为待复核，低于则丢弃 |
| `stage1_drop_below` | 30 | 阶段1 预筛低于此分提前丢弃（不做全文抓取） |
| `min_strong_for_accept` | **0（关闭）** | >0 时要求至少命中 N 个强特征词才可收录 |

> `min_strong_for_accept` 默认关闭。2026-10-08 用真实数据实测取 2 时，会把
> 「Citrix 警告 NetScaler 漏洞正被在野利用」这类只命中 1 个强词的安全要闻一并
> 降级 —— 因为强词表以 AI 话题词为主，`vulnerability`/`exploit`/`ransomware`
> 等经典安全事件词大多在中词层。启用前请用真实数据回归确认不误伤。

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

每个信源真实使用的字段（RSS / API / Scraper 通用）：

```yaml
- name: "安全内参"                   # 显示名称（必填，唯一）
  group: "国内信源"                   # 信源分组，用于健康告警
  url: "https://example.com/rss"     # 抓取地址（必填，仅支持 http/https）
  type: rss                          # rss / api / scraper
  language: zh                       # en / zh / fr / hr ...（仅作标记，翻译按内容判断）
  enabled: true                      # 是否启用
  note: "备注"                        # 可选备注
  ssl_verify: false                  # 可选，关闭 TLS 证书校验（有中间人风险，慎用）
  api_platform: github_repo          # 仅 API 类型：github / github_repo / arxiv /
                                     #   semantic_scholar / ietf / mitre_attack / secrss
  scraper_config:                    # 仅 Scraper 类型：CSS 选择器
    article_selector: "article"
    title_selector: "h2 a"
    summary_selector: "p"
    date_selector: "time"
    link_selector: "a"
    link_base: "https://example.com"
```

> 未写 `api_platform` 的 API 信源会按信源名自动回补平台（见
> `pipeline/steps/fetcher.py` 的 `SOURCE_NAME_PLATFORMS`），以便正确带上
> `GITHUB_TOKEN` / `SCHOLAR_API_KEY`。

### 设置 (`config/settings.json`)

只保存非敏感项：去重阈值与天数、翻译超时与**翻译范围**、分类顺序。

> **密钥一律不写入此文件。** 腾讯云密钥只从环境变量
> `TMT_SECRET_ID` / `TMT_SECRET_KEY`（兼容旧名 `TENCENT_SECRET_ID` /
> `TENCENT_SECRET_KEY`）或 `config/secrets.json` 读取；管理后台的翻译页
> 也不再提供密钥输入框。后台保存设置时会按白名单裁剪字段，未声明的键
> （含历史上的 `tencent_secret_*`）会被直接丢弃。

### 翻译范围 (`config/settings.json` 的 `translate.fields`)

默认 `["title", "ai_summary"]` —— 只翻译**标题**与报告「摘要」栏显示的抽取式摘要，
**正文（`full_body` / 被全文替换后的 `summary`）保持原文**。可选值：

| 值 | 含义 |
|---|---|
| `title` | 条目标题 |
| `ai_summary` | 报告「摘要」栏显示的抽取式摘要 |
| `summary` | 短摘要（RSS description）；开启后会把这段也翻译 |

> 该项没有后台界面控件，直接在 `config/settings.json` 修改；后台保存设置时会
> 原样保留该字段（不会被抹掉）。翻译步骤启动时会打印实际生效的范围。

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
    "timeout": 8,                  // 单条翻译超时（秒）
    "fields": ["title", "ai_summary"]   // 翻译范围：标题 + 摘要（正文不翻译）
  },
  "summary": {
    "max_chars": 500,              // 摘要长度上限（字）
    "short_max_chars": 300,        // 下列分类单独用更短的档位
    "short_categories": ["④ 政策法规与标准框架", "⑤ 产业动态与技术趋势"],
    "min_chars": 80                // 低于此长度视为无效，退化为「原文节选」
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

### 摘要长度 (`config/settings.json` 的 `summary` 段)

报告「摘要」栏由 `pipeline/steps/llm_processor.py` 生成，长度与分档在这里调整：

| 键 | 含义 | 取值范围 |
|---|---|---|
| `max_chars` | 一般分类的摘要长度上限（字） | 100-2000 |
| `short_max_chars` | `short_categories` 里分类用的上限 | 100-2000 |
| `short_categories` | 使用短档位的分类名（需与 `category_order` 里的写法一致） | 字符串数组 |
| `min_chars` | 低于此长度的摘要视为无效，退化为「原文节选」 | 20-500 |

> 该项没有后台界面控件，直接改 `config/settings.json`；后台保存设置时会**原样保留**
> 这段配置（不会被抹掉）。生成摘要时会把实际生效的上限打印在运行日志里。
>
> 每条摘要还会写入来源标记 `ai_summary_kind`：`extractive`（自动提炼）、
> `fallback`（原文节选，例如原文只有寥寥几句）、`empty`（无可用文本）。
> 报告「摘要」栏右上角据此显示「自动提炼 / 原文节选」，读者不必猜这段是改写还是摘抄。

### 正文清洗（步骤 5，`pipeline/steps/fulltext_extractor.py`）

摘要不足 300 字的文章会去抓原始网页，从导航/广告/评论框里认出真正的正文。清洗结果
直接决定**报告右栏「正文」栏读者看到的内容**，同时也是自动摘要的输入。规则：

| 环节 | 做法 | 为什么 |
|---|---|---|
| 定位正文 | 语义标签（`article`/`main`/`itemprop`）与常见正文类名全部参评，取「文本量达最大值 90% 以上者里 **HTML 最紧凑**的那个」 | 原先取第一个 `<article>` 就返回，页面里有多个文章小卡片时会抓错；只按"文本密度"打分又会偏爱又小又密的卡片 |
| 删模板 | **先定位正文，再删**；删除任何元素前，先看它是不是正文容器或其祖先，是就跳过 | 这是"模板选择器误伤正文"的根治手段：实测 `[class*="sidebar"]` 会命中 WordPress 给整页加的 `no-sidebar`，把 47,518 字正文删成 363 字 |
| 换行 | 只在块级标签（段落/标题/列表/表格行…）边界换行，内联标签之间不插换行 | 原先按**每个文本节点**换行，加粗/链接/行内代码会把一句话切成十几段；报告正文按换行分段，读者看到的就是碎句 |
| 行级过滤 | 分两档：整行即模板的（版权/登录/订阅/来源/标签/裸域名/时间元信息…）任何长度都丢；含"搜索、评论、订阅"等词的**只在短行（≤60 字）**时丢 | 长句里出现这些词属正常内容。实测把阈值放宽到 80 字，会把正常内容句一起删掉 |
| 重复收敛 | 同页重复 ≥3 次的整行只留一条；**同一行内连续重复 ≥3 次的短语**也收敛成一条 | 部分论坛把登录墙提示放在十几个行内 span 里重复，块级换行不会拆开它们，按整行比对抓不到 |

> 已知取舍：少数站点会在正文前残留一两行分类面包屑（如「分类：智能体安全」），
> 个别站点还会残留站内标签词（如 `Agent` / `AI` / `安全`，实测 102 篇中 2 篇）。
> 这两类都是站方给的分类信息，**没有再追加规则去清**——继续加规则的边际收益在下降、
> 误删正文的风险在上升。注意面包屑删除后个别条目的**分类**可能变化（评分不受影响）：
> 实测 163 条中 1 条因此从「① AI/LLM 安全」落到「④ 政策法规与标准框架」。

**改清洗逻辑后怎么做回归**（工作脚本在 `Output/文章清洗优化/Temp/`，gitignored）：

```bash
python Output/文章清洗优化/Temp/cache_html.py          # 1. 把真实页面缓存为固定基准
python Output/文章清洗优化/Temp/compare.py --old <改动前的备份>   # 2. 噪音行/行长/耗时
python Output/文章清洗优化/Temp/diff2.py 0 1 2 ...     # 3. 逐行核对被删内容有没有误伤正文
python Output/文章清洗优化/Temp/scoring_regression.py  # 4. 同一份 HTML 下评分与收录判定 A/B
```

第 3 步必须**逐行看**"旧版有、新版没有"的内容，确认删掉的都是导航/相关文章/作者简介/
聚合器元信息；第 4 步必须确认**没有条目被移出「收录」**。

### 摘要质量回归（`scripts/summary_quality_report.py`）

改动摘要逻辑后，用最近一次真实运行的产物做前后对比（**不要只看总数，要逐条看**）：

```bash
python scripts/summary_quality_report.py                      # 体检当前产物
python scripts/summary_quality_report.py 改前.json 改后.json   # 对比（含逐条差异样例）
```

输出半句截断率、网页噪声率、英文句子粘连处数、摘要与正文栏重复率、长度分布与来源标记分布。

### LLM 配置 (`config/llm_config.yaml`)

```yaml
enabled: false            # 是否启用外部 LLM 摘要（当前主流程仍走抽取式）
provider: openai          # openai / anthropic / ollama
api_key: ""               # API 密钥（建议改用 .env，不要写进配置文件）
model: gpt-4o-mini
base_url: ""              # 兼容代理或本地部署
prompt_template: |        # 摘要提示词模板
  请为以下网络安全资讯生成中文摘要…
```

> **当前为预留位**：`enabled: true` 也只会在日志里提示“外部调用尚未实现”，
> 实际仍使用抽取式摘要（见 `llm_processor.process()`）。接入生成式摘要属于
> 另一档改造，需同时补齐密钥来源（`.env` / `config/secrets.json`）、失败回退与
> 调用缓存。

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
- **失败即中止**：任一步骤失败都会中止运行且**不生成报告**（保留上一版），退出码非零；开跑前清空全部中间产物，杜绝“新信源告警 + 旧正文”的跨轮混用
- **摘要只做"摘"不做"编"**：抽取式摘要永远取自原文原句（按句边界收尾、附来源标记）；生成式改写是另一档改造，需外部模型与预算
- **评分替代分类器**：`scorer.py` 的词级评分模型替代了旧版的规则分类器，评分同时完成分类
- **两阶段过滤**：stage1 用标题+前200字快速预筛（减少全文抓取量），stage2 完整评分
- **预压缩**：`utils.precompress()` 在生成报告时同时生成 `.gz` 版本，减小传输体积
- **移动端轻量化**：详情按分类拆分 JSON 按需加载，初始 HTML 仅 ~70KB

---

## 开发指南

### 添加新信源

编辑 `config/source_config.yaml`，添加一条信源记录。RSS 类型最简配置只需 `name`、`url`、`type: rss`、`language`、`enabled`（字段名是 `url`，不是 `feed_url`）。保存时会做结构校验，缺少 `name` / `url` 或 YAML 语法错误会被拒绝。

### 修改评分关键词

编辑 `config/scoring_keywords.json`，或通过管理后台 Web 界面操作。

### 调试管道

```bash
# 跳过耗时步骤，快速验证报告生成
python app.py --run --skip-fetch

# 查看中间数据
cat data/parsed_items.json | python3 -m json.tool | head -50

# 摘要质量体检 / 改动前后回归对比
python scripts/summary_quality_report.py
python scripts/summary_quality_report.py 改前.json 改后.json
```

### 红线

- 不要直接运行 `pipeline/utils/scraper.py` 或 `pipeline/orchestrator.py` — 始终通过 `app.py` 入口
- **任何密钥都不得写入 `config/` 下的配置文件或提交进版本库** — 腾讯云密钥用 `.env` 或 `config/secrets.json`，其余用环境变量
- 评分阈值（stage1: 30, stage2: 80）改动需谨慎，影响报告条数质量
- `config/source_config.yaml` 中 `enabled: false` 的信源不要删除，留作记录
- `templates/weekly_report.html` 中的 `<!--DETAIL_PANEL_START-->` / `<!--DETAIL_PANEL_END-->` 标记供移动版转换定位详情面板，不要删除

---

## 深入文档

- [设计文档](docs/superpowers/specs/2026-06-22-security-weekly-report-design.md) — 分类体系、标签维度、布局规范
- [API 采集设计](docs/superpowers/specs/2026-06-30-api-collector-design.md) — API 采集通道设计
- [AGENTS.md](AGENTS.md) — 项目记忆 / Agent 指令：架构、硬约束、**已定型的决策与原因**、自检清单（改动前建议先读）
