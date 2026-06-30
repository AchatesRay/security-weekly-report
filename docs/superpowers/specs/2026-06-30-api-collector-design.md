# API 采集层 — 设计文档

日期: 2026-06-30
状态: 已审批

---

## 背景

现有采集层仅支持 RSS/Atom 和少量静态爬虫，信源以安全媒体和厂商博客为主，内容偏重漏洞通告和威胁情报。作为网络安全架构师追踪安全技术发展趋势（非漏洞信息），需要补充学术论文、标准框架、社区热点等深度结构化内容。

---

## 目标

在现有 fetcher 中新增 **API 采集通道**，与 RSS/Scraper 并列运行，覆盖四大内容域：

| 内容域 | API 源 | 认证 | 价值 |
|-------|--------|------|------|
| 学术论文 | arXiv API (cs.CR/cs.SE/cs.AI/cs.LG) | 无 | 最新安全研究论文，结构完整 |
| 学术论文 | Semantic Scholar API | 免费 API Key | 论文推荐、引文网络追踪 |
| 标准框架 | IETF Datatracker API | 无 | RFC/Draft 变更追踪 |
| 标准框架 | MITRE ATT&CK API | 无 | TTP 演化实时更新 |
| 社区热点 | GitHub API /search | 免费 Personal Token | 安全相关仓库趋势 |

---

## 架构

```
FetchController (fetch_all)
 ├─ type == "rss" → fetch_feed()       ← 现有
 ├─ type == "scraper" → fetch_feed()   ← 现有（走 RSS 通道）
 └─ type == "api"   → fetch_api()      ← 新增
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  _fetch_arxiv()    _fetch_semantic()    _fetch_github()
  _fetch_ietf()     _fetch_mitre_attack()
```

所有 API 源共用：
- 同一 `httpx.AsyncClient` 连接池
- 同一套健康追踪（`_update_health` / `_get_auto_disabled_sources`）
- 同一异常降级机制（连续失败 5 次自动禁用）

---

## 数据归一化

所有 API 结果归一化为统一结构，与 RSS 解析后的格式对齐：

```python
{
    "source_name": str,         # 信源名称
    "source_level": str,        # S/A/B/C
    "region": str,              # global/cn/us/...
    "language": str,            # en/zh/...
    "url": str,                 # 文章/条目链接
    "type": "api",              # 标记为 API 源
    "title": str,
    "summary": str,             # 摘要/描述（供下游评分使用）
    "published": str,           # ISO 时间
    "authors": list[str],       # API 特有字段
    "categories": list[str],    # API 特有字段，如 ["cs.CR", "cs.AI"]
    "error": str | None,
}
```

额外字段 `authors`/`categories` 作为展示增强，不干扰现有评分管线（已按 `title`+`summary` 评分）。

---

## source_config.yaml 扩展

所有 API 源新增字段 `api_platform`，用于分发到对应的处理函数。示例：

```yaml
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
  url: https://api.semanticscholar.org/graph/v1/paper/search?query=cybersecurity&limit=50
  type: api
  api_platform: semantic_scholar
  language: en
  source_level: S
  region: global
  enabled: true

- name: IETF Datatracker
  group: 标准框架
  url: https://datatracker.ietf.org/api/v1/doc/document/?document_group=draft-ietf&limit=50
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
  url: https://api.github.com/search/repositories?q=security&sort=stars&order=desc&per_page=30
  type: api
  api_platform: github
  language: en
  source_level: A
  region: global
  enabled: true
```

---

## 各 API 处理要点

| 平台 | 输入格式 | 关键处理 |
|------|---------|---------|
| arXiv | Atom XML | 解析 `<entry>` 获取 title/summary/published/authors/categories |
| Semantic Scholar | JSON | 解析 `data[].{title,url,publicationDate,authors,citationCount}` |
| IETF | JSON | 解析 `objects[].{title,replaces,ad,name,group,state}`；关注 draft/RFC 的 state 变化 |
| MITRE ATT&CK | STIX JSON | 解析 `objects[].{name,description,external_references,modified}` |
| GitHub | JSON | 解析 `items[].{full_name,html_url,description,topics,stargazers_count}` |

---

## 错误处理

- 沿用现有健康追踪机制（`_update_health`/`_get_auto_disabled_sources`）
- API 返回空/格式异常视为失败
- 认证失败（401/403）快速失败并记录，不重试
- 每个 API 源独立降级，不影响其他源

---

## pipeline 影响范围

| 步骤 | 影响 |
|------|------|
| fetcher | 新增 `fetch_api()` 及平台子函数；`fetch_all()` 按 type 分发 |
| parser | 不变（API 不经过 XML 解析） |
| deduplicator | 不变（API 项已结构化，直接进入去重） |
| keyword_filter/scorer | 不变 |
| llm_processor/translator | 不变（英文论文摘要按需翻译） |
| report_generator | 可选：在周报中标识 API 来源，增强可溯源 |
| source_config.yaml | 新增 5 个 API 信源条目 |
