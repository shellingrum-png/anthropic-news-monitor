# AI News Monitor

自动抓取多个 AI 新闻源，调用 LLM 分析后写入 Notion 数据库。

## 功能

- 每天定时抓取多个 AI 新闻源的最新文章
- **双抓取模式**：优先使用 Jina AI，失败时自动降级到直接 HTML 抓取
- 通过 Notion API 按 URL 去重，避免重复分析
- 调用 LLM（OpenAI 格式或 Anthropic 格式兼容）进行中文分析
- 将分析结果写入 Notion 数据库，按来源分类
- 完善的异常处理和重试机制

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

## 前置准备

### 1. 创建 Notion 数据库

在 Notion 中创建一个数据库，包含以下属性：

| 属性名 | 类型 | 说明 |
|--------|------|------|
| Title | Title | 文章标题 |
| URL | URL | 文章链接（用于去重） |
| Summary | Text | AI 分析摘要 |
| Date | Date | 抓取时间 |
| Source | Select | 新闻来源 |

### 2. 创建 Notion Integration

前往 [developers.notion.com](https://developers.notion.com) 创建 Integration，获取 `Internal Integration Token`，并将其关联到上述数据库。

### 3. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
# 编辑 .env 文件
```

或在 GitHub Secrets 中配置：

| Secret 名称 | 说明 |
|-------------|------|
| `NOTION_TOKEN` | Notion Integration Token |
| `NOTION_DATABASE_ID` | Notion 数据库 ID（从数据库 URL 中提取） |
| `LLM_API_KEY` | LLM API Key |
| `LLM_BASE_URL` | LLM API 请求地址，如 `https://api.openai.com/v1` |
| `LLM_MODEL` | 模型名称，如 `gpt-4o-mini` |
| `LLM_API_FORMAT` | （可选）API 格式，`openai`（默认）或 `anthropic` |

## 本地运行

```bash
pip install -r requirements.txt

# 配置好 .env 后运行
python monitor.py
```

## 添加新源

在 `monitor.py` 的 `SOURCES` 列表中添加新源：

```python
SOURCES = [
    # ... 现有源 ...
    {
        "name": "Hacker News",
        "url": "https://news.ycombinator.com/news",
        "link_pattern": r"/item\?id=\d+$",  # 可选，用于过滤链接
    },
]
```

## 手动触发

在 GitHub 仓库的 **Actions → Daily AI Monitor → Run workflow** 点击按钮即可手动运行。

## 更新日志

### v2.0 主要改进

- ✅ **双抓取模式**：Jina AI + 直接 HTML 抓取，自动降级
- ✅ **重试机制**：网络请求失败自动重试 2 次
- ✅ **异常处理**：单篇文章失败不影响整体流程
- ✅ **详细日志**：清晰的成功/失败状态输出
- ✅ **.env 支持**：本地调试更方便
- ✅ **链接过滤**：每个源可配置独立的链接匹配规则
- ✅ **超时优化**：避免单个请求卡住整个流程
