"""
AI News Monitor - 多源 AI 新闻抓取，AI 分析后写入 Notion
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

# 每个源最多抓取的文章数（避免一次触发太多 LLM 调用）
MAX_ARTICLES_PER_SOURCE = 2

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# 新闻源配置
SOURCES = [
    {
        "name": "Anthropic",
        "url": "https://www.anthropic.com/news",
    },
    {
        "name": "OpenAI",
        "url": "https://openai.com/news",
    },
    {
        "name": "Google DeepMind",
        "url": "https://deepmind.google/discover",
    },
    {
        "name": "TLDR AI",
        "url": "https://tldr.tech/ai",
    },
    {
        "name": "The Batch (吴恩达)",
        "url": "https://www.deeplearning.ai/the-batch",
    },
    {
        "name": "量子位",
        "url": "https://www.qbitai.com",
    },
    {
        "name": "机器之心",
        "url": "https://www.jiqizhixin.com",
    },
]


def fetch_source_articles(source):
    """抓取单个源的文章列表"""
    jina_url = f"https://r.jina.ai/{source['url']}"
    try:
        response = requests.get(jina_url, timeout=30)
        if response.status_code != 200:
            print(f"  [!] Failed to fetch {source['name']}: HTTP {response.status_code}")
            return []
    except requests.RequestException as e:
        print(f"  [!] Failed to fetch {source['name']}: {e}")
        return []

    raw_text = response.text
    articles = []
    seen_urls = set()

    # 提取所有 Markdown 链接 [Title](URL)
    for m in re.finditer(r'\[([^\]]{3,})\]\((https?://[^\s)]+)\)', raw_text):
        title = m.group(1).strip()
        url = m.group(2).split("?")[0]  # 去掉 query params

        # 过滤掉导航链接、锚点链接、图片链接等
        if (
            len(title) < 5
            or len(title) > 200
            or url.startswith("#")
            or ".png" in url
            or ".jpg" in url
            or ".gif" in url
            or "mailto:" in url
            or "javascript:" in url
            or "tel:" in url
        ):
            continue

        if url not in seen_urls:
            articles.append((title, url))
            seen_urls.add(url)

    # 如果正则没匹配到，尝试找纯 URL
    if not articles:
        for m in re.finditer(
            r"https://[a-z0-9.-]+\.[a-z]{2,}(/[^\s\"')]+)?", raw_text
        ):
            url = m.group(0)
            # 跳过常见非文章链接
            if any(
                skip in url
                for skip in [".png", ".jpg", ".gif", "mailto:", "javascript:"]
            ):
                continue
            title = url.split("/")[-1].replace("-", " ").replace("_", " ")
            if url not in seen_urls:
                articles.append((title, url))
                seen_urls.add(url)

    return articles[:MAX_ARTICLES_PER_SOURCE]


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


def write_to_notion(title, url, summary, source=""):
    """将分析结果写入 Notion 数据库"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    create_url = "https://api.notion.com/v1/pages"
    props = {
        "Title": {"title": [{"text": {"content": title}}]},
        "URL": {"url": url},
        "Summary": {
            "rich_text": [{"type": "text", "text": {"content": summary[:2000]}}]
        },
        "Date": {"date": {"start": now}},
    }
    if source:
        props["Source"] = {"select": {"name": source}}

    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": props,
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
        print(f"  Written: {title[:60]}...")
    else:
        print(f"  Failed: {response.status_code} {response.text}")


def main():
    try:
        total_new = 0
        total_seen = 0

        for source in SOURCES:
            print(f"\nFetching: {source['name']} ({source['url']})")
            articles = fetch_source_articles(source)
            print(f"  Found {len(articles)} articles")

            for title, url in articles:
                if is_already_recorded(url):
                    print(f"    Already recorded: {title[:60]}...")
                    total_seen += 1
                    continue

                print(f"    New: {title[:60]}...")
                try:
                    summary = analyze_article(url)
                    write_to_notion(title, url, summary, source=source["name"])
                    total_new += 1
                except Exception as e:
                    print(f"    Error processing: {e}")

        print(f"\n{'='*50}")
        print(f"Done. {total_new} new entries written, {total_seen} already recorded.")

    except Exception as e:
        print(f"Error occurred: {e}")
        raise


if __name__ == "__main__":
    main()
