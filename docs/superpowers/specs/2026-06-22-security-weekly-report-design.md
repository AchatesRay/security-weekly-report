# 网络安全周报系统 — 设计文档

日期: 2026-06-22
状态: 已审批

---

## 项目概述

基于 Python 的网络安全信息聚合与周报生成系统。从多信源 RSS 抓取网络安全相关内容，自动分类、去重、打标，按周生成 HTML 格式的网络安全周报。

运行方式: 手动触发 (`python main.py --run`)，不回溯历史内容。

---

## 分类体系

共 6 大分类，周报按以下顺序展示:

| # | 分类 | 说明 |
|---|------|------|
| ① | AI/LLM 安全 | 大模型安全、AI 供应链、AI 红队、AI 治理 |
| ② | 威胁情报与攻防对抗 | APT 活动、TTP、恶意软件、红队工具技术 |
| ③ | 漏洞态势与供应链安全 | 重大 CVE/0-day、软件供应链攻击、开源安全 |
| ④ | 政策法规与标准框架 | 各国法规、NIST/ISO/等保、合规要求 |
| ⑤ | 产业动态与技术趋势 | 安全运营、云基础设施、厂商动态、市场趋势 |
| ⑥ | 数据安全与隐私保护 | DLP、数据跨境、隐私增强技术、加密技术 |

无二级分类，子领域通过标签表达。

---

## 跨维度标签系统

每条内容从 5 个维度打标:

| 维度 | 标签值 | 呈现 |
|------|--------|------|
| 紧迫性 | 🚨 需立即响应 / ⏰ 近期需关注 / 📌 持续关注 / 📖 知识积累 | 左侧色条 + 图标 |
| 信源等级 | S(官方安全响应中心) / A(顶级安全研究机构) / B(专业安全媒体) / C(社区论坛) | 灰色标签 |
| 内容类型 | 研究报告/白皮书 / 漏洞披露 / 攻击活动报告 / 工具发布 / 行业分析 / 法规/标准发布 | 蓝色标签 |
| 影响面 | AI / 云原生 / 基础设施 / 身份安全 / 数据安全 / 端点 / 网络 / 供应链 | 绿色标签 |
| 地域 | 🌏 全球 / 🇨🇳 中国 / 🇺🇸 美国 / 🇪🇺 欧洲 | 图标 |

---

## 信源清单

### 一、英文安全媒体

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| BleepingComputer | en | B | global |
| The Hacker News | en | B | global |
| Dark Reading | en | B | global |
| SecurityWeek | en | B | global |
| Krebs on Security | en | A | global |
| The Record / Recorded Future | en | A | global |
| SC Media | en | B | global |

### 二、中文安全媒体

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| FreeBuf | zh | B | cn |
| 安全内参 | zh | B | cn |
| 嘶吼 RoarTalk | zh | B | cn |
| 看雪学院 | zh | B | cn |

### 三、大模型公司官网

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| OpenAI Blog | en | S | global |
| OpenAI Safety | en | S | global |
| Anthropic Research | en | S | global |
| Anthropic Safety | en | S | global |
| Google AI Blog | en | S | global |
| Microsoft AI Blog | en | S | global |
| Meta AI Blog | en | S | global |
| xAI | en | S | global |
| DeepSeek | zh | S | cn |
| 智谱AI | zh | S | cn |
| 月之暗面 | zh | S | cn |

### 四、安全厂商官网

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| Palo Alto Networks Blog | en | S | global |
| Unit 42 Research | en | A | global |
| CrowdStrike Blog | en | A | global |
| Fortinet Blog | en | S | global |
| Check Point Research | en | A | global |
| Cisco Talos Blog | en | A | global |
| Mandiant Blog | en | A | global |
| SentinelOne Blog | en | B | global |
| Trend Micro Research | en | B | global |
| Kaspersky ICS CERT | en | A | global |
| 奇安信威胁情报中心 | zh | A | cn |
| 360 Netlab | zh | A | cn |
| 深信服安全研究 | zh | A | cn |
| 绿盟科技安全公告 | zh | A | cn |
| 天融信安全研究 | zh | A | cn |

### 五、安全论坛 / 社区

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| Reddit r/netsec | en | C | global |
| Reddit r/blueteamsec | en | C | global |
| Reddit r/Malware | en | C | global |
| Hacker News | en | C | global |
| Exploit-DB | en | B | global |
| Packet Storm Security | en | B | global |
| 先知安全社区 | zh | C | cn |
| 看雪论坛 | zh | C | cn |

### 六、漏洞平台 / CERT

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| NVD / CVE | en | S | global |
| GitHub Security Advisories | en | A | global |
| MSRC | en | S | global |
| Zero Day Initiative | en | A | global |
| CISA | en | S | us |
| CNNVD / CNCERT | zh | S | cn |

### 七、政策法规

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| 中国网信办 | zh | S | cn |
| 工信部 | zh | S | cn |
| ENISA | en | S | eu |
| NIST | en | S | us |

### 八、新增补充信源

#### 云与基础设施安全

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| AWS Security Blog | en | S | global |
| Azure Security Blog | en | S | global |
| GCP Security Blog | en | S | global |
| Wiz Blog | en | A | global |
| Aqua Security Blog | en | A | global |
| HashiCorp Security | en | A | global |

#### 开源与软件供应链安全

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| OpenSSF | en | A | global |
| GitHub Security Lab | en | A | global |
| Snyk Blog | en | A | global |
| Socket.dev Blog | en | A | global |

#### 身份安全

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| Okta Security | en | S | global |
| Microsoft Entra Blog | en | S | global |
| Beyond Identity Blog | en | B | global |

#### 中文安全厂商（补充）

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| 微步在线 ThreatBook | zh | A | cn |
| 知道创宇 | zh | A | cn |
| 安恒信息 | zh | A | cn |
| 火绒安全 | zh | B | cn |
| 长亭科技 | zh | A | cn |

#### 威胁情报与标准（专项）

| 信源 | 语言 | 等级 | 地域 |
|------|------|------|------|
| MITRE ATT&CK 更新 | en | A | global |
| OWASP | en | A | global |
| SANS ISC | en | B | global |
| AlienVault OTX | en | B | global |
| 中国信通院 CAICT | zh | S | cn |
| 全国信安标委 TC260 | zh | S | cn |
| 公安三所 | zh | S | cn |
| IAPP | en | A | global |

---

## 系统架构

模块化管道架构，数据在各模块间通过 JSON 文件传递:

```
source_config.yaml  +  classifier_rules.yaml
         │                      │
         ▼                      │
    fetcher.py                  │
    (RSS 抓取)                   │
         │                      │
         ▼                      │
    parser.py                   │
    (XML 解析 → 统一结构体)      │
         │                      │
         ▼                      │
    deduplicator.py              │
    (URL去重 + 标题相似度合并)    │
         │                      │
         ▼                      │
    classifier.py ──────────────┘
    (关键词规则分类 + 打标)
         │
         ▼
    [llm_processor.py]  ← 预留，默认关闭
    (LLM 深度摘要)
         │
         ▼
    report_generator.py
    (Jinja2 → HTML 周报)
```

### 模块职责

| 模块 | 输入 | 输出 | 职责 |
|------|------|------|------|
| fetcher.py | source_config.yaml | raw_items.json | 遍历信源拉取 RSS XML |
| parser.py | raw_items.json | parsed_items.json | XML 解析，统一字段结构 |
| deduplicator.py | parsed_items.json | deduped_items.json | URL 精确去重 + 标题相似度合并 |
| classifier.py | deduped_items.json + classifier_rules.yaml | classified_items.json | 关键词规则分类 + 多维打标 |
| llm_processor.py | classified_items.json | enhanced_items.json | (预留) LLM 摘要生成 |
| report_generator.py | classified_items.json + templates/ | HTML 报告 | 渲染周报 |

### 原则

- 任何步骤失败不阻断管道，跳过错误继续后续处理
- 单个信源 RSS 拉取失败不影响其他信源
- 解析异常条目标记 `parse_error: true` 保留原始数据
- 分类无匹配归入"未分类"，周报底部展示

---

## HTML 周报布局

### 页面结构 (从上到下)

1. **页头**: 周报标题、日期范围、本期统计摘要
2. **架构师摘要**: 标记 🚨 和 ⏰ 的内容置顶展示
3. **六大板块**: 按 ①→⑥ 顺序排列，每个板块可折叠
   - 板块标题含该板块本周条数统计
   - 每条内容含: 紧迫性色条 + 标题 + 信源等级标签 + 地域标签 + 完整摘要 + 标签组 + 原文链接
4. **页脚**: 信源统计、免责声明

### 紧迫性颜色

| 紧迫性 | 色条 |
|--------|------|
| 🚨 需立即响应 | 红色 |
| ⏰ 近期需关注 | 黄色 |
| 📌 持续关注 | 蓝色 |
| 📖 知识积累 | 灰色 |

### 每条内容卡片布局

```
[紧迫性色条] CVE-2026-XXXX - 标题
来源: NVD  [B]  🌏
完整摘要内容 2-3 句...
标签: [CVE][漏洞披露] [影响面:基础设施]
→ 原文链接
```

---

## 数据与文件结构

```
SecurityInfo/
├── main.py                     # 入口
├── source_config.yaml          # 信源配置
├── classifier_rules.yaml       # 分类关键词规则
├── llm_config.yaml             # LLM 配置(预留)
├── fetcher.py
├── parser.py
├── deduplicator.py
├── classifier.py
├── llm_processor.py            # 预留空壳
├── report_generator.py
├── templates/
│   └── weekly_report.html      # Jinja2 模板
├── data/
│   ├── raw_items.json
│   ├── parsed_items.json
│   ├── deduped_items.json
│   └── classified_items.json
└── reports/
    ├── Security_Reports.html             # 最新周报
    ├── Security_Reports_2026W25.html     # 历史归档
    └── Security_Reports_2026W26.html     # 历史归档
```

### 报告归档策略

- 新报告生成前: 将旧 `Security_Reports.html` 重命名为 `Security_Reports_2026Wxx.html`
- 始终保持最新报告为 `reports/Security_Reports.html`
- 历史报告按周命名，永不删除

---

## 配置文件

### source_config.yaml

```yaml
sources:
  - name: BleepingComputer
    url: https://www.bleepingcomputer.com/feed/
    type: rss
    language: en
    source_level: B
    region: global
    enabled: true
```

信源配置仅含信源自身属性。分类和标签不由信源配置决定。

### classifier_rules.yaml

```yaml
rules:
  - keywords: [大模型, LLM, GPT, Claude, prompt injection]
    category: ① AI/LLM 安全
    tags: []
```

关键词匹配采用"最多命中"策略处理多规则冲突。

### llm_config.yaml (预留)

```yaml
enabled: false
provider: openai
api_key: ""
model: gpt-4o-mini
base_url: ""
prompt_template: |
  请为以下网络安全资讯生成中文摘要: {content}
```

---

## 技术栈

| 组件 | 选型 |
|------|------|
| 语言 | Python 3.10+ |
| RSS 解析 | feedparser |
| HTML 模板 | Jinja2 |
| 配置文件 | PyYAML |
| 网络请求 | httpx / requests |
| 相似度去重 | rapidfuzz (fuzzywuzzy 更轻量的替代) |
