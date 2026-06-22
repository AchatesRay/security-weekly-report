# 网络安全周报系统 实现计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建模块化管道，从多信源 RSS 抓取网络安全内容，自动分类去重打标，按周生成 HTML 报告。

**Architecture:** 模块化管道架构，各步骤通过 JSON 文件传递数据。fetcher → parser → deduplicator → classifier → [预留 LLM] → report_generator，主入口 main.py 串联全部步骤。

**Tech Stack:** Python 3.10+, feedparser, httpx, PyYAML, Jinja2, rapidfuzz

---

## 文件结构

```
SecurityInfo/
├── main.py                     # 主入口
├── source_config.yaml          # 信源配置
├── classifier_rules.yaml       # 分类规则
├── llm_config.yaml             # LLM 配置(预留)
├── fetcher.py                  # RSS 抓取
├── parser.py                   # RSS 解析
├── deduplicator.py             # 去重
├── classifier.py               # 分类+打标
├── llm_processor.py            # LLM 摘要(预留空壳)
├── report_generator.py         # HTML 生成
├── templates/
│   └── weekly_report.html      # Jinja2 模板
├── data/
│   ├── raw_items.json
│   ├── parsed_items.json
│   ├── deduped_items.json
│   └── classified_items.json
└── reports/
    └── Security_Reports.html   # 最新周报(归档时重命名)
```

---

## Task 1: 项目骨架与配置文件

**Files:**
- Create: `source_config.yaml`
- Create: `classifier_rules.yaml`
- Create: `llm_config.yaml`
- Create: `requirements.txt`
- Create: `.gitignore`

- [ ] **Step 1: 创建 source_config.yaml**

写入包含原始信源清单的配置（新增补充信源后续添加），结构如下:

```yaml
sources:
  - name: BleepingComputer
    url: https://www.bleepingcomputer.com/feed/
    type: rss
    language: en
    source_level: B
    region: global
    enabled: true

  - name: The Hacker News
    url: https://thehackernews.com/feed/
    type: rss
    language: en
    source_level: B
    region: global
    enabled: true

  - name: Krebs on Security
    url: https://krebsonsecurity.com/feed/
    type: rss
    language: en
    source_level: A
    region: global
    enabled: true

  - name: Dark Reading
    url: https://www.darkreading.com/rss.xml
    type: rss
    language: en
    source_level: B
    region: global
    enabled: true

  - name: SecurityWeek
    url: https://www.securityweek.com/feed/
    type: rss
    language: en
    source_level: B
    region: global
    enabled: true

  - name: The Record / Recorded Future
    url: https://therecord.media/feed/
    type: rss
    language: en
    source_level: A
    region: global
    enabled: true

  - name: SC Media
    url: https://www.scmagazine.com/feed
    type: rss
    language: en
    source_level: B
    region: global
    enabled: true

  - name: FreeBuf
    url: https://www.freebuf.com/feed
    type: rss
    language: zh
    source_level: B
    region: cn
    enabled: true

  - name: 安全内参
    url: https://www.secrss.com/feed
    type: rss
    language: zh
    source_level: B
    region: cn
    enabled: true

  - name: 嘶吼 RoarTalk
    url: https://www.4hou.com/feed
    type: rss
    language: zh
    source_level: B
    region: cn
    enabled: true

  - name: OpenAI Blog
    url: https://openai.com/blog/feed.xml
    type: rss
    language: en
    source_level: S
    region: global
    enabled: true

  - name: Anthropic Research
    url: https://www.anthropic.com/research/feed.xml
    type: rss
    language: en
    source_level: S
    region: global
    enabled: true

  - name: Google AI Blog
    url: https://ai.googleblog.com/feeds/posts/default
    type: rss
    language: en
    source_level: S
    region: global
    enabled: true

  - name: Microsoft AI Blog
    url: https://blogs.microsoft.com/ai/feed/
    type: rss
    language: en
    source_level: S
    region: global
    enabled: true

  - name: Palo Alto Networks Blog
    url: https://www.paloaltonetworks.com/blog/feed/
    type: rss
    language: en
    source_level: S
    region: global
    enabled: true

  - name: Unit 42
    url: https://unit42.paloaltonetworks.com/feed/
    type: rss
    language: en
    source_level: A
    region: global
    enabled: true

  - name: CrowdStrike Blog
    url: https://www.crowdstrike.com/blog/feed/
    type: rss
    language: en
    source_level: A
    region: global
    enabled: true

  - name: Cisco Talos Blog
    url: https://blog.talosintelligence.com/feed/
    type: rss
    language: en
    source_level: A
    region: global
    enabled: true

  - name: Mandiant Blog
    url: https://www.mandiant.com/resources/blog/feed
    type: rss
    language: en
    source_level: A
    region: global
    enabled: true

  - name: NVD / CVE
    url: https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml
    type: rss
    language: en
    source_level: S
    region: global
    enabled: true

  - name: MSRC
    url: https://msrc.microsoft.com/update/feed
    type: rss
    language: en
    source_level: S
    region: global
    enabled: true

  - name: CISA
    url: https://www.cisa.gov/feeds/cybersecurity-advisories.xml
    type: rss
    language: en
    source_level: S
    region: us
    enabled: true

  - name: Exploit-DB
    url: https://www.exploit-db.com/rss.xml
    type: rss
    language: en
    source_level: B
    region: global
    enabled: true

  - name: Reddit r/netsec
    url: https://www.reddit.com/r/netsec/.rss
    type: rss
    language: en
    source_level: C
    region: global
    enabled: true

  - name: Hacker News
    url: https://hnrss.org/frontpage
    type: rss
    language: en
    source_level: C
    region: global
    enabled: true

  - name: GitHub Security Advisories
    url: https://github.com/advisories.rss
    type: rss
    language: en
    source_level: A
    region: global
    enabled: true
```

- [ ] **Step 2: 创建 classifier_rules.yaml**

```yaml
rules:
  - category: ① AI/LLM 安全
    keywords:
      - 大模型
      - LLM
      - GPT
      - Claude
      - OpenAI
      - Anthropic
      - prompt injection
      - prompt注入
      - AI安全
      - AI红队
      - model security
      - AI governance
      - AI供应链
      - RAG安全
      - fine-tuning security
      - AI agent
      - AI alignment
      - AI safety
      - AI red team
      - 大模型安全
      - 模型安全
      - 幻觉攻击
      - 越狱攻击
      - jailbreak
      - DeepSeek
      - moonshot
      - 智谱
      - 月之暗面
    tags: [AI]

  - category: ② 威胁情报与攻防对抗
    keywords:
      - APT
      - 高级持续性威胁
      - Lazarus
      - APT29
      - APT41
      - 定向攻击
      - targeted attack
      - 鱼叉攻击
      - spear-phishing
      - 攻击活动
      - threat actor
      - threat group
      - 威胁团伙
      - 新型攻击
      - TTP
      - 攻击手法
      - MITRE ATT&CK
      - 红队
      - red team
      - C2
      - 命令与控制
      - 后门
      - backdoor
      - webshell
      - 恶意软件
      - malware
      - ransomware
      - 勒索软件
      - trojan
      - 木马
      - botnet
      - 僵尸网络
      - loader
      - dropper
      - infostealer
      - 信息窃取
      - 攻击链
      - kill chain
      - 横向移动
      - lateral movement
      - 权限提升
      - privilege escalation
      - 初始访问
      - initial access
      - 数据泄露
      - data breach
      - data leak
      - 入侵
      - intrusion
      - compromise
      - 攻防
      - offensive
      - defense evasion
      - 免杀
      - bypass
      - rootkit
      - worm
      - 蠕虫
    tags: []

  - category: ③ 漏洞态势与供应链安全
    keywords:
      - CVE-
      - 0-day
      - 零日漏洞
      - 0day
      - RCE
      - 远程代码执行
      - PoC
      - 漏洞利用
      - exploit
      - 漏洞披露
      - vulnerability disclosure
      - 补丁
      - patch
      - 安全更新
      - security update
      - 供应链攻击
      - supply chain
      - 供应链安全
      - dependency confusion
      - 依赖混淆
      - 开源安全
      - OSS security
      - 软件供应链
      - software supply chain
      - SBOM
      - 漏洞库
      - advisory
      - 安全公告
      - 漏洞预警
      - exp
      - 漏洞复现
      - 漏洞分析
      - XSS
      - SQL注入
      - SQL injection
      - CSRF
      - SSRF
      - 缓冲区溢出
      - buffer overflow
      - 内存破坏
      - memory corruption
      - 提权
      - 代码执行
      - 拒绝服务
      - DoS
      - DDoS
    tags: []

  - category: ④ 政策法规与标准框架
    keywords:
      - 网络安全法
      - 数据安全法
      - 个人信息保护法
      - 关键信息基础设施
      - 等保
      - 等保2.0
      - 网信办
      - 工信部
      - 信安标委
      - TC260
      - 数据出境
      - 数据跨境
      - 合规
      - compliance
      - 监管
      - 法规
      - regulation
      - 法律
      - legislation
      - GDPR
      - PIPL
      - DSL
      - CCPA
      - NIST
      - ISO 27001
      - ISO 27000
      - ENISA
      - 认证
      - 安全评估
      - 网络安全审查
      - 处罚
      - 罚款
      - 行政处罚
      - 国家标准
      - 行业标准
    tags: []

  - category: ⑤ 产业动态与技术趋势
    keywords:
      - 安全运营
      - SOC
      - XDR
      - SIEM
      - SOAR
      - 威胁狩猎
      - threat hunting
      - 事件响应
      - incident response
      - 应急响应
      - 检测规则
      - Sigma
      - YARA
      - 云安全
      - cloud security
      - 容器安全
      - container security
      - K8s安全
      - Kubernetes
      - CSPM
      - CWPP
      - WAF
      - DDoS防护
      - 融资
      - 收购
      - M&A
      - 上市
      - IPO
      - 估值
      - 投资
      - 厂商
      - vendor
      - 收购
      - 合并
      - 市场报告
      - 行业报告
      - 技术趋势
      - 安全趋势
      - Gartner
      - Forrester
      - 研究报告
      - 白皮书
      - 产品发布
      - 新品上市
      - SentinelOne
      - CrowdStrike
      - Palo Alto
      - Fortinet
      - 奇安信
      - 深信服
      - 绿盟科技
      - 天融信
      - 360
      - 微步
      - 知道创宇
      - 长亭
      - 安恒
      - 火绒
      - 身份安全
      - identity security
      - IAM
      - 零信任
      - Zero Trust
      - SASE
      - SSE
      - FWaaS
      - EDR
      - EPP
      - NDR
      - MDR
      - 安全服务
      - 托管安全
      - M的安全
    tags: []

  - category: ⑥ 数据安全与隐私保护
    keywords:
      - 数据安全
      - data security
      - DLP
      - 数据防泄漏
      - 数据分类分级
      - 数据分级
      - 隐私保护
      - privacy
      - 个人信息
      - personal information
      - 数据加密
      - 加密技术
      - 后量子密码
      - post-quantum
      - PQC
      - 联邦学习
      - 同态加密
      - 差分隐私
      - 数据水印
      - 数据脱敏
      - 数据审计
      - 数据生命周期
      - 数据治理
      - data governance
      - 隐私计算
      - confidential computing
      - 机密计算
      - TEE
      - 密钥管理
      - key management
      - PKI
      - 证书
      - certificate
      - 数据泄露事件
      - 数据泄露
      - 数据暴露
      - data exposure
      - 隐私合规
      - 隐私政策
      - 知情同意
      - consent
      - PET
      - 隐私增强技术
    tags: []
```

- [ ] **Step 3: 创建 llm_config.yaml**

```yaml
# LLM 配置 — 预留功能
# 设置 enabled: true 并填入 API Key 后激活 AI 摘要生成

enabled: false
provider: openai          # openai / anthropic / ollama
api_key: ""
model: gpt-4o-mini
base_url: ""               # 兼容代理或本地部署
prompt_template: |
  请为以下网络安全资讯生成中文摘要，保留技术要点，不超过200字：

  标题: {title}
  原文: {content}
```

- [ ] **Step 4: 创建 requirements.txt**

```txt
feedparser>=6.0.0
httpx>=0.27.0
PyYAML>=6.0
Jinja2>=3.1.0
rapidfuzz>=3.0.0
```

- [ ] **Step 5: 创建 .gitignore**

```
__pycache__/
*.pyc
data/
reports/
.env
```

- [ ] **Step 6: 提交**

```bash
git add source_config.yaml classifier_rules.yaml llm_config.yaml requirements.txt .gitignore
git commit -m "feat: add project skeleton and configuration files"
```

---

## Task 2: RSS 抓取模块 (fetcher.py)

**Files:**
- Create: `fetcher.py`

- [ ] **Step 1: 实现 fetcher.py**

```python
import httpx
import yaml
import json
from pathlib import Path
from datetime import datetime
import asyncio
import traceback

CONFIG_PATH = Path("source_config.yaml")
DATA_DIR = Path("data")
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"

async def fetch_feed(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 RSS 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    feed_url = source["url"]
    print(f"  [FETCH] {name} <- {feed_url}")

    try:
        resp = await client.get(feed_url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        return {
            "source_name": name,
            "source_level": source["source_level"],
            "region": source["region"],
            "language": source["language"],
            "url": feed_url,
            "xml_text": resp.text,
            "fetch_time": datetime.now().isoformat(),
            "error": None,
        }
    except Exception as e:
        print(f"  [FETCH ERROR] {name}: {e}")
        return {
            "source_name": name,
            "source_level": source["source_level"],
            "region": source["region"],
            "language": source["language"],
            "url": feed_url,
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": str(e),
        }


async def fetch_all() -> list[dict]:
    """抓取所有启用的信源"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    sources = [s for s in config["sources"] if s.get("enabled", True)]
    print(f"[FETCHER] 开始抓取 {len(sources)} 个信源...")

    async with httpx.AsyncClient() as client:
        tasks = [fetch_feed(client, src) for src in sources]
        results = await asyncio.gather(*tasks)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    success = sum(1 for r in results if r["error"] is None)
    print(f"[FETCHER] 完成: {success}/{len(results)} 成功")
    return results


if __name__ == "__main__":
    asyncio.run(fetch_all())
```

- [ ] **Step 2: 验证 fetcher 可执行**

```bash
cd /root/Claude_Projects/SecurityInfo && python fetcher.py
```

预期: 输出各信源抓取状态，data/raw_items.json 生成。

- [ ] **Step 3: 提交**

```bash
git add fetcher.py
git commit -m "feat: add RSS fetcher module"
```

---

## Task 3: RSS 解析模块 (parser.py)

**Files:**
- Create: `parser.py`

- [ ] **Step 1: 实现 parser.py**

```python
import feedparser
import json
import traceback
from pathlib import Path
from datetime import datetime

DATA_DIR = Path("data")
RAW_ITEMS_PATH = DATA_DIR / "raw_items.json"
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"


def parse_entry(source_info: dict, entry) -> dict:
    """将 feedparser 的 entry 转换为统一结构"""
    title = entry.get("title", "").strip()
    link = entry.get("link", "").strip()
    summary = entry.get("summary", entry.get("description", "")).strip()
    published = entry.get("published_parsed", entry.get("updated_parsed", None))

    pub_date = ""
    if published:
        try:
            pub_date = datetime(*published[:6]).isoformat()
        except Exception:
            pub_date = ""

    return {
        "title": title,
        "url": link,
        "summary": summary,
        "published_date": pub_date,
        "source_name": source_info["source_name"],
        "source_level": source_info["source_level"],
        "region": source_info["region"],
        "language": source_info["language"],
        "parse_time": datetime.now().isoformat(),
    }


def parse_all() -> list[dict]:
    """解析所有已抓取的原始 RSS 数据"""
    with open(RAW_ITEMS_PATH, "r", encoding="utf-8") as f:
        raw_sources = json.load(f)

    all_items = []
    for source in raw_sources:
        if source["error"] or not source["xml_text"]:
            continue

        try:
            feed = feedparser.parse(source["xml_text"])
            for entry in feed.entries:
                try:
                    item = parse_entry(source, entry)
                    all_items.append(item)
                except Exception as e:
                    print(f"  [PARSE ERROR] {source['source_name']}: {e}")
                    all_items.append({
                        "title": entry.get("title", "").strip(),
                        "url": entry.get("link", "").strip(),
                        "summary": "",
                        "published_date": "",
                        "source_name": source["source_name"],
                        "source_level": source["source_level"],
                        "region": source["region"],
                        "language": source["language"],
                        "parse_time": datetime.now().isoformat(),
                        "parse_error": True,
                    })
        except Exception as e:
            print(f"  [PARSE ERROR] {source['source_name']} feed: {e}")

    print(f"[PARSER] 解析完成: {len(all_items)} 条")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(PARSED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_items, f, ensure_ascii=False, indent=2)

    return all_items


if __name__ == "__main__":
    parse_all()
```

- [ ] **Step 2: 验证 parser 可执行**

```bash
cd /root/Claude_Projects/SecurityInfo && python parser.py
```

预期: data/parsed_items.json 生成，包含统一结构的数据。

- [ ] **Step 3: 提交**

```bash
git add parser.py
git commit -m "feat: add RSS parser module"
```

---

## Task 4: 去重模块 (deduplicator.py)

**Files:**
- Create: `deduplicator.py`

- [ ] **Step 1: 实现 deduplicator.py**

```python
import json
from pathlib import Path
from rapidfuzz import fuzz

DATA_DIR = Path("data")
PARSED_ITEMS_PATH = DATA_DIR / "parsed_items.json"
DEDUPED_ITEMS_PATH = DATA_DIR / "deduped_items.json"

SIMILARITY_THRESHOLD = 85  # 标题相似度阈值


def normalize_title(title: str) -> str:
    """规范化标题用于相似度比较"""
    title = title.lower().strip()
    # 移除常见前后缀
    prefixes = [
        "cve alert: ", "alert: ", "news: ", "update: ",
        "critical: ", "high: ", "medium: ", "low: ",
    ]
    for p in prefixes:
        if title.startswith(p):
            title = title[len(p):]
            break
    return title.strip()


def deduplicate(items: list[dict]) -> list[dict]:
    """
    URL 精确去重 + 标题相似度模糊去重。
    合并同类项时保留多信源信息，标记为 merged_source。
    """
    seen_urls: set[str] = set()
    seen_titles: list[tuple[str, dict]] = []
    result = []

    for item in items:
        url = item.get("url", "")
        title = item.get("title", "")

        # URL 精确去重
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)

        # 标题相似度去重
        norm_title = normalize_title(title)
        is_dup = False
        for existing_title, existing_item in seen_titles:
            score = fuzz.token_sort_ratio(norm_title, existing_title)
            if score >= SIMILARITY_THRESHOLD:
                # 合并信源: 记录来自多个信源
                if "merged_sources" not in existing_item:
                    existing_item["merged_sources"] = [existing_item["source_name"]]
                if item["source_name"] not in existing_item["merged_sources"]:
                    existing_item["merged_sources"].append(item["source_name"])
                is_dup = True
                break

        if not is_dup:
            seen_titles.append((norm_title, item))
            result.append(item)

    print(f"[DEDUP] 去重: {len(items)} -> {len(result)} 条")
    return result


def run():
    with open(PARSED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    result = deduplicate(items)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEDUPED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: 验证去重**

```bash
cd /root/Claude_Projects/SecurityInfo && python deduplicator.py
```

预期: data/deduped_items.json 生成，条目数比 parsed_items 少。

- [ ] **Step 3: 提交**

```bash
git add deduplicator.py
git commit -m "feat: add deduplicator module"
```

---

## Task 5: 分类与打标模块 (classifier.py)

**Files:**
- Create: `classifier.py`

- [ ] **Step 1: 实现 classifier.py**

```python
import json
import re
import yaml
from pathlib import Path

DATA_DIR = Path("data")
RULES_PATH = Path("classifier_rules.yaml")
DEDUPED_ITEMS_PATH = DATA_DIR / "deduped_items.json"
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"


def load_rules() -> dict:
    """加载分类规则"""
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def match_keywords(text: str, keywords: list[str]) -> int:
    """计算文本中命中的关键词数量"""
    text_lower = text.lower()
    count = 0
    for kw in keywords:
        if kw.lower() in text_lower:
            count += 1
    return count


def classify_item(item: dict, rules: list[dict]) -> dict:
    """
    基于关键词匹配对内容进行分类和打标。
    分类规则: 取命中关键词最多的规则对应分类。
    无匹配时归入"未分类"。
    """
    text = f"{item.get('title', '')} {item.get('summary', '')}"

    best_category = "未分类"
    best_tags = []
    best_count = 0

    for rule in rules:
        count = match_keywords(text, rule["keywords"])
        if count > best_count:
            best_count = count
            best_category = rule["category"]
            best_tags = rule.get("tags", [])

    item["category"] = best_category
    item["tags"] = best_tags

    # 初步紧迫性推断 (后续可细化)
    urgency_keywords = {
        "需立即响应": [
            "在野利用", "已在野", "活跃利用", "emergency", "urgent",
            "actively exploited", "0-day", "zero-day",
        ],
        "近期需关注": [
            "CVE-", "补丁", "patch available", "披露", "disclosure",
            "更新", "update", "advisory", "安全公告",
        ],
        "持续关注": [
            "研究", "分析", "报告", "report", "trend", "趋势",
            "survey", "调查", "roadmap",
        ],
    }

    text_lower = text.lower()
    assigned = False
    for urgency, kws in urgency_keywords.items():
        if any(kw.lower() in text_lower for kw in kws):
            item["urgency"] = urgency
            assigned = True
            break
    if not assigned:
        item["urgency"] = "知识积累"

    # 内容类型推断
    content_type_map = {
        "研究报告/白皮书": ["白皮书", "研究报告", "whitepaper", "white paper",
                           "研究", "research paper", "技术报告"],
        "漏洞披露": ["CVE-", "漏洞披露", "vulnerability disclosure", "0-day",
                     "advisory", "安全公告", "漏洞预警"],
        "攻击活动报告": ["APT", "攻击活动", "threat actor", "threat group",
                        "入侵", "intrusion", "campaign", "攻击链"],
        "工具发布": ["工具", "tool", "发布", "release", "开源项目"],
        "行业分析": ["市场", "market", "报告", "analysis", "趋势", "趋势",
                     "Gartner", "Forrester", "行业"],
        "法规/标准发布": ["法规", "regulation", "标准", "standard", "法律",
                         "法案", "合规", "compliance", "NIST", "ISO"],
    }

    for content_type, kws in content_type_map.items():
        if any(kw.lower() in text_lower for kw in kws):
            item["content_type"] = content_type
            break
    else:
        item["content_type"] = "综合"

    # 地域推断: 在文本中检测特定国家/地区关键词
    region_map = {
        "cn": ["中国", "国家网信办", "工信部", "cncert", "中国信通院",
               "全国信安标委", "公安三所", "等保", "网信办"],
        "us": ["美国", "CISA", "FBI", "NSA", "白宫", "Biden", "Trump",
               "美国政府", "US government"],
        "eu": ["欧盟", "ENISA", "GDPR", "欧洲", "EU", "European Union"],
    }
    for region, kws in region_map.items():
        if any(kw.lower() in text_lower for kw in kws):
            if item.get("region") != region:
                # 仅在检测到特定地域关键词时覆盖（源级默认为 global）
                item["region"] = region
            break

    return item


def classify_all() -> list[dict]:
    """对所有去重后的内容进行分类"""
    rules = load_rules()["rules"]
    with open(DEDUPED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    classified = [classify_item(item, rules) for item in items]

    # 统计
    categories = {}
    for item in classified:
        cat = item["category"]
        categories[cat] = categories.get(cat, 0) + 1
    print(f"[CLASSIFIER] 分类完成: {len(classified)} 条")
    for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CLASSIFIED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)

    return classified


if __name__ == "__main__":
    classify_all()
```

- [ ] **Step 2: 验证分类**

```bash
cd /root/Claude_Projects/SecurityInfo && python classifier.py
```

预期: data/classified_items.json 生成，每项含 category/urgency/content_type/region/tags 字段。

- [ ] **Step 3: 提交**

```bash
git add classifier.py
git commit -m "feat: add classifier module with keyword-based classification and tagging"
```

---

## Task 6: LLM 预留空壳 (llm_processor.py)

**Files:**
- Create: `llm_processor.py`

- [ ] **Step 1: 实现 llm_processor.py**

```python
import json
import yaml
from pathlib import Path

DATA_DIR = Path("data")
CONFIG_PATH = Path("llm_config.yaml")
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
ENHANCED_ITEMS_PATH = DATA_DIR / "enhanced_items.json"


def process(items: list[dict], config: dict) -> list[dict]:
    """
    LLM 摘要模块（预留空壳）。
    当 config.enabled = true 时，调用指定 LLM 为每条内容生成摘要。
    当前阶段：直接返回原数据，不做处理。
    """
    if config.get("enabled", False):
        print("[LLM] LLM 功能已启用但尚未实现 — 直接透传数据")
    else:
        print("[LLM] LLM 功能未启用（默认），透传数据")

    return items


def run():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    with open(CLASSIFIED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    result = process(items, config)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ENHANCED_ITEMS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: 提交**

```bash
git add llm_processor.py
git commit -m "feat: add LLM processor stub (placeholder)"
```

---

## Task 7: HTML 周报生成模块 (report_generator.py + template)

**Files:**
- Create: `report_generator.py`
- Create: `templates/weekly_report.html`

- [ ] **Step 1: 实现 report_generator.py**

```python
import json
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from jinja2 import Environment, FileSystemLoader

DATA_DIR = Path("data")
REPORTS_DIR = Path("reports")
TEMPLATES_DIR = Path("templates")
CLASSIFIED_ITEMS_PATH = DATA_DIR / "classified_items.json"
LATEST_REPORT = REPORTS_DIR / "Security_Reports.html"

CATEGORY_ORDER = [
    "① AI/LLM 安全",
    "② 威胁情报与攻防对抗",
    "③ 漏洞态势与供应链安全",
    "④ 政策法规与标准框架",
    "⑤ 产业动态与技术趋势",
    "⑥ 数据安全与隐私保护",
    "未分类",
]

# CSS 颜色
URGENCY_COLORS = {
    "需立即响应": "#dc3545",
    "近期需关注": "#ffc107",
    "持续关注": "#0d6efd",
    "知识积累": "#6c757d",
}


def get_week_number(dt: datetime) -> str:
    """返回 ISO 周号字符串，如 2026W26"""
    iso = dt.isocalendar()
    return f"{iso[0]}W{iso[1]:02d}"


def group_by_category(items: list[dict]) -> dict:
    """按分类对内容分组"""
    groups = {cat: [] for cat in CATEGORY_ORDER}
    for item in items:
        cat = item.get("category", "未分类")
        if cat not in groups:
            cat = "未分类"
        groups[cat].append(item)
    # 移除空组
    return {k: v for k, v in groups.items() if v}


def get_executive_summary(items: list[dict]) -> list[dict]:
    """获取架构师摘要：紧迫性为 🚨 和 ⏰ 的内容"""
    urgent = [i for i in items if i.get("urgency") == "需立即响应"]
    watch = [i for i in items if i.get("urgency") == "近期需关注"]
    return urgent, watch


def archive_previous_report():
    """将现有 Security_Reports.html 重命名为带周号的归档文件"""
    if LATEST_REPORT.exists():
        # 读取旧文件 mtime 所在周
        mtime = datetime.fromtimestamp(LATEST_REPORT.stat().st_mtime)
        week_str = get_week_number(mtime)
        archive_name = REPORTS_DIR / f"Security_Reports_{week_str}.html"
        shutil.move(str(LATEST_REPORT), str(archive_name))
        print(f"[REPORT] 归档旧报告: {archive_name.name}")


def generate_report():
    with open(CLASSIFIED_ITEMS_PATH, "r", encoding="utf-8") as f:
        items = json.load(f)

    now = datetime.now()
    week_str = get_week_number(now)

    # 计算本周起始和结束日期（周一 ~ 周日）
    monday = now - timedelta(days=now.weekday())
    sunday = monday + timedelta(days=6)
    date_range = f"{monday.strftime('%Y.%m.%d')}-{sunday.strftime('%Y.%m.%d')}"

    groups = group_by_category(items)
    urgent_items, watch_items = get_executive_summary(items)

    # 统计
    total_count = len(items)
    urgent_count = len(urgent_items)
    watch_count = len(watch_items)

    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    template = env.get_template("weekly_report.html")

    html = template.render(
        week_str=week_str,
        date_range=date_range,
        generate_time=now.strftime("%Y-%m-%d %H:%M"),
        total_count=total_count,
        urgent_count=urgent_count,
        watch_count=watch_count,
        urgent_items=urgent_items,
        watch_items=watch_items,
        groups=groups,
        urgency_colors=URGENCY_COLORS,
    )

    # 归档旧报告
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    archive_previous_report()

    # 写新报告
    with open(LATEST_REPORT, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[REPORT] 周报生成完成: {LATEST_REPORT}")
    print(f"[REPORT] 共 {total_count} 条, 🚨 {urgent_count} 条, ⏰ {watch_count} 条")
    return str(LATEST_REPORT)


if __name__ == "__main__":
    generate_report()
```

- [ ] **Step 2: 实现 Jinja2 模板**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>网络安全周报 {{ week_str }}</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            background: #f5f6fa; color: #2c3e50; line-height: 1.6;
            padding: 0;
        }
        .container { max-width: 960px; margin: 0 auto; padding: 20px; }

        /* 页头 */
        .header {
            background: linear-gradient(135deg, #1a237e, #283593);
            color: white; padding: 32px 40px; border-radius: 12px; margin-bottom: 24px;
        }
        .header h1 { font-size: 24px; margin-bottom: 8px; }
        .header .meta { font-size: 14px; opacity: 0.85; }
        .header .stats { margin-top: 12px; font-size: 14px; }
        .header .stats span { display: inline-block; margin-right: 16px; }
        .badge-urgent { background: #dc3545; color: white; padding: 2px 8px; border-radius: 4px; font-size: 12px; }
        .badge-watch { background: #ffc107; color: #333; padding: 2px 8px; border-radius: 4px; font-size: 12px; }

        /* 架构师摘要 */
        .executive-summary {
            background: white; border-radius: 12px; padding: 24px; margin-bottom: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }
        .executive-summary h2 { font-size: 18px; margin-bottom: 16px; color: #1a237e; }
        .es-item {
            padding: 10px 16px; margin-bottom: 8px; border-radius: 8px;
            border-left: 4px solid #ddd; font-size: 14px;
        }
        .es-item.urgent { border-left-color: #dc3545; background: #fff5f5; }
        .es-item.watch { border-left-color: #ffc107; background: #fffef5; }
        .es-item .es-title { font-weight: 600; }
        .es-item .es-meta { font-size: 12px; color: #666; margin-top: 4px; }

        /* 板块 */
        .section {
            background: white; border-radius: 12px; margin-bottom: 16px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow: hidden;
        }
        .section-header {
            padding: 16px 24px; cursor: pointer; user-select: none;
            display: flex; justify-content: space-between; align-items: center;
            border-bottom: 1px solid #eee;
        }
        .section-header:hover { background: #f8f9fa; }
        .section-header h3 { font-size: 16px; }
        .section-header .count { font-size: 13px; color: #666; }
        .section-content { padding: 16px 24px; }

        /* 内容卡片 */
        .item-card {
            padding: 14px 16px; margin-bottom: 12px; border-radius: 8px;
            border-left: 4px solid #ddd; background: #fafafa;
            transition: background 0.2s;
        }
        .item-card:hover { background: #f0f0f0; }
        .item-card.urgency-urgent { border-left-color: #dc3545; }
        .item-card.urgency-watch { border-left-color: #ffc107; }
        .item-card.urgency-later { border-left-color: #0d6efd; }
        .item-card.urgency-learn { border-left-color: #6c757d; }

        .item-title { font-size: 15px; font-weight: 600; margin-bottom: 6px; }
        .item-title a { color: #1a237e; text-decoration: none; }
        .item-title a:hover { text-decoration: underline; }

        .item-summary { font-size: 14px; color: #444; margin-bottom: 8px; }
        .item-summary .more-link { font-size: 13px; color: #0d6efd; }

        .item-meta { font-size: 12px; color: #666; margin-bottom: 6px; }
        .item-meta .sep { margin: 0 6px; color: #ccc; }

        .item-tags { display: flex; flex-wrap: wrap; gap: 4px; }
        .tag {
            display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: 11px; background: #e9ecef; color: #495057;
        }
        .tag-source-s { background: #d4edda; color: #155724; }
        .tag-source-a { background: #cce5ff; color: #004085; }
        .tag-source-b { background: #fff3cd; color: #856404; }
        .tag-source-c { background: #f8d7da; color: #721c24; }
        .tag-content-type { background: #d1ecf1; color: #0c5460; }
        .tag-impact { background: #e8daef; color: #6c3483; }

        /* 页脚 */
        .footer {
            text-align: center; font-size: 12px; color: #999;
            padding: 24px; margin-top: 24px;
        }

        /* 折叠动画 */
        .section-content.collapsed { display: none; }
        .collapse-icon { font-size: 12px; transition: transform 0.2s; }
        .collapse-icon.collapsed { transform: rotate(-90deg); }
    </style>
</head>
<body>
    <div class="container">
        <!-- 页头 -->
        <div class="header">
            <h1>网络安全周报 — {{ week_str }}</h1>
            <div class="meta">{{ date_range }} ｜ 生成时间: {{ generate_time }}</div>
            <div class="stats">
                <span>共 <strong>{{ total_count }}</strong> 条</span>
                <span><span class="badge-urgent">🚨 {{ urgent_count }}</span></span>
                <span><span class="badge-watch">⏰ {{ watch_count }}</span></span>
            </div>
        </div>

        <!-- 架构师摘要 -->
        {% if urgent_items or watch_items %}
        <div class="executive-summary">
            <h2>📋 架构师摘要</h2>

            {% if urgent_items %}
            <h4 style="margin-bottom: 10px; color: #dc3545;">🚨 需立即响应</h4>
            {% for item in urgent_items %}
            <div class="es-item urgent">
                <div class="es-title">{{ item.title }}</div>
                <div class="es-meta">
                    {{ item.source_name }}
                    {% if item.merged_sources %} (联合报道: {{ item.merged_sources | join(", ") }}){% endif %}
                    {% if item.region %} ｜ 地域: {{ item.region }}{% endif %}
                </div>
            </div>
            {% endfor %}
            {% endif %}

            {% if watch_items %}
            <h4 style="margin-bottom: 10px; margin-top: 16px; color: #856404;">⏰ 近期需关注</h4>
            {% for item in watch_items %}
            <div class="es-item watch">
                <div class="es-title">{{ item.title }}</div>
                <div class="es-meta">
                    {{ item.source_name }}
                    {% if item.merged_sources %} (联合报道: {{ item.merged_sources | join(", ") }}){% endif %}
                    {% if item.region %} ｜ 地域: {{ item.region }}{% endif %}
                </div>
            </div>
            {% endfor %}
            {% endif %}
        </div>
        {% endif %}

        <!-- 六大板块 -->
        {% for category, items in groups.items() %}
        <div class="section">
            <div class="section-header" onclick="toggleSection(this)">
                <h3>{{ category }}</h3>
                <span class="count">{{ items | length }} 条 <span class="collapse-icon">▼</span></span>
            </div>
            <div class="section-content">
                {% for item in items %}
                <div class="item-card urgency-{{ {'需立即响应': 'urgent', '近期需关注': 'watch', '持续关注': 'later', '知识积累': 'learn'}.get(item.urgency, 'learn') }}">
                    <div class="item-title">
                        <a href="{{ item.url }}" target="_blank" rel="noopener">{{ item.title }}</a>
                    </div>
                    <div class="item-summary">
                        {{ item.summary | striptags | truncate(300) }}
                    </div>
                    <div class="item-meta">
                        <strong>{{ item.source_name }}</strong>
                        {% if item.merged_sources %}(联合报道: {{ item.merged_sources | join(", ") }}){% endif %}
                        <span class="sep">｜</span>
                        <span class="tag tag-source-{{ item.source_level | lower }}">{{ item.source_level }}</span>
                        {% if item.region %}<span class="sep">｜</span> 🌏 {{ item.region }}{% endif %}
                        {% if item.published_date %}<span class="sep">｜</span> {{ item.published_date[:10] }}{% endif %}
                    </div>
                    <div class="item-tags">
                        {% if item.urgency %}<span class="tag">{{ item.urgency }}</span>{% endif %}
                        {% if item.content_type %}<span class="tag tag-content-type">{{ item.content_type }}</span>{% endif %}
                        {% for tag in item.tags %}<span class="tag tag-impact">{{ tag }}</span>{% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}

        <!-- 页脚 -->
        <div class="footer">
            <p>本报告由网络安全周报系统自动生成 ｜ 信息仅供参考，请以原文为准</p>
        </div>
    </div>

    <script>
        function toggleSection(header) {
            const content = header.nextElementSibling;
            const icon = header.querySelector('.collapse-icon');
            content.classList.toggle('collapsed');
            icon.classList.toggle('collapsed');
        }
    </script>
</body>
</html>
```

- [ ] **Step 3: 验证报告生成**

```bash
cd /root/Claude_Projects/SecurityInfo && python report_generator.py
```

预期: reports/Security_Reports.html 生成，浏览器打开可正常渲染。

- [ ] **Step 4: 提交**

```bash
git add report_generator.py templates/weekly_report.html
git commit -m "feat: add HTML report generator with Jinja2 template"
```

---

## Task 8: 主入口 (main.py)

**Files:**
- Create: `main.py`

- [ ] **Step 1: 实现 main.py**

```python
#!/usr/bin/env python3
"""
网络安全周报系统 — 主入口

用法:
    python main.py --run          # 执行完整管道: 抓取→解析→去重→分类→生成周报
    python main.py --run --skip-fetch  # 跳过抓取，使用已有数据重新生成
"""

import argparse
import sys
import asyncio
from pathlib import Path
from datetime import datetime

# 确保 data/ 目录存在
DATA_DIR = Path("data")

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

def run_pipeline(skip_fetch: bool = False):
    """执行完整管道"""
    ensure_dirs()
    start = datetime.now()
    print(f"=== 网络安全周报系统 ===")
    print(f"开始时间: {start.isoformat()}")
    print()

    # Step 1: 抓取
    if skip_fetch:
        print("[SKIP] 跳过抓取阶段，使用已有数据")
    else:
        print("[1/5] 正在抓取 RSS 信源...")
        from fetcher import fetch_all
        asyncio.run(fetch_all())
        print()

    # Step 2: 解析
    print("[2/5] 正在解析 RSS 数据...")
    from parser import parse_all
    parse_all()
    print()

    # Step 3: 去重
    print("[3/5] 正在去重...")
    from deduplicator import run as run_dedup
    run_dedup()
    print()

    # Step 4: 分类
    print("[4/5] 正在分类与打标...")
    from classifier import classify_all
    classify_all()
    print()

    # Step 5: 生成报告
    print("[5/5] 正在生成 HTML 周报...")
    from report_generator import generate_report
    report_path = generate_report()
    print()

    elapsed = (datetime.now() - start).total_seconds()
    print(f"=== 完成! 耗时 {elapsed:.1f} 秒 ===")
    print(f"报告: {report_path}")


def main():
    parser = argparse.ArgumentParser(description="网络安全周报系统")
    parser.add_argument("--run", action="store_true", help="执行完整管道")
    parser.add_argument("--skip-fetch", action="store_true", help="跳过抓取阶段")
    args = parser.parse_args()

    if args.run:
        run_pipeline(skip_fetch=args.skip_fetch)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 测试主入口**

```bash
cd /root/Claude_Projects/SecurityInfo && python main.py --run --skip-fetch
```

预期: 管道从第2步开始执行，最终生成周报。

- [ ] **Step 3: 提交**

```bash
git add main.py
git commit -m "feat: add main pipeline entry point"
```

---

## 后续扩展

- 新增信源: 直接编辑 `source_config.yaml` 追加条目
- 调整分类规则: 编辑 `classifier_rules.yaml` 增减关键词
- 启用 LLM 摘要: 编辑 `llm_config.yaml` 设置 `enabled: true` 并填入 API Key 后，在管道中激活 `llm_processor.py`
- 新增补充信源（云安全、身份安全等）: 参考设计文档中信源清单，逐条追加到 `source_config.yaml`
