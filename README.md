# 网络安全周报系统

从 80+ 信源（安全媒体、厂商、CERT、AI 厂商等）自动抓取网络安全资讯，经 9 步流水线处理后生成 HTML 周报。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 完整运行
python app.py --run

# 跳过抓取，用上次数据重新生成
python app.py --run --skip-fetch

# 启动管理后台 (默认 8081 端口)
python app.py server [port]
```

## 项目结构

```
app.py                    统一入口
pipeline/                 9 步数据处理管道
config/                   配置（信源、评分关键词等）
server/                   Web 管理后台
templates/                周报/管理界面模板
reports/                  生成的 HTML 周报
docs/                     文档
```

## 9 步管道

1. 抓取 RSS → 2. 解析 → 3. 去重 →
4. 评分过滤(阶段1: 快速预筛) → 5. 全文提取 →
6. 评分过滤(阶段2: 完整评分+分类) → 7. AI 摘要 →
8. 翻译摘要 → 9. 生成 HTML 报告

## 配置目录

```
config/
  source_config.yaml       信源配置（约 80 个信源）
  scoring_keywords.json    评分关键词配置（强/中/弱三级权重）
  keywords.json            关键词别名映射
  llm_config.yaml          LLM 配置（预留）
  settings.json            管理后台配置
```
