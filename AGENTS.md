# SecurityInfo — 项目记忆（Agent 指令）

> **单一事实来源**：本文与代码不一致时以代码为准，并顺手把本文改对。
> 这是本仓库自动加载的 Agent 指令文件。改动本仓库前，请先读
> **§3 硬约束** 与 **§5 已定型的决策与原因**——那两节里多数"看起来奇怪"的写法
> 都是有意为之，直接"优化"会踩回已经修过的坑。

---

## 1. 这是什么

从 **141 个信源（当前启用 107 个）** 抓取网络安全资讯，经 10 步管道评分分类后，
生成 HTML 周报（桌面端 + 移动端自适应）。评分门槛：**≥80 收录，50–79 待复核，<50 丢弃**。

技术栈：Python 3.11 / httpx / feedparser / BeautifulSoup / rapidfuzz / Jinja2 / jieba + numpy。
数据源三种：RSS、JSON API、静态页爬取（`scraper_config` 选择器）。

## 2. 跑起来

```bash
pip install -r requirements.txt     # 版本已锁定，勿随意放宽
python app.py --run                 # 完整管道（实测约 2–3 分钟）
python app.py --run --skip-fetch    # 跳过抓取，用已有 raw_items.json 重跑后续
python app.py server [port]         # 管理后台，默认 8090
```

**退出码即成败**：任何步骤失败 → 非零退出，且不生成报告。
依赖可用项目 `venv`（远端）或临时目录 + `PYTHONPATH`（本地验证）。
`app.py` 与后台都会显式从项目根加载 `.env`，不依赖当前工作目录。

## 3. 硬约束

- **不要直接运行** `pipeline/utils/scraper.py` 或 `pipeline/orchestrator.py`，始终走 `app.py`
- **任何密钥都不得写入 `config/` 下的配置文件，也不得提交进版本库**。腾讯云密钥只从
  `.env` 或 `config/secrets.json` 读取；后台翻译页已不提供密钥输入框，保存设置时
  未声明的键会被白名单直接丢弃
- **改评分阈值/词表前必须用真实数据回归**（流程见 §7），不要凭感觉调
- `config/source_config.yaml` 里 `enabled: false` 的信源**不要删除**，留作记录
- `templates/weekly_report.html` 的 `<!--DETAIL_PANEL_START/END-->` 标记供移动版转换
  定位详情面板，**不要删除**
- `.gitignore` 里的 `Output/` 规则**不要移除**（内含修复前的仓库备份）
- 所有路径常量必须由 `pipeline/utils/__init__.py` 的 `PROJECT_ROOT` 推导为**绝对路径**，
  不要再写 `Path("data")` 这类依赖当前工作目录的相对路径
- 文档要与行为同步：改了功能**必须**同步更新本文与 `README.md`

## 4. 架构与数据流

```
app.py                       统一入口（退出码反映管道成败）
AGENTS.md                    本文件（项目记忆 + Agent 指令）
README.md                    面向使用者的完整说明
pipeline/
  orchestrator.py            编排 10 步；开跑清空中间产物；失败即中止
  steps/
    fetcher.py               并发抓取；API 密钥按平台下发；信源健康与指数退避
    parser.py                解析为统一结构；时间归一到 UTC；相对链接补全
    deduplicator.py          URL 规范化去重 + 标题相似去重 + 过期过滤
    keyword_filter.py        stage1 预筛 / stage2 完整评分
    scorer.py                评分引擎（词级加权 + 位置加成 + 分类梯度）
    fulltext_extractor.py    短摘要文章抓原文（并发 8；SSRF 防护）
    llm_processor.py         清洗噪声 + 最优连续窗口抽取式摘要（LLM 分支预留未实现）
    translator.py            非中文标题与摘要 → 中文（腾讯云 TMT；正文不翻）
    report_generator.py      Jinja2 → HTML；**渲染成功后才写数据文件**
    mobile_converter.py      桌面 → 移动版（按模板标记剥离详情面板）
  utils/__init__.py          绝对路径、原子写入、时间归一、SSRF 防护、密钥加载
  utils/scraper.py           静态页面抓取辅助
  assets/mobile.{css,js}     移动端样式与交互
config/
  source_config.yaml         信源配置（141 条，启用 107 条）
  scoring_keywords.json      评分词表与阈值（改前先看 §5、§7）
  settings.json              去重阈值/天数、翻译超时与范围、摘要长度分档、分类顺序（无密钥）
  llm_config.yaml            LLM 配置（预留，默认 enabled: false）
  keywords.json              历史遗留，**当前代码不读取**
server/config_server.py      管理后台（静态文件白名单 + 认证 + 写入校验）
server/config.html           管理后台页面
templates/weekly_report.html 周报模板
scripts/server.sh            Server 管理脚本（优先 venv 解释器）
scripts/summary_quality_report.py  摘要质量体检 / 改动前后回归对比（入库）
reports/                     生成的周报（gitignored）
data/                        管道中间数据（gitignored）
docs/                        设计与历史计划文档
```

### 10 步与各自的产物

| # | 模块 | 产物 | 职责 |
|---|------|------|------|
| 1 | fetcher | `raw_items.json` | 并发抓取启用的 RSS/API/Scraper 信源；连续失败进入指数退避 |
| 2 | parser | `parsed_items.json` | XML → 统一结构；时间归一到 UTC 无时区 |
| 3 | deduplicator | `deduped_items.json` | URL 规范化去重 + 标题相似去重 + 过期过滤（>7 天） |
| 4 | keyword_filter(stage1) | `parsed_items.json` | 标题+前200字快速评分，<30 提前丢弃（省掉全文抓取） |
| 5 | fulltext_extractor | 原地增强 `parsed_items.json` | 摘要过短的文章抓原文（并发 8、上限 20000 字） |
| 6 | keyword_filter(stage2) | `classified_items.json` | 完整评分 + 分类 + 内容类型 + 阈值判定 |
| 7 | llm_processor | `enhanced_items.json` | 清洗噪声 → 最优连续窗口抽取式摘要 → `ai_summary` + `ai_summary_kind`（不做翻译） |
| 8 | translator | `translated_items.json` | 非中文**标题与摘要** → 中文；正文不翻译 |
| 9 | report_generator | `Security_Reports.html` + `data_<week>*.json` | 渲染成功后才落盘数据文件 |
| 10 | mobile_converter | `Security_Reports_mobile.html` | 剥离详情面板，注入移动端 CSS/JS |

阶段 1 与阶段 2 共用同一套评分口径，分值可直接比较。运行状态见 `data/pipeline_run.json`。

### 两个分数

- `confidence_score`：**截顶到 100**，只用于门槛判断（语义是"达到门槛"）
- `raw_score`：**不截顶**的原始证据强度，用于报告内排序与详情展示

**不要用 `confidence_score` 排序**：实测约 78% 的收录条目同为 100 分，排序会失去意义。

### 移动端

- 服务端按 `User-Agent` 返回对应版本（同路径不同内容，不重定向）
- 移动版内联列表字段，详情从 `/reports/data_<week>[_cat_N|_review].json` 按需 fetch
- 交互三层：文章列表 → 左侧分类抽屉 → 右侧详情面板

## 5. 已定型的决策与原因

> 以下每条都是踩过坑之后定的。**要改先看清原因，并做 §7 的回归。**

| 决策 | 原因（踩过的坑） |
|---|---|
| 任一步骤失败即中止，且**不生成报告** | 原先各步 `skip_ok=True`，某步失败后用上一轮遗留的 `parsed_items.json` 继续跑，结果是"新信源告警 + 旧正文"的静默重发 |
| 开跑先清空**全部**中间产物；每步产物独立留档 | 同上；从结构上杜绝跨轮数据串用。`--skip-fetch` 时保留 `raw_items.json` |
| 时间必须归一到 **UTC 无时区** | 带时区的时间戳与无时区 cutoff 比较会抛 `TypeError`，被 `except` 吞掉后**过期过滤整体失效**，任意年代旧文都能进本周周报 |
| 报告内按 `raw_score` 降序 | 截顶分大量并列 100，无法分辨"哪条更重要" |
| `min_strong_for_accept` **默认 0（关闭）** | 实测取 2 时，「Citrix 警告 NetScaler 漏洞正被在野利用」这类只命中 1 个强词的安全要闻会被降级——词表以 AI 话题词为主，`vulnerability`/`exploit`/`ransomware` 等事件词多在中词层 |
| 翻译范围默认只有 `title` + `ai_summary` | 需求明确"只翻标题和摘要，正文不翻译"；范围在 `settings.json` 的 `translate.fields`，**改配置而不是改代码** |
| 摘要必须先洗噪声、再按**连续窗口**摘取、按**句边界**收尾 | 改前实测（142 条真实产物）：74.6% 的摘要是半句话（拼完超 500 字直接从中间切开）、25.4% 开头混着导航/署名/分享按钮、181 处英文句子粘连（`accessed.The`）、49.3% 与原文开头逐字相同。散句打分拼接会东抽一句西抽一句 |
| 摘要长度走 `settings.json` 的 `summary` 段（含分类分档），不写死在代码里 | 改前 72.5% 的摘要正好卡在写死的 500 字上限——长度由上限而不是内容决定。政策/产业类（④⑤）默认 300 字 |
| 每条摘要写 `ai_summary_kind`，报告显示「自动提炼 / 原文节选」 | 兜底截断与正常提炼此前无法区分，读者会把"原文开头几句"当成摘要 |
| 报告端剥掉"正文以摘要开头"的那一段 | 改前 39.4% 的收录条目在同一屏把同一段话显示两遍（摘要栏 + 正文栏）；摘要被翻译成中文时与原文对不上，自然不会误剥 |
| 后台静态文件走**白名单** | 原先直接复用 `SimpleHTTPRequestHandler`，匿名即可下载 `.env`、`config/`、`docs/server_deployment.md`（含 SSH 凭据）与全部源码（已实测复现并修复） |
| 密钥不进 `config/settings.json` | 该文件曾明文存放腾讯云密钥并推送到公开仓库；现只从 `.env` / `config/secrets.json` 读取 |
| 后台写入前做结构校验 + 原子替换 | 原先"写进去就算成功"，一次空内容保存即可让下一次运行在第 1 步崩溃 |
| 全文抓取与爬虫必须走 `safe_get()` | RSS 条目链接由外部内容方控制，可指向 `127.0.0.1`/内网/云元数据；抓回内容会进周报正文，形成 SSRF + 内容外带 |
| 相对链接用 `urljoin` 统一补全 | 原先只在链接以 `/` 开头时才拼 `link_base`，`thread-293107.htm` 这类被原样保留，周报里出现点不开的死链（实测看雪论坛 10 条全中） |
| 去重要**规范化** URL 与标题 | 原先带 `utm_*` 参数或标题差一个标点即视作不同条目 |
| 信源连续失败改为**指数退避重试** | 原先拉黑后不再尝试，而计数只在成功时归零 → 永久禁用且失败数虚高 |
| 单信源响应有 8MB 上限 | 实测 Google Project Zero 的订阅源有 11.3MB，不设限会把内存与 `raw_items.json` 撑爆 |
| 移动版按**标记**剥离详情面板 | 原先逐字符数 `<div>`/`</div>` 配平，遇到面板内 `<script>` 字符串就错位，产出损坏 HTML |
| 全文抓取并发 8 条 | 原先逐条串行、每条 15s 超时，数百条时耗时数十分钟 |
| `pipeline/utils/__init__.py` 定义所有绝对路径 | 原先各模块用相对路径，换个工作目录就把数据写到别处甚至别的盘符根目录 |
| 项目记忆文件是 `AGENTS.md` | 不再使用 Claude Code，故弃用 `CLAUDE.md`；`AGENTS.md` 是本 Harness 的 Agent 指令约定，且工具中立 |

## 6. 已知取舍与无解项

- **语义类误收录无解**：如「Anthropic 发布 Claude Haiku 5.5」——正文确实讨论了渗透测试
  （命中 3 个强词），任何关键词方案都拦不住，需语义判断。`llm_processor` 的 LLM 分支
  是预留位，接入外部模型才可能解决
- **长文天然占优**：正文越长命中关键词的机会越多，与内容是否更重要无关。实测到
  "全文抓取成功率从 55% 升到 90% 时收录数从 88 升到 109"。要消除需按长度归一化
- **`ssl_verify: false`**：`source_config.yaml` 中 Seebug 一项关闭了 TLS 校验（该站证书
  链问题），其内容存在中间人篡改可能，运行时会有告警。证书修好后应删除该行
- **词表分层是"反的"但不要急着改**：事件词在中词层、AI 话题词在强词层，看似倒置；
  实测把它纠正回来会**牺牲 6 条正当 AI 安全内容只换来移出 1 条产业新闻**——因为
  「① AI/LLM 安全」是本项目的一等分类，其词汇本身就是 AI 话题词。要动必须重做回归
- **能显著减少产业新闻的实测有效改法**（尚未实施，供后续参考）：负向过滤补充
  "商业/产业动态"词（融资/估值/营收/上市/推出/收购/合并/厂商/成本降低/吞吐提升/
  市场报告/财报/定价/订阅），并把负向过滤的豁免条件由"存在任意强词"改为"存在
  **具体安全事件词**"。实测收录 109→106，移出 3 条产业/贸易内容，**零误伤**
- **抽取式摘要的天花板（改不掉，只能缓解）**：摘要永远是原文里出现过的句子，
  所以"读懂后用一句话概括"做不到；个别信源的 RSS 正文本身就混着无关内容
  （实测 AI Hot 的「OpenAI 推出 GPT-6」正文里夹着烤羊肉的待办清单），
  摘要只能跟着脏。要真正改写必须接入外部模型（`llm_processor._call_llm` 是预留位，
  主流程当前**从不调用**它，所以后台勾"启用 LLM"也不会改变行为）
- **历史遗留**：`config/keywords.json`、`docs/superpowers/plans/*`（其中仍有对
  `CLAUDE.md` 的引用）与当前实现无关，不要据此改代码

## 7. 改完怎么验证

```bash
python -m py_compile $(git ls-files '*.py')          # 语法
```

离线端到端与专项回归脚本位于 `Output/修复审查问题/Temp/`（该目录 gitignored，不入库）：

| 脚本 | 覆盖 |
|---|---|
| `test_pipeline_e2e.py` | 沙箱内用合成信源跑步骤 2–10；并验证失败时不出报告、无旧产物残留 |
| `test_config_server.py` | 后台静态文件白名单、认证与锁定、配置写入校验 |
| `test_scoring_dedup.py` | 阈值、多分类计分、阶段1/2 口径、过期过滤、去重 |
| `test_link_fix.py` | 相对链接补全（单元 + 真实站点） |
| `test_translate_scope.py` | 翻译范围（只翻标题与摘要） |
| `scripts/summary_quality_report.py`（**入库**） | 摘要质量体检与改动前后回归：半句截断率、网页噪声率、英文粘连、摘要与正文栏重复率、长度分布、来源标记分布，并逐条打印变化的摘要 |

**调摘要逻辑的流程**（与调评分同等对待，不要凭感觉调）：

1. 用同一份 `data/classified_items.json` 当评测集（抓取波动会影响条目集合，跨轮对比不公平）
2. 跑 `scripts/summary_quality_report.py 改前.json 改后.json` 出指标与逐条差异
3. **逐条看变化的摘要**，确认没有把好内容洗掉、没有把正文开头当摘要

**调评分/词表的强制流程**（不要凭感觉调）：

1. 用最近一次真实运行的 `data/translated_items.json` 当评测集
2. 写脚本对比"改动前 vs 改动后"的收录条数与**具体被移出的条目清单**
3. **逐条看被移出的清单**，确认没有误伤真安全情报——只看总数会被平均掉
4. 特别注意：任何"提高证据门槛"的改动都会误伤"标题简洁但确实是安全要闻"的条目

## 8. 配置与环境变量

`.env`（gitignored，参考 `.env.example`）：

| 变量 | 用途 | 必需 |
|---|---|---|
| `TMT_SECRET_ID` / `TMT_SECRET_KEY` | 腾讯云翻译（旧名 `TENCENT_SECRET_ID/KEY` 兼容） | 翻译步骤必需，缺失则跳过并如实标注 |
| `CONFIG_USERNAME` / `CONFIG_PASSWORD` | 管理后台 Basic Auth（留空则不启用认证） | 强烈建议设置 |
| `SCHOLAR_API_KEY` | Semantic Scholar（可选，无 key 有频率限制） | 可选 |
| `GITHUB_TOKEN` | GitHub API（可选，无 token 有频率限制） | 可选 |

`config/settings.json` 关键项：

```jsonc
{
  "dedup":     { "similarity_threshold": 75, "max_days": 7 },
  "translate": { "timeout": 8, "fields": ["title", "ai_summary"] },  // 正文不翻译
  "summary":   { "max_chars": 500, "short_max_chars": 300,           // 摘要长度（含分档）
                 "short_categories": ["④ 政策法规与标准框架", "⑤ 产业动态与技术趋势"],
                 "min_chars": 80 },                                  // 低于则退化为「原文节选」
  "category_order": ["① AI/LLM 安全", "…", "未分类"]
}
```

`config/scoring_keywords.json` 的 `thresholds`：`accept_threshold` 80 /
`review_threshold` 50 / `stage1_drop_below` 30 / `min_strong_for_accept` 0。

## 9. 远端部署（Hermes 服务器）

- 部署信息（含 SSH 凭据）见 `docs/server_deployment.md`（gitignored，**不提交**）
- 路径 `/home/ubuntu/.hermes/profiles/zhuanjia/workspace/hub/cybersec/`；入口 `venv/bin/python app.py --run`
- 管理后台 `scripts/server.sh {start|stop|restart|status}`，端口 8090
- Hermes 定时任务 `网络安全周报自动更新`：每周一 08:00（`0 8 * * 1`）
- 部署方式：本地打包 `tar`（排除 `.git/.claude/data/reports/.env`）→ scp → 解压；
  `data/`、`reports/` 由远端生成，`.env`、`venv` 保留远端版本
- 远端服务器是 Linux：**不要把带 CRLF 的脚本打包上线**（`.gitattributes` 已统一为 LF）

注意事项：

- `scripts/server.sh` 优先用项目 `venv/bin/python3`，无 venv 才回退系统 `python3`
- 远端 `.env` 的后台密码仍为默认 `you_should_change_this`，**建议修改**
- `hub/` 下有孤儿文件 `server_with_ua.py`（旧版，未使用）
- Hermes 运行后会在项目根生成 `cybersec_weekly_<日期>.html` 冗余副本
- **不要修改 Hermes 自身配置**（`~/.hermes/` 由 Hermes 管理）

## 10. 深入文档

- [设计文档](docs/superpowers/specs/2026-06-22-security-weekly-report-design.md) — 分类体系、标签维度、布局规范
- [API 采集设计](docs/superpowers/specs/2026-06-30-api-collector-design.md) — API 采集通道设计
- `README.md` — 面向使用者的完整说明（配置项、流水线、红线）

## 11. 本文件的维护约定

- 本文件是**正本**，不要另建同义的项目记忆文件（历史上曾用 `CLAUDE.md` / `memory.md`，已废弃）
- 改了架构、阈值、决策或约束 → 同步改本文；**文档与行为不一致视为任务未完成**
- 新增"看起来奇怪"的写法时，在 §5 补一行说明原因，避免后来者改回去
