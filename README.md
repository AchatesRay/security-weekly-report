# 网络安全周报系统

从 80+ 信源（安全媒体、厂商、CERT、AI 厂商等）自动抓取网络安全资讯，
经 10 步流水线处理后生成 HTML 周报。

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
pipeline/                 10 步数据处理管道
config/                   配置（信源、分类规则、关键词等）
server/                   Web 管理后台
templates/                周报/管理界面模板
reports/                  生成的 HTML 周报
docs/                     文档
```

## 管道步骤

1. 抓取 RSS → 2. 解析 → 3. 关键字过滤(标题) → 4. 全文提取 →
5. 关键字过滤(全文) → 6. 去重 → 7. 分类 → 8. 翻译 →
9. AI 摘要 → 10. 生成 HTML 报告
