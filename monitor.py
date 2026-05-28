"""
Anthropic News Monitor - 抓取 Anthropic 最新新闻，AI 分析后写入 Notion
"""

import os
import json
import requests

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# LLM 配置：兼容 OpenAI / Anthropic 格式的 API
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
# 如果 API 是 Anthropic 格式（非 OpenAI 兼容），设为 "anthropic"
LLM_API_FORMAT = os.environ.get("LLM_API_FORMAT", "openai")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def get_latest_article():
    """使用 Jina Reader 抓取 Anthropic 新闻页，解析最新一篇文章的标题和 URL"""
    jina_url = "https://r.jina.ai/https://www.anthropic.com/news"
    response = requests.get(jina_url, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch from Jina: {response.status_code}")

    lines = response.text.split("\n")
    article_link = None
    article_title = None

    # 寻找第一条形如 [Title](/news/xxx) 的 Markdown 链接
    for line in lines:
        if "(/news/" in line and "[" in line:
            start_title = line.find("[") + 1
            end_title = line.find("]")
            start_url = line.find("(") + 1
            end_url = line.find(")")

            article_title = line[start_title:end_title]
            relative_url = line[start_url:end_url]
            article_link = f"https://www.anthropic.com{relative_url}"
            break

    return article_title, article_link


def is_already_recorded(url):
    """查询 Notion 数据库中是否已存在该 URL（去重）"""
    if not DATABASE_ID:
        raise ValueError("NOTION_DATABASE_ID is not set")

    query_url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "URL",
            "url": {"equals": url},
        }
    }
    response = requests.post(query_url, headers=NOTION_HEADERS, json=payload)
    if response.status_code == 200:
        results = response.json().get("results", [])
        return len(results) > 0
    else:
        print(f"Notion query failed: {response.text}")
        return False


def analyze_article(article_url):
    """抓取文章正文并调用 LLM 分析"""
    article_content = requests.get(
        f"https://r.jina.ai/{article_url}", timeout=30
    ).text

    # 截取过长内容，避免 token 超限
    if len(article_content) > 15000:
        article_content = article_content[:15000] + "\n\n[内容已截断...]"

    prompt = (
        "请详细阅读以下文章，并用中文进行分析，输出包含：\n"
        "1. 核心摘要（150字以内）\n"
        "2. 关键技术点或产品亮点（3-5条）\n"
        "3. 对行业的影响或意义（50字以内）\n\n"
        f"以下是文章内容：\n\n{article_content}"
    )

    if LLM_API_FORMAT == "anthropic":
        return _call_anthropic_api(prompt)
    else:
        return _call_openai_api(prompt)


def _call_openai_api(prompt):
    """调用 OpenAI 格式兼容的 API"""
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    response = requests.post(
        f"{LLM_BASE_URL}/chat/completions", headers=headers, json=data
    )
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        raise Exception(f"LLM API failed: {response.status_code} {response.text}")


def _call_anthropic_api(prompt):
    """调用 Anthropic 格式兼容的 API"""
    headers = {
        "x-api-key": LLM_API_KEY,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    data = {
        "model": LLM_MODEL,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }
    response = requests.post(
        f"{LLM_BASE_URL}/v1/messages", headers=headers, json=data
    )
    if response.status_code == 200:
        return response.json()["content"][0]["text"]
    else:
        raise Exception(f"LLM API failed: {response.status_code} {response.text}")


def write_to_notion(title, url, summary):
    """将分析结果写入 Notion 数据库"""
    create_url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": title}}]},
            "URL": {"url": url},
            "Summary": {
                "rich_text": [{"type": "text", "text": {"content": summary[:2000]}}]
            },
        },
        "children": [
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {"type": "text", "text": {"content": "AI 分析报告"}}
                    ]
                },
            },
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": summary}}]
                },
            },
        ],
    }
    response = requests.post(create_url, headers=NOTION_HEADERS, json=payload)
    if response.status_code == 200:
        print("Successfully written to Notion!")
    else:
        print(f"Failed to write to Notion: {response.status_code} {response.text}")


def main():
    try:
        print("Checking Anthropic News...")
        title, url = get_latest_article()

        if not url:
            print("No article found.")
            return

        print(f"Latest article found: {title} ({url})")

        if is_already_recorded(url):
            print("Article already recorded in Notion. Skipping.")
            return

        print("New article detected! Starting analysis...")
        summary = analyze_article(url)

        print("Writing to Notion...")
        write_to_notion(title, url, summary)

    except Exception as e:
        print(f"Error occurred: {e}")
        raise


if __name__ == "__main__":
    main()
