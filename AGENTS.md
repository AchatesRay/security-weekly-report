# AGENTS.md — SecurityInfo 网络安全态势周报系统（开发类工作区）

> 本文件是本仓库的**项目记忆正本**，也是 Agent 的常驻指令：领域知识、"看起来奇怪但有意为之"的写法及原因都在这里。
> **单一事实来源**：与代码不一致时以代码为准，并顺手把本文改对。
> 动手前至少读 **§3 硬约束** 与 **§5 已定型的决策与原因**——那两节里多数写法都是踩过坑之后定的，直接"优化"会踩回已经修过的坑。

---

## 0 全局规范（会话启动第一步）

会话启动时，首先读取并严格遵守全局规范正本：

`C:\Users\LeiChuanhou\AppData\Roaming\QAgentWork\qagent-share\gateway\user_sso\05e0b07df010253e\AGENTS-GLOBAL.md`

全局规范承载所有工作区共同遵守的通用规则（Python / PowerShell 工程规范、安全边界、产出规范等），**本文不复制**。
优先级：用户当次明确指令 ＞ 本文件 ＞ 全局正本 ＞ 历史记忆。

### 0.1 会话启动流程（本工作区）

1. 读取 `AGENTS-GLOBAL.md`（全局规范正本）
2. 长期记忆由系统自动注入（`.qagent/MEMORY.md`），无需手动读取；新增结论按 `- <规则> @(日期)` 追加
3. 先读本文 §3 硬约束、§5 已定型决策，再动代码
4. **续接会话不豁免**：续接摘要只含任务上下文、不含全局规则，未读正本前不得修改任何文件

---

## 1 本工作区特有事实

- **项目名称**：SecurityInfo（仓库名 `security-weekly-report`）
- **仓库**：<https://github.com/AchatesRay/security-weekly-report>
- **工作目录**：`D:\Qagent\Project\security-weekly-report`
- **业务定位**：开发类 — 自动采集网络安全资讯、评分分类并生成双端 HTML 周报的内容生产系统
- **技术栈**：Python（3.11 环境验证，本机 3.13 可运行）/ httpx / feedparser / BeautifulSoup4 / lxml / rapidfuzz / Jinja2 / jieba + numpy；翻译用腾讯云 TMT
- **规模**：141 个信源（当前启用 107），10 步流水线；评分门槛 **≥80 收录、50–79 待复核、<50 丢弃**
- **运行环境**：Windows 开发 + Linux 远端定时任务；部署脚本要求 **LF** 换行
- **产物**：`reports/Security_Reports.html`（桌面）、`Security_Reports_mobile.html`（移动）、`data_<week>*.json`（分类详情）、同名 `.gz` 预压缩

### 1.1 目录结构（一级）

```
app.py              统一 CLI 入口（退出码即管道成败）
pipeline/           10 步数据管道 + 工具 + 移动端静态资产
  steps/            各步骤实现（fetcher … mobile_converter）
  utils/            绝对路径、原子写入、时间归一、SSRF 防护、密钥加载
  assets/           mobile.css / mobile.js
server/             管理后台（REST API + 静态文件白名单 + Basic Auth）
config/             信源、评分词表、设置、LLM 配置（密钥一律不入库）
templates/          周报 Jinja2 模板（含移动版剥离标记）
scripts/            server.sh、摘要质量回归脚本
docs/               设计与历史计划文档（README 为文档总索引）
data/ reports/      运行产物（gitignored）
Output/ .qagent/ .cache/   本地工作区与备份（gitignored，禁止入库）
```

---

## 2 📌 开发类专属规范（本项目）

### 2.1 环境与依赖

- `requirements.txt` 是**精确版本锁定**，不要随意放宽或升级；确需升级须成组进行，并跑一次 `python app.py --run --skip-fetch` 验证
- 安装：`pip install -r requirements.txt`；远端用项目内 `venv`
- `.env`、`config/secrets.json`、`data/`、`reports/` 一律不入库

### 2.2 常用命令

```powershell
python app.py --run                    # 完整管道（实测 2–3 分钟）
python app.py --run --skip-fetch       # 跳过抓取，用已有 raw_items.json 重跑后续
python app.py server 8090              # 管理后台（默认 8090）
python -m py_compile (git ls-files '*.py')              # 语法自查
python scripts/summary_quality_report.py                # 摘要质量体检
python scripts/summary_quality_report.py 改前.json 改后.json   # 摘要回归对比
```

- **始终走 `app.py` 入口**；`pipeline/orchestrator.py`、`pipeline/utils/scraper.py` 不单独执行
- 新建路径常量必须从 `pipeline/utils/__init__.py` 的 `PROJECT_ROOT` 推导**绝对路径**，禁止 `Path("data")` 这类依赖当前工作目录的写法
- `app.py` 与后台都会显式从项目根加载 `.env`，不依赖启动时的工作目录

### 2.3 编码与换行

- 仓库统一 LF（见 `.gitattributes`）；`scripts/server.sh` 带 CRLF 上线会报 `$'\r': command not found`
- Windows 下 PowerShell 5.1 执行含中文的 `.ps1` 编码规则见全局正本 §11

### 2.4 提交与分支

- 提交信息沿用仓库既有风格：`feat|fix|docs|chore|refactor(scope): 中文说明`
- 一次提交只做一件事；重构与功能改动分开
- 分支以 `main` 为准，保持历史线性、可快进

### 2.5 部署（已脱敏）

- 部署方式：本地打包 `tar` → `scp` → 远端解压；远端为 Linux，定时任务每周一 08:00 触发
- 具体主机地址、目录、凭据见 `docs/server_deployment.md`（**gitignored，禁止提交**）
- 打包排除 `.git/ .qagent/ data/ reports/ .env`；`data/`、`reports/`、`.env`、`venv` 保留远端版本
- **不要修改远端任务调度系统（Hermes）自身的配置**

---

## 3 🔴 硬约束

- **不要直接运行** `pipeline/utils/scraper.py` 或 `pipeline/orchestrator.py`，始终走 `app.py`
- **任何密钥都不得写入 `config/` 下的配置文件，也不得提交进版本库**。腾讯云密钥只从
  `.env` 或 `config/secrets.json` 读取；后台保存设置时未声明的键会被白名单直接丢弃
- **改评分阈值/词表前必须用真实数据回归**（流程见 §7），不要凭感觉调
- `config/source_config.yaml` 里 `enabled: false` 的信源**不要删除**，留作记录
- `templates/weekly_report.html` 的 `<!--DETAIL_PANEL_START/END-->` 标记供移动版转换
  定位详情面板，**不要删除**
- `.gitignore` 里的 `Output/` 规则**不要移除**（内含修复前的仓库备份与回归脚本）
- 所有路径常量必须由 `PROJECT_ROOT` 推导为**绝对路径**
- 后台静态文件必须走**白名单**，不得直接暴露项目目录
- 全文抓取与爬虫必须走 `safe_get()`（SSRF 防护）
- 文档要与行为同步：改功能**必须**同步更新本文与 `README.md`

---

## 4 架构与数据流

```
app.py                  统一入口（退出码反映管道成败）
pipeline/orchestrator.py 编排 10 步；开跑清空中间产物；失败即中止
pipeline/steps/*         各步骤实现
pipeline/utils/*         绝对路径、原子写入、时间归一、SSRF 防护、密钥加载
server/config_server.py  管理后台（静态白名单 + 认证 + 写入校验）
templates/weekly_report.html  周报模板
scripts/server.sh        后台启停（优先 venv 解释器）
```

| # | 模块 | 产物 | 职责 |
|---|------|------|------|
| 1 | fetcher | `raw_items.json` | 并发抓取 RSS/API/Scraper 信源；密钥按平台下发；健康监测与指数退避 |
| 2 | parser | `parsed_items.json` | XML/HTML → 统一结构；时间归一到 UTC 无时区；相对链接补全 |
| 3 | deduplicator | `deduped_items.json` | URL 规范化去重 + 标题相似去重（阈值 75）+ 过期过滤（>7 天） |
| 4 | keyword_filter (stage1) | `parsed_items.json` | 标题+前 200 字快速评分，<30 提前丢弃，省掉全文抓取 |
| 5 | fulltext_extractor | 原地增强 | 摘要过短文章抓原文（并发 8、上限 20000 字）；先定位正文再删模板，只在块级标签边界换行 |
| 6 | keyword_filter (stage2) | `classified_items.json` | 完整评分 + 分类 + 内容类型 + 阈值判定 |
| 7 | llm_processor | `enhanced_items.json` | 清洗噪声 → 连续窗口抽取式摘要 → `ai_summary` + `ai_summary_kind`（不做翻译） |
| 8 | translator | `translated_items.json` | 非中文**标题与摘要** → 中文；正文不翻译 |
| 9 | report_generator | HTML + 分类 JSON | Jinja2 渲染；**渲染成功后才写数据文件** |
| 10 | mobile_converter | 移动版 HTML | 按模板标记剥离详情面板，注入移动端 CSS/JS |

- **两个分数**：`confidence_score` 截顶 100，只用于门槛判断；`raw_score` 不截顶，用于报告排序。
  **不要用 `confidence_score` 排序**——实测约 78% 的收录条目同为 100 分。
- **流程文件**：步骤间以 `data/` 下 JSON 传递，可独立重跑；运行状态见 `data/pipeline_run.json`。
- **移动端**：服务端按 `User-Agent` 返回对应版本（同路径不同内容）；详情按分类拆 JSON 按需 `fetch`；
  交互三层：文章列表 → 左侧分类抽屉 → 右侧详情面板。
- 各步骤的输入输出、字段与阈值细节见 `README.md`「10 步流水线详解」，本文只保留改动时需要知道的约束。

---

## 5 已定型的决策与原因

> 以下每条都是踩过坑之后定的。**要改先看清原因，并做 §7 的回归。**

| 决策 | 原因（踩过的坑） |
|---|---|
| 任一步骤失败即中止，且**不生成报告** | 原先各步 `skip_ok=True`，某步失败后用上一轮遗留的 `parsed_items.json` 继续跑，结果是"新信源告警 + 旧正文"的静默重发 |
| 开跑先清空**全部**中间产物；每步产物独立留档 | 同上；从结构上杜绝跨轮数据串用。`--skip-fetch` 时保留 `raw_items.json` |
| 时间必须归一到 **UTC 无时区** | 带时区时间戳与无时区 cutoff 比较会抛 `TypeError`，被 `except` 吞掉后**过期过滤整体失效**，任意年代旧文都能进本周周报 |
| 报告内按 `raw_score` 降序 | 截顶分大量并列 100，无法分辨"哪条更重要" |
| `min_strong_for_accept` **默认 0（关闭）** | 实测取 2 时，「Citrix 警告 NetScaler 漏洞正被在野利用」这类只命中 1 个强词的安全要闻会被降级——词表以 AI 话题词为主，`vulnerability`/`exploit`/`ransomware` 等事件词多在中词层 |
| 翻译范围默认只有 `title` + `ai_summary` | 需求明确"只翻标题和摘要，正文不翻译"；范围在 `settings.json` 的 `translate.fields`，**改配置而不是改代码** |
| 摘要必须先洗噪声、再按**连续窗口**摘取、按**句边界**收尾 | 改前实测（142 条真实产物）：74.6% 的摘要是半句话、25.4% 开头混着导航/署名/分享按钮、181 处英文句子粘连（`accessed.The`）、49.3% 与原文开头逐字相同 |
| 摘要长度走 `settings.json` 的 `summary` 段（含分类分档），不写死在代码里 | 改前 72.5% 的摘要正好卡在写死的 500 字上限——长度由上限而不是内容决定。政策/产业类（④⑤）默认 300 字 |
| 每条摘要写 `ai_summary_kind`，报告显示「自动提炼 / 原文节选」 | 兜底截断与正常提炼此前无法区分，读者会把"原文开头几句"当成摘要 |
| 报告端剥掉"正文以摘要开头"的那一段 | 改前 39.4% 的收录条目在同一屏把同一段话显示两遍（摘要栏 + 正文栏）；摘要被翻译成中文时与原文对不上，自然不会误剥 |
| 后台静态文件走**白名单** | 原先直接复用 `SimpleHTTPRequestHandler`，匿名即可下载 `.env`、全部配置与源码（已实测复现并修复） |
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
| 项目记忆文件是 `AGENTS.md` | 不再使用 Claude Code，故弃用 `CLAUDE.md`；`AGENTS.md` 是工具中立的 Agent 指令约定 |
| 正文提取**只在块级标签边界换行** | 原先用 `get_text(separator="\n")`，它在**每个文本节点**之间插换行，加粗/链接/行内代码把一句话切成十几段；报告正文按换行分段，读者看到的就是碎句 |
| 先定位正文容器、再删模板；删除时**跳过正文容器及其所有祖先** | 这是"模板选择器误伤正文"的根治手段。原先先删后认，规则命中正文祖先就整页删光（实测 Unit42 因 `[class*="sidebar"]` 命中 WordPress `<body class="...no-sidebar...">`，47,518 字 → 363 字） |
| 不删 `[class*="sidebar"]`、`[aria-hidden="true"]`、`textarea` | 三条都实测误伤过正文：`sidebar` 命中 WordPress 的 `no-sidebar`（整页删除）与正文壳；`aria-hidden` 命中轮播/选项卡的非当前面板（删掉 2,000 字客户证言）；`textarea` 是 Discuz 论坛（看雪）正文的真实存放位置 |
| 正文容器打分 = "文本量达最大值 90% 以上者里取 HTML 最紧凑的" | 原先取第一个 `<article>` 直接返回，多卡片页面会抓错；改成"文本量 × 密度²"又会偏爱又小又密的卡片（实测 219 字的 card-body 盖过 6,438 字的正文） |
| 模板选择器用**单遍遍历 + 属性判断**，不用逐个 `select` | 逐条 `select` 把整棵树扫 120 遍，实测占清洗总耗时 80%（27.6s/33s）；选择器列表仍是唯一事实来源，由 `_compile_selector_rules()` 导入时编译为集合/子串表 |
| 行级去噪分"任意长度"与"仅短行（≤60 字）"两档 | 长句里出现"搜索/评论/订阅"属正常内容。实测短行阈值放宽到 80 字会把正常内容句一起删掉（某页丢失率 13%→26%），只换来噪音行 358→311 |
| 行内连续重复短语要收敛（不只是整行去重） | 部分论坛把登录墙提示放在十几个行内 span 里重复，块级换行不拆开，等于一行重复十遍；按整行比对抓不到。探测用"≥6 字片段近距离再现"，判据是"连续重复 ≥3 次" |
| 抓取失败后的重试**只能用真正的 RSS 阅读器 UA**（`RSS_READER_UAS` 里不得出现浏览器 UA），并按顺序逐个尝试 | 部分站点封浏览器 UA、放行阅读器 UA（2026-10-09 实测 Aqua Security Blog、Cynet Blog、Jack Clark、Microsoft AI Blog、OWASP LLM Top 10、Qualys Blog）。该列表原先第 0 个误写成浏览器 UA：一旦再加第二个 UA 做轮换，约一半概率重试又取回浏览器 UA → 这 6 个信源整周丢失 |

---

## 6 已知取舍与无解项

- **语义类误收录无解**：如「Anthropic 发布 Claude Haiku 5.5」——正文确实讨论了渗透测试（命中 3 个强词），关键词方案拦不住，需语义判断。`llm_processor` 的 LLM 分支是预留位，主流程**从不调用**它，后台勾"启用 LLM"也不改变行为
- **长文天然占优**：正文越长命中关键词机会越多，与内容是否更重要无关。实测"全文抓取成功率 55%→90% 时收录数 88→109"。要消除需按长度归一化
- **`ssl_verify: false`**：`source_config.yaml` 中 Seebug 一项关闭了 TLS 校验（该站证书链问题），存在中间人篡改可能，运行时有告警；证书修好后应删除该行。另 2 个信源的订阅地址已失效（跳过校验后只解析出 0 条），因此**没有**为它们开 `ssl_verify: false`
- **词表分层是"反的"但不要急着改**：事件词在中词层、AI 话题词在强词层，看似倒置；实测纠正会**牺牲 6 条正当 AI 安全内容只换来移出 1 条产业新闻**——因为「① AI/LLM 安全」是本项目一等分类，其词汇本身就是 AI 话题词。要动必须重做回归
- **可减少产业新闻的实测改法（尚未实施）**：负向过滤补充"商业/产业动态"词（融资/估值/营收/上市/推出/收购/合并/厂商/市场报告/财报/定价/订阅），并把豁免条件由"存在任意强词"改为"存在**具体安全事件词**"。实测收录 109→106，移出 3 条产业内容、零误伤
- **抽取式摘要的天花板**：摘要永远是原文原句，"读懂后一句话概括"做不到；个别信源 RSS 正文本身混着无关内容，摘要只能跟着脏。真正改写需接入外部模型
- **历史遗留**：`config/keywords.json`、`docs/superpowers/plans/*` 与当前实现无关，不要据此改代码

---

## 7 改完怎么验证

```powershell
python -m py_compile (git ls-files '*.py')     # 语法
python app.py --run --skip-fetch                # 端到端（离线重跑）
python scripts/summary_quality_report.py        # 摘要体检
```

专项回归脚本位于 `Output/` 下（gitignored、不入库）：

| 脚本 | 覆盖 |
|---|---|
| `Output/修复审查问题/Temp/test_pipeline_e2e.py` | 合成信源跑步骤 2–10；失败时不出报告、无旧产物残留 |
| `Output/修复审查问题/Temp/test_config_server.py` | 后台白名单、认证与锁定、配置写入校验 |
| `Output/修复审查问题/Temp/test_scoring_dedup.py` | 阈值、多分类计分、阶段 1/2 口径、过期过滤、去重 |
| `Output/修复审查问题/Temp/test_link_fix.py` | 相对链接补全 |
| `Output/修复审查问题/Temp/test_translate_scope.py` | 翻译范围（只翻标题与摘要） |
| `Output/文章清洗优化/Temp/` | 文章清洗回归（缓存 / 对比 / 逐行 diff / 评分 A/B） |

**调文章清洗 ⚠️ 必做四步**：

1. `cache_html.py` 把真实页面缓存为**固定基准**（抓取波动会让跨轮对比不公平）
2. `compare.py` 看噪音行、行长、耗时
3. `diff2.py` **逐行核对"旧版有、新版没有"的内容**，确认没误伤正文
4. `scoring_regression.py` 做受控 A/B（同一份 HTML，唯一变量是清洗实现），确认**没有条目被移出「收录」**

**调摘要 ⚠️ 必做**：用同一份 `data/classified_items.json` 当评测集 → 跑 `summary_quality_report.py 改前.json 改后.json` → **逐条看变化**，确认没洗掉好内容、没把正文开头当摘要。

**调评分/词表 ⚠️ 必做**：用最近一次 `data/translated_items.json` 当评测集 → 对比改动前后收录条数与**被移出条目清单** → **逐条看清单**。任何"提高证据门槛"的改动都会误伤"标题简洁但确实是安全要闻"的条目，只看总数会被平均掉。

---

## 8 配置与环境变量

`.env`（gitignored，参考 `.env.example`）：

| 变量 | 用途 | 必需 |
|---|---|---|
| `TMT_SECRET_ID` / `TMT_SECRET_KEY` | 腾讯云翻译（兼容旧名 `TENCENT_SECRET_ID/KEY`） | 翻译步骤必需；缺失则跳过并如实标注 |
| `CONFIG_USERNAME` / `CONFIG_PASSWORD` | 管理后台 Basic Auth（留空则不启用） | 强烈建议设置 |
| `SCHOLAR_API_KEY` | Semantic Scholar（无 key 有频率限制） | 可选 |
| `GITHUB_TOKEN` | GitHub API（无 token 有频率限制） | 可选 |

`config/settings.json` 关键项：去重阈值/天数、翻译范围 `translate.fields`、摘要长度分档 `summary`、分类顺序。
**该文件不含任何密钥**，且后台保存时按白名单裁剪字段。

`config/scoring_keywords.json` 的 `thresholds`：`accept_threshold` 80 / `review_threshold` 50 / `stage1_drop_below` 30 / `min_strong_for_accept` 0。
字段级说明与示例见 `README.md`「配置参考」。

---

## 9 文档、记忆与产出

- **文档组织**：项目说明性文档统一放 `docs/`，`README.md` 作文档总索引；主文档只载概要与目录，细节拆章节小文件并互相链接
- **文档同步**：改功能必须同步对应文档，文档与行为不一致视为任务未完成
- **记忆写入路由**：
  - 所有工作区通用 → 全局正本 `AGENTS-GLOBAL.md`
  - 开发类通用 → 类模板 `D:\Qagent\assets\AGENTS-template\开发类\AGENTS.md`
  - 本仓库工程事实与决策 → 本文
  - 本机路径、部署凭据位置、会话踩坑 → `.qagent/MEMORY.md`（一行一条 `- <规则> @(日期)`，不入库）
  - 用户层偏好 → 网关全局工作区 `.qagent/MEMORY.md`
- **产出**：一次性任务交付物放工作区 `Output/`（gitignored）；长期文档放 `docs/`

---

## 10 深入文档

- [README.md](README.md) — 面向使用者的完整说明（功能、流水线、评分机制、配置参考、红线）
- [设计文档](docs/superpowers/specs/2026-06-22-security-weekly-report-design.md) — 分类体系、标签维度、布局规范
- [API 采集设计](docs/superpowers/specs/2026-06-30-api-collector-design.md) — API 采集通道设计

---

## 11 本文件的维护约定

- 本文件是**正本**，不要另建同义的项目记忆文件（历史上曾用 `CLAUDE.md` / `memory.md`，已废弃）
- 改了架构、阈值、决策或约束 → 同步改本文；**文档与行为不一致视为任务未完成**
- 新增"看起来奇怪"的写法时，在 §5 补一行说明原因，避免后来者改回去
- 不把内部主机地址、账号口令、凭据位置等敏感信息写进本文（仓库公开）
