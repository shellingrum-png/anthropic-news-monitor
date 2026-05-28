"""
Anthropic News Monitor - 抓取 Anthropic 最新新闻，AI 分析后写入 Notion
"""

from datetime import datetime, timezone
import os
import re
import requests

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# LLM 配置：兼容 OpenAI / Anthropic 格式的 API
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_API_FORMAT = os.environ.get("LLM_API_FORMAT", "openai")

# 每次最多抓取的文章数（避免一次触发太多 LLM 调用）
MAX_ARTICLES_PER_RUN = 3

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def get_all_articles():
    """使用 Jina Reader 抓取 Anthropic 新闻页，解析所有文章链接"""
    jina_url = "https://r.jina.ai/https://www.anthropic.com/news"
    response = requests.get(jina_url, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch from Jina: {response.status_code}")

    raw_text = response.text
    articles = []
    seen_urls = set()

    # 策略 1: 匹配 Markdown 链接 [/news/...](...)
    for line in raw_text.split("\n"):
        if "[/news/" in line:
            m = re.search(r'\[([^\]]+)\]\((/news/[^\s)]+)\)', line)
            if m:
                url = f"https://www.anthropic.com{m.group(2)}"
                title = m.group(1)
                if url not in seen_urls:
                    articles.append((title, url))
                    seen_urls.add(url)

    # 策略 2: 匹配完整 URL 的 Markdown 链接
    if not articles:
        for line in raw_text.split("\n"):
            if "anthropic.com/news/" in line:
                m = re.search(
                    r'\[([^\]]+)\]\((https://www\.anthropic\.com/news/[^\s)]+)\)',
                    line,
                )
                if m:
                    url = m.group(2)
                    title = m.group(1)
                    if url not in seen_urls:
                        articles.append((title, url))
                        seen_urls.add(url)

    # 策略 3: 用正则直接搜 URL
    if not articles:
        for m in re.finditer(
            r'https://www\.anthropic\.com/news/([^\s")]+)', raw_text
        ):
            url = m.group(0)
            title = m.group(1).rstrip("/").replace("-", " ")
            if url not in seen_urls:
                articles.append((title, url))
                seen_urls.add(url)

    return articles


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
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    create_url = "https://api.notion.com/v1/pages"
    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": title}}]},
            "URL": {"url": url},
            "Summary": {
                "rich_text": [{"type": "text", "text": {"content": summary[:2000]}}]
            },
            "Date": {"date": {"start": now}},
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
        print(f"  Written: {title}")
    else:
        print(f"  Failed: {response.status_code} {response.text}")


def main():
    try:
        print("Checking Anthropic News...")
        articles = get_all_articles()

        if not articles:
            print("No articles found.")
            return

        print(f"Found {len(articles)} articles on page")

        processed = 0
        new_count = 0

        for title, url in articles:
            if processed >= MAX_ARTICLES_PER_RUN:
                print(f"Reached max articles limit ({MAX_ARTICLES_PER_RUN})")
                break

            processed += 1
            print(f"  [{processed}] {title}")

            if is_already_recorded(url):
                print(f"    Already recorded, skipping")
                continue

            print(f"    New article! Analyzing...")
            summary = analyze_article(url)

            print(f"    Writing to Notion...")
            write_to_notion(title, url, summary)
            new_count += 1

        print(f"\nDone. Processed {processed} articles, {new_count} new entries written.")

    except Exception as e:
        print(f"Error occurred: {e}")
        raise


if __name__ == "__main__":
    main()
