# API 采集层实施计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 fetcher 中新增 API 采集通道，在 parser 中添加对应解析器，并在 source_config.yaml 中注册 5 个 API 信源（arXiv、Semantic Scholar、IETF、MITRE ATT&CK、GitHub），使系统能够采集学术论文、标准框架和社区热点类结构化内容。

**Architecture:** 在现有 `fetch_all()` 中按 `type` 分发——RSS/Scraper 走原有 `fetch_feed()`，API 走新增 `fetch_api()`。`fetch_api()` 按 `api_platform` 分发到各平台处理函数，执行 HTTP 请求并把原始响应体保存到 `xml_text` 字段（与现有数据模型兼容）。parser 端用 `API_PARSERS` 注册表按 `source_name` 分发给对应解析函数，提取结构化字段后加入统一 `parsed_items.json` 流。新增 API 源需要 API Key 的通过环境变量注入（`SCHOLAR_API_KEY` / `GITHUB_TOKEN`）。

**Tech Stack:** Python + httpx (async) + feedparser (arXiv)、json (内建)

---

## Chunk 1: Fetcher — 新增 API 采集通道

### Task 1.1: 在 fetcher.py 中新增 `fetch_api()` 函数族

**Files:**
- Modify: `pipeline/fetcher.py`

**分析：** 当前 `fetch_feed()` 对所有信源一视同仁执行 HTTP GET，把响应体写入 `xml_text`。API 源需要：
1. 认证头（GitHub Token、Semantic Scholar Key）
2. 平台特定的 URL 构造/参数
3. 将来可能有 rate limiting/分页

因此新增独立 `fetch_api()` 分支，与 RSS 分流，不破坏现有逻辑。

- [ ] **Step 1.1.1: 在 fetcher.py 顶部环境变量读取区添加 API Key 常量**

在 `MAX_CONSECUTIVE_FAILURES = 5` 之后添加：

```python
import os

# API Keys（从环境变量读取）
SCHOLAR_API_KEY = os.environ.get("SCHOLAR_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
```

- [ ] **Step 1.1.2: 添加 `fetch_api()` 主调度函数**

在 `fetch_feed()` 之后、`fetch_all()` 之前添加：

```python
async def fetch_api(client: httpx.AsyncClient, source: dict) -> dict:
    """抓取单个 API 信源，返回 {source_name, url, xml_text, error}"""
    name = source["name"]
    platform = source.get("api_platform", "")
    feed_url = source["url"]
    print(f"  [FETCH API] {name} <- {feed_url}")

    # 按 api_platform 分发
    platform_handlers = {
        "arxiv": _fetch_arxiv,
        "semantic_scholar": _fetch_semantic_scholar,
        "ietf": _fetch_ietf,
        "mitre_attack": _fetch_mitre_attack,
        "github": _fetch_github,
    }

    handler = platform_handlers.get(platform)
    if not handler:
        return {
            "source_name": name,
            "source_level": source["source_level"],
            "region": source["region"],
            "language": source["language"],
            "url": feed_url,
            "type": "api",
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": f"未知 api_platform: {platform}",
        }

    try:
        result = await handler(client, source)
        return result
    except Exception as e:
        print(f"  [FETCH API ERROR] {name}: {e}")
        return {
            "source_name": name,
            "source_level": source["source_level"],
            "region": source["region"],
            "language": source["language"],
            "url": feed_url,
            "type": "api",
            "xml_text": "",
            "fetch_time": datetime.now().isoformat(),
            "error": str(e),
        }
```

- [ ] **Step 1.1.3: 添加 `_fetch_arxiv()` 子函数**

```python
async def _fetch_arxiv(client: httpx.AsyncClient, source: dict) -> dict:
    """arXiv API: 按分类查询最新论文"""
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return {
        "source_name": source["name"],
        "source_level": source["source_level"],
        "region": source["region"],
        "language": source["language"],
        "url": url,
        "type": "api",
        "api_platform": "arxiv",
        "xml_text": resp.text,
        "fetch_time": datetime.now().isoformat(),
        "error": None,
    }
```

- [ ] **Step 1.1.4: 添加 `_fetch_semantic_scholar()` 子函数**

```python
async def _fetch_semantic_scholar(client: httpx.AsyncClient, source: dict) -> dict:
    """Semantic Scholar API: 搜索安全相关论文"""
    url = source["url"]
    headers = {}
    if SCHOLAR_API_KEY:
        headers["x-api-key"] = SCHOLAR_API_KEY
    resp = await client.get(url, timeout=30.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    return {
        "source_name": source["name"],
        "source_level": source["source_level"],
        "region": source["region"],
        "language": source["language"],
        "url": url,
        "type": "api",
        "api_platform": "semantic_scholar",
        "xml_text": resp.text,
        "fetch_time": datetime.now().isoformat(),
        "error": None,
    }
```

- [ ] **Step 1.1.5: 添加 `_fetch_ietf()` 子函数**

```python
async def _fetch_ietf(client: httpx.AsyncClient, source: dict) -> dict:
    """IETF Datatracker API: 获取最新 drafts"""
    url = source["url"]
    resp = await client.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return {
        "source_name": source["name"],
        "source_level": source["source_level"],
        "region": source["region"],
        "language": source["language"],
        "url": url,
        "type": "api",
        "api_platform": "ietf",
        "xml_text": resp.text,
        "fetch_time": datetime.now().isoformat(),
        "error": None,
    }
```

- [ ] **Step 1.1.6: 添加 `_fetch_mitre_attack()` 子函数**

```python
async def _fetch_mitre_attack(client: httpx.AsyncClient, source: dict) -> dict:
    """MITRE ATT&CK STIX: 从 GitHub 获取完整 ATT&CK 数据"""
    url = source["url"]
    resp = await client.get(url, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    return {
        "source_name": source["name"],
        "source_level": source["source_level"],
        "region": source["region"],
        "language": source["language"],
        "url": url,
        "type": "api",
        "api_platform": "mitre_attack",
        "xml_text": resp.text,
        "fetch_time": datetime.now().isoformat(),
        "error": None,
    }
```

- [ ] **Step 1.1.7: 添加 `_fetch_github()` 子函数**

```python
async def _fetch_github(client: httpx.AsyncClient, source: dict) -> dict:
    """GitHub API: 搜索安全相关热门仓库"""
    url = source["url"]
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    resp = await client.get(url, timeout=30.0, follow_redirects=True, headers=headers)
    resp.raise_for_status()
    return {
        "source_name": source["name"],
        "source_level": source["source_level"],
        "region": source["region"],
        "language": source["language"],
        "url": url,
        "type": "api",
        "api_platform": "github",
        "xml_text": resp.text,
        "fetch_time": datetime.now().isoformat(),
        "error": None,
    }
```

- [ ] **Step 1.1.8: 修改 `fetch_all()` 添加 `type: api` 分流**

在 `fetch_all()` 中，`tasks` 列表生成处将 API 源路由到 `fetch_api()`：

```python
    tasks = []
    for src in sources:
        if src.get("type") == "api":
            tasks.append(fetch_api(client, src))
        else:
            tasks.append(fetch_feed(client, src))
```

- [ ] **Step 1.1.9: 验证 fetcher 改动不破坏现有逻辑**

运行：`python -c "from pipeline.fetcher import fetch_all; import asyncio; asyncio.run(fetch_all())"`

预期：现有 RSS 信源正常抓取，API 信源也发起请求（可能因 Key 缺失而报错但不影响整体流程）。

---

## Chunk 2: Parser — 新增 API 解析器

### Task 2.1: 在 parser.py 中注册 5 个新 API 解析器

**Files:**
- Modify: `pipeline/parser.py`

**分析：** 当前 `parser.py` 已有 `API_PARSERS` 注册表和 `parse_api_secrss()` 示例。新增解析函数遵循相同模式：从 `api_platform` 或 `source_name` 分发，解析原始 JSON/XML 为统一结构。

- [ ] **Step 2.1.1: 添加 `parse_api_arxiv()` 解析函数**

```python
def parse_api_arxiv(source_info: dict, raw_text: str) -> list[dict]:
    """解析 arXiv API 返回的 Atom XML 为统一格式"""
    import feedparser
    feed = feedparser.parse(raw_text)
    items = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        if not title:
            continue
        link = entry.get("link", "").strip()
        summary = entry.get("summary", "").strip()
        published = entry.get("published", "")
        authors = [a.get("name", "") for a in entry.get("authors", []) if a.get("name")]
        categories = [c.get("term", "") for c in entry.get("tags", []) if c.get("term")]
        items.append({
            "title": title,
            "url": link,
            "summary": summary,
            "published_date": published,
            "source_name": source_info["source_name"],
            "source_level": source_info["source_level"],
            "region": source_info["region"],
            "language": source_info["language"],
            "source_type": "API",
            "authors": authors,
            "categories": categories,
            "parse_time": datetime.now().isoformat(),
        })
    return items
```

- [ ] **Step 2.1.2: 添加 `parse_api_semantic_scholar()` 解析函数**

```python
def parse_api_semantic_scholar(source_info: dict, raw_text: str) -> list[dict]:
    """解析 Semantic Scholar API 返回的 JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    for paper in data.get("data", []):
        title = paper.get("title", "").strip()
        if not title:
            continue
        link = paper.get("url", "")
        summary = paper.get("abstract", "") or paper.get("title", "")
        published = paper.get("publicationDate", "") or ""
        authors = [a.get("name", "") for a in paper.get("authors", []) if a.get("name")]
        items.append({
            "title": title,
            "url": link,
            "summary": summary,
            "published_date": published,
            "source_name": source_info["source_name"],
            "source_level": source_info["source_level"],
            "region": source_info["region"],
            "language": source_info["language"],
            "source_type": "API",
            "authors": authors,
            "categories": [],
            "parse_time": datetime.now().isoformat(),
        })
    return items
```

- [ ] **Step 2.1.3: 添加 `parse_api_ietf()` 解析函数**

```python
def parse_api_ietf(source_info: dict, raw_text: str) -> list[dict]:
    """解析 IETF Datatracker API 返回的 JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    for doc in data.get("objects", []):
        title = doc.get("title", "").strip()
        if not title:
            continue
        name = doc.get("name", "")
        link = f"https://datatracker.ietf.org/doc/{name}/" if name else ""
        summary_parts = [f"状态: {doc.get('state', '未知')}"]
        if doc.get("intended_std_level"):
            summary_parts.append(f"标准级别: {doc['intended_std_level']}")
        if doc.get("group"):
            summary_parts.append(f"工作组: {doc['group']}")
        summary = " | ".join(summary_parts)
        published = doc.get("time", "") or ""
        items.append({
            "title": f"{name} - {title}" if name else title,
            "url": link,
            "summary": summary,
            "published_date": published,
            "source_name": source_info["source_name"],
            "source_level": source_info["source_level"],
            "region": source_info["region"],
            "language": source_info["language"],
            "source_type": "API",
            "authors": [],
            "categories": ["IETF", "标准草案"],
            "parse_time": datetime.now().isoformat(),
        })
    return items
```

- [ ] **Step 2.1.4: 添加 `parse_api_mitre_attack()` 解析函数**

```python
def parse_api_mitre_attack(source_info: dict, raw_text: str) -> list[dict]:
    """解析 MITRE ATT&CK STIX JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    seen_names = set()
    for obj in data.get("objects", []):
        if obj.get("type") not in ("attack-pattern", "malware", "tool", "intrusion-set", "campaign"):
            continue
        name = obj.get("name", "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        description = obj.get("description", "")
        summary = description[:500] if description else name
        ext_refs = obj.get("external_references", [])
        link = ""
        for ref in ext_refs:
            if ref.get("source_name") == "mitre-attack" and ref.get("url"):
                link = ref["url"]
                break
        modified = obj.get("modified", "")
        items.append({
            "title": f"[MITRE {obj['type']}] {name}",
            "url": link,
            "summary": summary,
            "published_date": modified,
            "source_name": source_info["source_name"],
            "source_level": source_info["source_level"],
            "region": source_info["region"],
            "language": source_info["language"],
            "source_type": "API",
            "authors": [],
            "categories": [obj.get("type", ""), "MITRE ATT&CK"],
            "parse_time": datetime.now().isoformat(),
        })
    return items
```

- [ ] **Step 2.1.5: 添加 `parse_api_github()` 解析函数**

```python
def parse_api_github(source_info: dict, raw_text: str) -> list[dict]:
    """解析 GitHub API 返回的仓库搜索 JSON"""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return []
    items = []
    for repo in data.get("items", []):
        full_name = repo.get("full_name", "").strip()
        if not full_name:
            continue
        description = repo.get("description", "") or ""
        topics = repo.get("topics", [])
        stars = repo.get("stargazers_count", 0)
        language = repo.get("language") or "未知"
        summary = description[:500] if description else full_name
        summary += f" | ⭐{stars} | 语言: {language}"
        if topics:
            summary += f" | 标签: {', '.join(topics[:5])}"
        items.append({
            "title": full_name,
            "url": repo.get("html_url", f"https://github.com/{full_name}"),
            "summary": summary,
            "published_date": repo.get("created_at", ""),
            "source_name": source_info["source_name"],
            "source_level": source_info["source_level"],
            "region": source_info["region"],
            "language": source_info["language"],
            "source_type": "API",
            "authors": [repo.get("owner", {}).get("login", "")] if repo.get("owner") else [],
            "categories": topics[:5],
            "parse_time": datetime.now().isoformat(),
        })
    return items
```

- [ ] **Step 2.1.6: 注册新解析器到 `API_PARSERS`**

将 `API_PARSERS` 字典更新为：

```python
API_PARSERS = {
    "安全内参": parse_api_secrss,
    "arXiv cs.CR": parse_api_arxiv,
    "Semantic Scholar": parse_api_semantic_scholar,
    "IETF Datatracker": parse_api_ietf,
    "MITRE ATT&CK": parse_api_mitre_attack,
    "GitHub Security Trending": parse_api_github,
}
```

- [ ] **Step 2.1.7: 验证解析逻辑**

运行：`python -c "from pipeline.parser import parse_all; items=parse_all(); api_items=[i for i in items if i.get('source_type')=='API']; print(f'API 条目数: {len(api_items)}')"`

预期：API 源条目被正确解析并纳入 `parsed_items.json`。

---

## Chunk 3: 配置与环境变量

### Task 3.1: 在 source_config.yaml 中注册 API 信源

**Files:**
- Modify: `config/source_config.yaml`

- [ ] **Step 3.1.1: 添加 arXiv cs.CR 条目**

在 `config/source_config.yaml` 末尾、`sources:` 列表最后添加以下 5 个条目：

```yaml
# ════════════════════════════════════════════
# API 采集层 — 技术趋势追踪（2026-06-30 新增）
# ════════════════════════════════════════════
- name: arXiv cs.CR
  group: 学术论文
  url: https://export.arxiv.org/api/query?search_query=cat:cs.CR&sortBy=submittedDate&sortOrder=descending&max_results=50
  type: api
  api_platform: arxiv
  language: en
  source_level: S
  region: global
  enabled: true

- name: Semantic Scholar
  group: 学术论文
  url: https://api.semanticscholar.org/graph/v1/paper/search?query=cybersecurity&limit=50&fields=title,url,abstract,publicationDate,authors
  type: api
  api_platform: semantic_scholar
  language: en
  source_level: S
  region: global
  enabled: true

- name: IETF Datatracker
  group: 标准框架
  url: https://datatracker.ietf.org/api/v1/doc/document/?document_group=draft-ietf&limit=50&order_by=-time&format=json
  type: api
  api_platform: ietf
  language: en
  source_level: S
  region: global
  enabled: true

- name: MITRE ATT&CK
  group: 标准框架
  url: https://raw.githubusercontent.com/mitre/cti/refs/heads/master/enterprise-attack/enterprise-attack.json
  type: api
  api_platform: mitre_attack
  language: en
  source_level: S
  region: global
  enabled: true

- name: GitHub Security Trending
  group: 开发者社区
  url: https://api.github.com/search/repositories?q=security+topic:security&sort=stars&order=desc&per_page=30
  type: api
  api_platform: github
  language: en
  source_level: A
  region: global
  enabled: true
```

- [ ] **Step 3.1.2: 为现有"安全内参"API 源补充 `api_platform` 字段**

找到 `安全内参` 条目，在其 `type: api` 行下方添加：

```yaml
  api_platform: secrss
```

### Task 3.2: 更新文档中的环境变量说明

- [ ] **Step 3.2.1: 更新 CLAUDE.md 环境变量表**

在 `CLAUDE.md` 的 `## 环境变量` 小节添加两行：

| `SCHOLAR_API_KEY` | Semantic Scholar API 密钥（可选，无 key 有频率限制） |
| `GITHUB_TOKEN` | GitHub Personal Access Token（可选，无 token 有频率限制） |

- [ ] **Step 3.2.2: 在 pipeline/__init__.py 中为 `api_platform` 字段添加导出（可选）**

无需改动——`__init__.py` 已统一导出 `fetch_all`，模块内部变化对外透明。

---

## Chunk 4: 集成验证

### Task 4.1: 端到端验证

- [ ] **Step 4.1.1: 验证配置加载**

运行：`python -c "import yaml; c=yaml.safe_load(open('config/source_config.yaml')); api=[s for s in c['sources'] if s.get('type')=='api']; print(f'API 信源数: {len(api)}'); [print(f'  - {s[\"name\"]} ({s.get(\"api_platform\",\"N/A\")})') for s in api]"`

预期：显示 6 个 API 信源（含原有的安全内参）。

- [ ] **Step 4.1.2: 完整抓取测试（跳过 RSS 只测 API）**

运行：`python -c "from pipeline.fetcher import fetch_all; import asyncio; asyncio.run(fetch_all())"`

预期：RSS 信源和 API 信源都被抓取，API 源的信息出现在 `data/raw_items.json` 中（无 key 的 Semantic Scholar 和 GitHub 可能报 403/429，但不影响其他源）。

- [ ] **Step 4.1.3: 完整管道测试**

运行：`python app.py --run`

预期：9 步管道全部完成，生成的 HTML 周报中包含来自 API 源的条目（标题前带论文/标准/仓库标识）。
