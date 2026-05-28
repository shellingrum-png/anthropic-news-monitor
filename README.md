# Anthropic Notion Monitor

自动抓取 Anthropic 最新新闻，调用 LLM 分析后写入 Notion 数据库。

## 功能

- 每天定时抓取 [Anthropic News](https://www.anthropic.com/news) 最新一篇文章
- 通过 Notion API 按 URL 去重，避免重复分析
- 调用 LLM（OpenAI 格式或 Anthropic 格式兼容）进行中文分析
- 将分析结果写入 Notion 数据库

## 前置准备

### 1. 创建 Notion 数据库

在 Notion 中创建一个数据库，包含以下属性：

| 属性名 | 类型 | 说明 |
|--------|------|------|
| Title | Title | 文章标题 |
| URL | URL | 文章链接（用于去重） |
| Summary | Text | AI 分析摘要 |

### 2. 创建 Notion Integration

前往 [developers.notion.com](https://developers.notion.com) 创建 Integration，获取 `Internal Integration Token`，并将其关联到上述数据库。

### 3. 配置 GitHub Secrets

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中添加：

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

export NOTION_TOKEN="your_token"
export NOTION_DATABASE_ID="your_database_id"
export LLM_API_KEY="your_key"
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_MODEL="gpt-4o-mini"

python monitor.py
```

## 手动触发

在 GitHub 仓库的 **Actions → Daily Anthropic Monitor → Run workflow** 点击按钮即可手动运行。
