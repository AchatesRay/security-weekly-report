# SecurityInfo — 网络安全态势日报系统

从 141 个信源（当前启用 107 个；安全媒体、厂商、CERT、政府机构、AI 厂商等）自动抓取网络安全资讯，经 10 步流水线处理评分后，**每天早上 08:00** 生成 HTML 日报（桌面端 + 移动端自适应）。日报采用**近 14 天滚动窗口**：每天把新收录条目并入累加库，首页最多展示 120 条；超过 14 天的数据直接删除、不备份，收藏夹永久保留。

## 功能特性

- **141 个信源（启用 107）** — RSS、API、HTTP 爬虫三种采集方式，覆盖国内外主流安全情报源
- **两阶段评分过滤** — 先快速筛掉无关内容，再完整评分分类，确保报告质量
- **主题分类导航** — 侧栏按 7 个主题分类（①AI/LLM 安全、②威胁情报与攻防对抗、③漏洞态势与供应链安全、④政策法规与标准框架、⑤产业动态与技术趋势、⑥数据安全与隐私保护、⑦产业与商业动态）导航，报告按相关性从高到低排列
- **AI 摘要生成** — 抽取式摘要（先洗掉网页噪声，再摘取最值得看的**一整段连续内容**，按句边界收尾），无需外部 API；长度可按分类分档，并标注「自动提炼 / 原文节选」
- **自动翻译** — 非中文的**标题与摘要**自动翻译为中文（腾讯云 TMT API；**正文保持原文**）
- **双端自适应** — 桌面端完整版 + 移动端轻量版，服务端根据 UA 自动切换
- **管理后台** — Web 界面管理信源、评分关键词、分类排序、管道启停
- **文章收藏** — 详情页一键收藏，自动生成标签（分类 / CVE / 领域词 / 关键词），收藏内容以完整快照独立存放，可在「我的收藏」页搜索、筛选、改标签、写备注、删除与导出
- **预压缩** — HTML 和数据文件自动 gzip 预压缩，减少传输体积
- **日更 + 14 天滚动窗口** — 新条目按天累加，`first_seen` 落在窗口内的条目持续展示；`python app.py retention` 清理超期数据（不备份，`data/favorites/` 不受影响）

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（参考 .env.example）
cp .env.example .env
# 编辑 .env，填入翻译密钥：腾讯云 TMT_SECRET_ID / TMT_SECRET_KEY
# （改用百度翻译则填 BAIDU_APPID / BAIDU_KEY，并把 settings.json 的 translate.provider 设为 baidu）

# 3. 完整运行（抓取 + 处理 + 生成报告）
python app.py --run

# 4. 启动管理后台（默认 8090 端口）
python app.py server 8090
#    日报：http://localhost:8090/
#    我的收藏：http://localhost:8090/bookmarks

# 直接启动并仅监听回环地址（置于 Caddy/Nginx 反向代理后时使用）
python server/config_server.py --project-dir . --host 127.0.0.1 8090
```

### 其他命令

```bash
# 跳过抓取，用已有数据重新生成（调试时常用）
python app.py --run --skip-fetch

# 管理脚本
./scripts/server.sh start   # 后台启动
./scripts/server.sh stop    # 停止
./scripts/server.sh status  # 查看状态

# 清理超过保留期的运行产物（默认 14 天；--dry-run 只打印不删）
python app.py retention --dry-run
python app.py retention --days 14

# 日更发布（生产用：跑管道 → 发布稳定链接 → 归档 → 清理）
bash scripts/daily_publish.sh
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
│   │   ├── report_generator.py   # [步骤9] Jinja2 HTML 报告生成（窗口级排版）
│   │   ├── mobile_converter.py   # [步骤10] 桌面→移动端转换
│   │   ├── rolling_store.py      # 滚动条目库（跨天累加，data/state/rolling_items.json）
│   │   └── retention.py          # 保留期清理（默认 14 天，不备份）
│   ├── utils/                    # 工具函数
│   │   ├── __init__.py           # atomic_write, precompress, load_secrets
│   │   └── scraper.py            # HTTP 静态页面爬虫（被 fetcher/parser 调用）
│   └── assets/                   # 移动端静态资产
│       ├── mobile.css            # 移动端样式
│       └── mobile.js             # 移动端交互（按需加载、分类导航、详情面板）
│
├── server/                       # 管理后台
│   ├── config_server.py          # REST API + 静态文件服务（SimpleHTTPRequestHandler）
│   ├── config.html               # 管理后台 Web 界面
│   ├── favorites.py              # 收藏存储、自动标签、图片快照与导出（不依赖 HTTP）
│   └── bookmarks.html            # 「我的收藏」页面
│
├── config/                       # 配置文件（全部位于此目录，平铺管理）
│   ├── source_config.yaml        # 信源配置（141 个信源、启用 107 个；含 RSS/API/Scraper 三种类型）
│   ├── scoring_keywords.json     # 评分关键词（强/中/弱三级 + 分类 + 内容类型）
│   ├── keywords.json             # 历史遗留，当前代码未读取（保留备查）
│   ├── llm_config.yaml           # LLM 摘要配置（抽取式/API 模式切换）
│   ├── settings.json             # 管理后台全局设置
│   ├── favorite_tags.json        # 收藏自动标签规则（分类短名 / 领域词表 / 泛词黑名单）
│   └── secrets.json              # API 密钥（gitignored，建议改用 .env）
│
├── templates/                    # Jinja2 模板
│   └── weekly_report.html        # 日报 HTML 模板（文件名沿用历史约定）
│
├── scripts/                      # 运维与检查脚本
│   ├── server.sh                 # 管理后台启停脚本（start/stop/restart/status）
│   ├── daily_publish.sh          # 日更发布：跑管道 → 发布 → 归档 → 保留期清理
│   └── summary_quality_report.py # 摘要质量体检 / 改动前后回归对比
│
├── reports/                      # 生成的周报（gitignored）
│   ├── Security_Reports.html     # 桌面端完整日报
│   ├── Security_Reports_mobile.html  # 移动端轻量版
│   ├── Security_Reports.html.gz      # 预压缩版本
│   ├── Security_Reports_mobile.html.gz
│   └── data_2026-11-11*.json     # 按版本/分类拆分的详情数据（移动端按需加载）
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
│   ├── scoring_history/          # 评分统计归档（用于日对比）
│   └── state/                    # 跨轮持久状态（滚动库/台账/译文缓存/信源 ETag）
│   └── favorites/                # 文章收藏（一篇一个快照 + assets/ 图片副本，gitignored）
│
└── docs/                         # 文档
    └── superpowers/specs/
        ├── 2026-06-22-security-weekly-report-design.md  # 设计文档
        └── 2026-06-30-api-collector-design.md           # API 采集设计
```

---

## 10 步流水线详解

管道由 `pipeline/orchestrator.py` 统一编排。**任一步骤失败都会中止整个运行，且不会生成报告**（保留上一版报告），并以非零退出码结束；开跑前会清空全部中间产物，因此不存在“用上一轮旧数据重发报告”的情况。每次运行的步骤状态记录在 `data/pipeline_run.json`。步骤 9 会先把本轮收录条目并入滚动库，再按 `report.rolling` 窗口裁剪后渲染。

| # | 模块 | 输入 | 输出 | 职责 | 失败处理 |
|---|------|------|------|------|------|
| 1 | fetcher | 141 个信源配置 | `raw_items.json` | 并发抓取 RSS/API/Scraper 信源，API 密钥按平台下发，信源健康监测；支持 ETag/Last-Modified/304、同主机限速与 Retry-After；304 时复用 `data/state/feed_cache/` 上次 XML | 单信源失败不阻断；连续失败进入指数退避重试 |
| 2 | parser | `raw_items.json` | `parsed_items.json` | XML→统一 dict，HTML 去标签，时间统一归一到 UTC | 中止 |
| 3 | deduplicator | `parsed_items.json` | `deduped_items.json` | URL 规范化去重（剥追踪参数）+ rapidfuzz 标题相似去重（阈值 75%），过期过滤（>7 天） | 中止 |
| 4 | keyword\_filter (stage1) | `deduped_items.json` | `parsed_items.json` | 标题+前200字快速评分，**<30 分提前丢弃**，减少全文抓取量 | 中止 |
| 5 | fulltext\_extractor | `parsed_items.json` | `parsed_items.json`(原地增强) | 摘要 <300 字的文章抓取全文（并发 8，上限 20000 字），**含 SSRF 防护**；正文清洗为**先定位正文容器、再删模板**（删除时跳过正文容器及其祖先），只在块级标签边界换行以免把句子切碎；自研结果明显偏短时用 trafilatura 做**第二意见**（≥300 字、≥自研 1.5 倍才采用）；**同时产出白名单净化后的结构化正文 `content_html`**（供详情页渲染，图片在报告阶段本地化）；随后执行 **5.5 正文指纹去重 + 跨周台账过滤**（SimHash，Hamming≤3，72h 窗） | 中止（单条失败仅记录状态） |
| 6 | keyword\_filter (stage2) | `parsed_items.json` | `classified_items.json` | 完整评分 + 领域分类 + 内容类型。**≥80 收录，50-79 待复核，<50 丢弃** | 中止 |
| 7 | llm\_processor | `classified_items.json` | `enhanced_items.json` | 抽取式摘要：先洗掉网页噪声（导航/署名/分享/日期/聚合壳），再摘取**最值得看的连续一段**，按句边界收尾；长度按分类分档（`summary` 段），结果附来源标记 `ai_summary_kind` | 中止 |
| 8 | translator | `enhanced_items.json` | `translated_items.json` | 腾讯云 TMT，把**非中文的标题与摘要**翻译为中文；**正文不翻译**（范围见 settings 的 `translate.fields`）；**只翻最终上屏条目**且译文落盘缓存（`data/state/translate_cache.json`），额度用完立即停止并标记 | 中止（无密钥时跳过并记录状态） |
| 9 | report\_generator | `translated_items.json` | `Security_Reports.html` + 分类 JSON | Jinja2 渲染 HTML；**渲染成功后才写数据文件**，避免数据与页面不一致 | 中止（保留上一版报告） |
| 10 | mobile\_converter | `Security_Reports.html` | `Security_Reports_mobile.html` | 按模板标记剥离详情面板，注入 mobile.css/mobile.js | 中止（保留上一版报告） |

> **跨周发布台账**：第 5 步的指纹去重只处理本轮内容；`data/state/published_ledger.json`
> 记录报告与移动版都成功生成后的已发布条目，下一周自动过滤同一 URL/同一正文的重复稿。
> 台账是持久状态，不在 `INTERMEDIATE_FILES` 清理名单里；同周重跑不会拦截（否则重跑会清空本周内容）。

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
- **读者画像（新增）**：关键词分之上再叠加主题权重、深度分、信源权威度与商业噪声惩罚，
  得到 `relevance_score`，用于收录与排序。详见下节。

#### 读者画像（2026-11-09）

关键词分只能回答「像不像安全内容」，回答不了「值不值得架构师读」。因此在
`relevance_score` 中叠加四个信号：

| 信号 | 作用 | 配置位置 |
|---|---|---|
| 主题权重 | 架构/漏洞优先，产业/商业靠后 | `scoring_keywords.json` 的 `category_weights` |
| 深度分 | CVE/利用链/根因/代码/基准/架构取舍等证据加分 | 代码内置规则 + `quality.depth_bonus_max` |
| 信源权威 | S/A/B/C 四级，官方与一线研究优先 | `settings.json` 的 `quality.source_tiers` |
| 商业噪声 | Gartner/榜单/融资/发布会/产品发布、企业通讯降权或封顶 | `scoring_keywords.json` 的 `commercial_filters` |

最终分数：`关键词分 × 主题权重 × 信源系数 × 内容类型系数 + 深度加分 − 商业惩罚 − 系列惩罚`。

- 伞形大词（`网络安全`、`SOC`、`SIEM`、`SASE`、`XDR` 等）在 `ambiguity_rules` 里标为
  `standalone_score: false`：仍参与分类，但不再单独计分，避免厂商稿靠产品名词堆到收录线。
- 标题以 `Introducing/发布/推出/上线/Marketplace` 开头且深度不足的发布稿，相关性封顶 45，
  不会仅凭产品名词进入收录。
- 报告按相关性从高到低排序；侧栏按 7 个**主题分类**导航，分类 JSON 供前端按需加载。
  单信源每周最多 3 条，同一系列每周最多 1 条。

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

#### 入选依据（`score_breakdown`）

每条收录/待复核条目都会持久化 `score_breakdown`，详情面板据此展示「入选依据」：
关键词分、主题权重、信源权威系数、技术深度加分、商业噪声扣分、系列稿扣分、
发布稿封顶/权威保底是否生效、最终相关性分与收录线。这样读者不必面对一个
无法解释的 100 分，运营也能快速判断“某条为什么被收录/被降权”。

#### 评分回归（`scripts/scoring_golden_test.py`）

改评分、词表、主题权重前先跑：

```powershell
python scripts/scoring_golden_test.py            # 对比固定样本的决策与顺序
python scripts/scoring_golden_test.py --update   # 仅在人工确认后重写快照
```

样本覆盖 KEV 漏洞、厂商发布稿、奖项稿、融资稿、政策、AI 安全、系列周报等；
断言的是**决策**与**相对顺序**，不要求精确分数。快照里的 `raw_score` 允许
±12 漂移，超过会提示人工检查。

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

## 文章收藏

周报详情页提供「☆ 收藏」按钮；收藏后可在 `/bookmarks` 统一查看。收藏与周报生产链路完全解耦：

- **完整快照**：标题、摘要、正文 HTML、纯文本正文与图片副本都存进 `data/favorites/`，不依赖每周被覆盖的 `reports/data_*.json`；原文失效或图片过期也能回看。
- **独立目录**：一篇收藏一个 JSON（`<链接哈希>.json`），图片副本在 `data/favorites/assets/<id>/`；周报每轮只清理固定中间文件，不会动收藏目录。
- **自动标签**：按「主题分类短名 → CVE 编号 → 领域词表 → 具体命中关键词 → 内容类型」生成，最多 6 个；规则在 `config/favorite_tags.json`，可人工调整。
- **可编辑**：收藏页支持增删标签、写备注（失焦自动保存）、按标签筛选、关键词搜索、删除与导出 Markdown / JSON。
- **安全**：收藏页与后台共用同一套 Basic Auth；正文入库前用 `sanitize_fragment()` 白名单再净化；图片经专用接口读取并校验路径，不直接暴露目录。

收藏接口（均需登录；写操作要求 `X-Requested-With: XMLHttpRequest` 同源头）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/favorites` | 清单与标签统计 |
| GET | `/api/favorites/<id>` | 单篇完整快照 |
| POST | `/api/favorites` | 新增收藏（服务端净化、自动标签、复制图片） |
| PUT | `/api/favorites/<id>` | 修改标签 / 备注 |
| DELETE | `/api/favorites/<id>` | 删除收藏 |
| GET | `/api/favorites/assets/<id>/<file>` | 收藏图片副本 |
| GET | `/api/favorites/export?format=md\|json` | 导出全部收藏 |

> 收藏只存服务器端，`data/` 已 gitignored；升级部署会保留 `data/`，仍建议定期用 JSON 导出做备份。

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

只保存非敏感项：去重阈值与天数、翻译超时与**翻译范围**、分类顺序、日更滚动窗口（`report.rolling`）与保留期。

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
>
> “是否需要翻译”按内容判断（不依赖 `language` 标记）：中文字符≥6 且占
> 非空白字符 15% 即视为已是中文。否则「Linux内核IPv4 TCP释放后重用漏洞」
> 这类中英混排标题会被送翻，接口识别出 zh→zh 后原样返回，反而被标成
> “翻译失败”。真原样返回时也按“原文已是中文”处理，并写入缓存不再白调。

### 翻译渠道 (`config/settings.json` 的 `translate.provider`)

| 取值 | 渠道 | 凭据（`config/secrets.json` 或环境变量） | 免费额度 | 限制 |
|---|---|---|---|---|
| `tencent`（默认） | 腾讯云 TMT | `tmt_secret_id` / `tmt_secret_key`（环境变量 `TMT_SECRET_ID` / `TMT_SECRET_KEY`） | 500 万字符/月 | 默认 5 QPS，单条 1500 字符 |
| `baidu` | 百度翻译开放平台 | `baidu_appid` / `baidu_key`（环境变量 `BAIDU_APPID` / `BAIDU_KEY`） | 标准版 5 万字符/月；**实名认证升级高级版后 100 万字符/月** | 标准版 1 QPS、单条 1000 字符；高级版 10 QPS |

> 本项目每周新增约 3 万字符（含复核条目），**标准版 5 万字符只够一周多**，
> 长期用百度请务必实名升级高级版；否则建议继续用腾讯云免费额度。
> 切换渠道只需改 `translate.provider` 并填好对应密钥；两家共用同一套缓存
> （缓存键带渠道前缀，换渠道不会串用旧译文）、重试与额度快停逻辑。

### 翻译额度控制 (`config/settings.json` 的 `translate` 段)

翻译按字符计费（腾讯云文本翻译免费额度 500 万字符/月，超出后 58 元/百万字符），
四个开关用来避免额度被白白消耗：

| 键 | 默认 | 作用 |
|---|---|---|
| `only_displayed` | `true` | 只翻**最终会上报告**的条目（复用 `select_display_items()` 的展示规则）；未上屏的条目不再送翻，实测可省约一半字符 |
| `cache_enabled` | `true` | 译文落盘缓存到 `data/state/translate_cache.json`，重跑不再重复计费 |
| `retry_times` | `3` | 单条失败重试次数（退避 1s/3s/7s）；额度用完、鉴权失败等不可重试错误会立即停止，不浪费调用 |
| `cache_max_entries` | `20000` | 缓存条数上限，超出后按最后使用时间保留最近的 N 条 |

> `data/state/` 与 `data/favorites/` 一样属于**跨轮持久状态**，不在
> `INTERMEDIATE_FILES` 里，开跑不会被清空。删 `data/` 时缓存一并丢失，
> 下次运行会重新计费。
>
> 翻译状态写入 `data/translation_status.json`，字段含 `status`
> （`ok` / `partial` / `unavailable` / `quota_exhausted` / `auth_failed`）、
> 本次消耗字符 `chars_used`、估算花费 `estimated_cost_yuan`、缓存命中数
> `cache_hits` 与跳过条数 `skipped_not_displayed`。

### 评分关键词 (`config/scoring_keywords.json`)

```json
{
  "强": [{"word": "CVE-2024-", "score": 30, "category": "③ 漏洞态势...", "content_type": "漏洞披露"}],
  "中": [{"word": "ransomware", "score": 15, "category": "② 威胁情报..."}],
  "弱": [{"word": "security", "score": 5}]
}
```

每个关键词可选字段：`word`(匹配词), `score`(权重), `category`(分类), `content_type`(内容类型), `region`(地域)。

### 收藏标签 (`config/favorite_tags.json`)

控制收藏时自动生成的标签，改完无需重启服务（每次收藏时读取）：

- `category_tags`：主题分类 → 标签短名；
- `topic_rules`：领域词表（勒索软件 / 供应链攻击 / 零日漏洞 / 数据泄露 等），每条 `tag` + `any` 关键词；
- `generic_keywords`：`漏洞 / 攻击 / 安全` 等泛词，不作为独立标签；
- `keyword_aliases`：把英文或别名词归一到一个标签；
- `max_tags` / `max_cve_tags` / `max_topic_tags` / `max_keyword_tags` / `max_tag_chars`：数量与长度上限。

用户手动编辑过的标签优先于自动标签，系统不会在后续收藏或重跑时覆盖。

### 全局设置 (`config/settings.json`)

```json
{
  "dedup": {
    "similarity_threshold": 75,    // 标题模糊去重阈值（0-100）
    "max_days": 7,                 // 文章过期天数
    "near_dup_hamming": 3,         // 正文 SimHash 近重复阈值（0-64，越小越严）
    "window_hours": 72,            // 近重复比对时间窗（小时）
    "ledger_ttl_days": 21,         // 发布台账保留天数（日更口径，3 周）
    "near_dup_enabled": true,      // 正文近重复合并总开关
    "ledger_enabled": true         // 跨周台账过滤总开关
  },
  "translate": {
    "provider": "baidu",          // 翻译渠道：tencent（腾讯云 TMT）/ baidu（百度翻译）
    "timeout": 8,                  // 单条翻译超时（秒）
    "fields": ["title", "ai_summary"],  // 翻译范围：标题 + 摘要（正文不翻译）
    "only_displayed": true,       // 只翻最终上屏的条目（省额度）
    "cache_enabled": true,        // 译文落盘缓存（data/state/translate_cache.json）
    "retry_times": 3,             // 单条失败重试次数（额度/鉴权错误立即停止）
    "cache_max_entries": 20000    // 缓存条数上限
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
    "⑦ 产业与商业动态",
    "未分类"
  ],
  "report": {
    "rolling": {
      "enabled": true,                // 日更滚动累加总开关
      "window_days": 14,              // 滚动窗口天数（首页只展示 first_seen 在窗口内的条目）
      "max_per_source": 8             // 窗口内单信源最多展示条数
    }
  },
  "reader_profile": {
    "max_total_items": 120             // 首页最多展示条数
  },
  "quality": {
    "authority_multipliers": {"S": 1.2, "A": 1.1, "B": 1.0, "C": 0.85},
    "depth_bonus_max": 60,
    "commercial_penalty_max": 100,
    "series_penalty": 25,
    "quotas": {"max_per_source": 3}
  },
  "fetch": {
    "min_host_interval_seconds": 0.6,  // 同一主机两次请求的最小间隔（秒）
    "max_per_host_concurrency": 2      // 同一主机并发上限
  },
  "fulltext": {
    "fallback_enabled": true           // trafilatura 第二意见总开关
  },
  "images": {
    "enabled": true,                   // 正文图片本地化总开关
    "max_bytes": 5242880,              // 单张图片上限（字节）
    "max_per_article": 12,             // 单篇最多保留多少张图
    "timeout": 10,                     // 单张下载超时（秒）
    "concurrency": 4,                  // 下载并发
    "retention_days": 14               // 本地图片保留天数（同时也是 retention 命令的默认值）
  }
}
```

### 日更与滚动窗口 (`config/settings.json` 的 `report.rolling` 段)

每天 08:00 跑一次管道，但报告内容不是“当天抓到的条目”，而是**近 `window_days` 天的累加结果**：

| 键 | 含义 | 默认 |
|---|---|---|
| `enabled` | 滚动累加总开关；关闭后回退为“只用本轮收录条目” | `true` |
| `window_days` | 滚动窗口天数，条目按 `first_seen`（首次出现时间）入选 | `14` |
| `max_per_source` | 窗口内单信源最多展示条数，避免高产信源霸榜 | `8` |

滚动库落盘在 `data/state/rolling_items.json`（不在开跑清理范围内）：

- 只收 `filter_decision != review` 的收录条目；同一条目再次出现时**以新快照为准，但保留最早的 `first_seen`**；
- 条目去重依次按 `item_id` → 规范化 URL → 标题三级兜底；
- 窗口外的条目不再展示，`python app.py retention` 会把对应的 `data_*.json` 与图片目录一起删掉（不备份）；
- 首页总条数仍由 `reader_profile.max_total_items` 截断（当前 120）。

保留期与回滚：

- 默认保留 14 天（取自 `images.retention_days`，回退到 `report.rolling.window_days`），超期运行产物直接删除；
- **收藏快照（`data/favorites/`）永久保留**，不受清理影响；
- 回滚：`report.rolling.enabled=false` + 调度改回每周一次（已删数据不可恢复，回滚前先备份）。

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

### 正文结构化与图片本地化（步骤 5 / 9）

报告详情页原先只展示纯文本：标题层级、列表、代码块、表格在抽取阶段就丢了，图片也不存在，
模板只能按句号硬分段，读者看到的是“排版碎、没图”的正文。现在改为：

| 环节 | 做法 | 为什么 |
|---|---|---|
| 结构化抽取 | 复用“定位正文 → 去模板”的结果，序列化为 HTML；自研结果偏短或明显缺图时用 trafilatura 的 HTML 输出兜底 | 与纯文本链路共用同一套容器选择规则，不维护两套正文识别逻辑 |
| 白名单净化 | 只保留 `h1-h6/p/ul/ol/li/blockquote/pre/code/table/figure/img/a/strong/em/br/hr` 等语义标签；属性只留 `a[href]`、`img[src/alt/title]`、`td/th[colspan/rowspan]` | 正文来自外部网站，而报告页与后台配置接口同源；不净化就等于把 `/api/config/*` 写接口交给外部内容方 |
| 危险内容 | 剔除 `script/style/iframe/form/svg/video`、所有 `on*` 事件属性、`javascript:` 链接；`.svg` 图片与 1×1 跟踪像素直接丢弃 | SVG 可内嵌脚本；跟踪像素会泄漏读者 IP |
| 图片本地化 | 只对最终展示的条目下载正文图片，存 `reports/assets/img/<item_id>/`，正文改写为站内绝对路径 `/reports/assets/img/...`；单图 ≤5MB、单篇 ≤12 张，并发 4 | 热链图片会 404/被防盗链，离线打开与后续导出都不可用；不给被过滤掉的文章白下载 |
| 失败兜底 | 下载失败的图片保留原始 URL（带 `referrerpolicy=no-referrer`），前端 `onerror` 隐藏裂图 | 图片是锦上添花，单张失败不能阻断报告生成 |
| 清理 | 按 `images.retention_days` 清理过期图片目录，当前条目引用的目录始终保留 | 避免磁盘无限增长 |

配套约束：

- `content_html` **只用于展示**；评分、摘要、正文指纹仍走纯文本（`summary` / `full_body`），行为与改动前一致。
- 渲染时优先用 `content_html`，为空（未抓到全文）才回退到旧的纯文本分段。
- 报告页加了 CSP：`img-src` 限定 `self/https/http/data`，`object-src 'none'`，作为净化之外的第二道保险。
- 移动端同样渲染结构化正文，表格在小屏可横向滚动。
- 图片目录与 `item_id`（规范化 URL 的 sha1 前 16 位）绑定，同一篇文章重跑不会重复下载。

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
| `TMT_SECRET_KEY` | 腾讯云翻译 API 密钥 Key（兼容旧名 `TENCENT_SECRET_KEY`） | **是**（`provider=tencent` 时） |
| `BAIDU_APPID` | 百度翻译开放平台 APP ID | **是**（`provider=baidu` 时） |
| `BAIDU_KEY` | 百度翻译开放平台密钥 | **是**（`provider=baidu` 时） |
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

# 评分决策/顺序回归（改评分或词表前必跑）
python scripts/scoring_golden_test.py

# 正文近重复合并清单（人工抽检误合并率）
python scripts/dedup_report.py
```

### 红线

- 不要直接运行 `pipeline/utils/scraper.py` 或 `pipeline/orchestrator.py` — 始终通过 `app.py` 入口
- **任何密钥都不得写入 `config/` 下的配置文件或提交进版本库** — 腾讯云密钥用 `.env` 或 `config/secrets.json`，其余用环境变量
- 评分阈值（stage1: 30, stage2: 80）改动需谨慎，影响报告条数质量
- `config/source_config.yaml` 中 `enabled: false` 的信源不要删除，留作记录
- `templates/weekly_report.html` 中的 `<!--DETAIL_PANEL_START-->` / `<!--DETAIL_PANEL_END-->` 标记供移动版转换定位详情面板，不要删除
- 保留期清理**不得删除** `data/favorites/`（收藏是完整快照，永久保留），也不得删除最新一版报告数据；清理不备份，删了不可恢复
- 滚动库 `data/state/rolling_items.json` 不得加入 `INTERMEDIATE_FILES`（否则每天累加结果会被开跑清空）

---

## 深入文档

- [设计文档](docs/superpowers/specs/2026-06-22-security-weekly-report-design.md) — 分类体系、标签维度、布局规范
- [API 采集设计](docs/superpowers/specs/2026-06-30-api-collector-design.md) — API 采集通道设计
