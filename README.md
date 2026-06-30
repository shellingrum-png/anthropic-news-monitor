# AI News Monitor

自动抓取多个 AI 新闻源 + QQ邮箱【爆点】推送，调用 LLM 分析后写入 Notion 数据库。

## 功能

- 每天定时抓取 7 个 AI 新闻源的最新文章
- 抓取 QQ 邮箱中标题含【爆点】🔥 [必推] 的邮件
- 通过 Jina AI Reader 抓取文章正文
- 调用 LLM 生成结构化中文摘要（核心摘要 + 技术亮点 + 行业影响）
- 通过 Notion API 按 URL 去重，避免重复写入
- 将分析结果写入 Notion 数据库，按来源分类
- 爆点邮件自动标记为"🔥 必推"状态

## 监控源

| 源 | 类型 | 说明 |
|----|------|------|
| Anthropic | 官方博客 | Claude 系列模型发布、技术研究 |
| OpenAI | 官方博客 | GPT 新功能、Sora 等新产品 |
| Google DeepMind | 官方博客 | AlphaFold、Gemini 技术报告 |
| TLDR AI | 日报聚合 | 每日 AI 要闻摘要 |
| The Batch (吴恩达) | 周报 | AI 周评与深度解读 |
| 量子位 | 中文媒体 | 大模型、自动驾驶、芯片报道 |
| 机器之心 | 中文媒体 | 开源模型、AI 论文技术拆解 |
| QQ邮箱 | 邮件推送 | 标题含【爆点】🔥 [必推] 的邮件 |

## 前置准备

### 1. 创建 Notion 数据库

在 Notion 中创建一个数据库，包含以下属性：

| 属性名 | 类型 | 说明 |
|--------|------|------|
| Title | Title | 文章标题 |
| URL | URL | 文章链接（用于去重） |
| Summary | Rich Text | AI 分析摘要 |
| Date | Date | 抓取时间 |
| Source | Select | 新闻来源（Anthropic/OpenAI/...） |
| Status | Select | 状态（To Read / 🔥 必推 / 价值低 / 待分析） |

### 2. 创建 Notion Integration

前往 [developers.notion.com](https://developers.notion.com) 创建 Integration，获取 `Internal Integration Token`，并将其关联到上述数据库。

### 3. 配置环境变量

在 GitHub Secrets 中配置：

| Secret 名称 | 说明 |
|-------------|------|
| `NOTION_TOKEN` | Notion Integration Token |
| `NOTION_DATABASE_ID` | Notion 数据库 ID |
| `LLM_API_KEY` | LLM API Key |
| `LLM_BASE_URL` | LLM API 请求地址，如 `https://coding.dashscope.aliyuncs.com/v1` |
| `LLM_MODEL` | 模型名称，如 `qwen3.6-plus` |
| `LLM_API_FORMAT` | （可选）`openai`（默认）或 `anthropic` |
| `QQ_EMAIL` | （可选）QQ 邮箱地址，用于抓取爆点邮件 |
| `QQ_IMAP_PASSWORD` | （可选）QQ 邮箱 IMAP 授权码 |
| `QQ_IMAP_SERVER` | （可选）IMAP 服务器，默认 `imap.qq.com` |

## 本地运行

```bash
pip install -r requirements.txt
export NOTION_TOKEN=xxx NOTION_DATABASE_ID=xxx ...
python monitor.py
```

## 添加新源

在 `monitor.py` 的 `SOURCES` 列表中添加：

```python
SOURCES = [
    # ... 现有源 ...
    {"name": "Hacker News", "url": "https://news.ycombinator.com/news"},
]
```

## 手动触发

在 GitHub 仓库的 **Actions → Daily Anthropic Monitor → Run workflow** 点击按钮即可手动运行。

## 更新日志

### v3.0
- ✅ 恢复 7 个新闻源（Anthropic/OpenAI/DeepMind/TLDR AI/The Batch/量子位/机器之心）
- ✅ 新增 QQ 邮箱【爆点】邮件抓取
- ✅ 使用原始 HTTP 请求操作 Notion（彻底解决 notion-client 版本兼容问题）
- ✅ LLM 结构化摘要：核心摘要 + 技术亮点 + 行业影响

### v2.0
- ✅ Jina AI + 直接 HTML 双抓取模式
- ✅ 重试机制 + 异常处理
- ✅ 详细日志输出
