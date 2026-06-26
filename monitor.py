import os
import re
import time
import requests
from urllib.parse import urljoin
from datetime import datetime
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

FETCH_TIMEOUT = 30
MAX_ARTICLES_PER_SOURCE = 20

SOURCES = [
    {
        "name": "Anthropic",
        "url": "https://www.anthropic.com/news",
        "icon": "🧠",
        "link_pattern": r"/news/[a-z0-9-]+$",
    }
]

notion = Client(auth=NOTION_TOKEN)

def fetch_with_retry(url, headers=None, timeout=FETCH_TIMEOUT, retries=2):
    last_error = None
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    }
    headers = headers or default_headers
    for i in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                return response
            last_error = Exception("HTTP {}".format(response.status_code))
        except requests.RequestException as e:
            last_error = e
        if i < retries:
            time.sleep(2)
    raise last_error or Exception("Unknown error")

def extract_article_links(html, base_url, source_config=None):
    articles = []
    seen_urls = set()
    link_pattern = source_config.get("link_pattern") if source_config else None
    if link_pattern:
        all_links = re.findall(r"/news/[a-z0-9-]+", html, re.IGNORECASE)
        for path in all_links:
            if re.match(link_pattern, path):
                url = urljoin(base_url, path).split("?")[0]
                if url not in seen_urls:
                    title = path.split("/")[-1].replace("-", " ").title()
                    title = re.sub(r"\d+", "", title).strip()
                    if len(title) > 5:
                        articles.append((title, url))
                        seen_urls.add(url)
    unique_articles = []
    seen_titles = set()
    for title, url in articles:
        clean_title = re.sub(r"\W+", "", title).lower()
        if clean_title not in seen_titles and len(clean_title) > 5:
            seen_titles.add(clean_title)
            unique_articles.append((title, url))
    return unique_articles

def fetch_source_articles(source):
    print("\n📡 正在抓取: {} {}".format(source["icon"], source["name"]))
    articles = []
    try:
        response = fetch_with_retry(source["url"], timeout=FETCH_TIMEOUT)
        articles = extract_article_links(response.text, source["url"], source)
        print("✅ 抓取成功: 找到 {} 篇文章".format(len(articles)))
    except Exception as e:
        print("❌ 抓取失败: {}".format(e))
        return []
    return articles[:MAX_ARTICLES_PER_SOURCE]

def is_already_recorded(url):
    try:
        response = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter={
                "property": "URL",
                "url": {"equals": url},
            }
        )
        return len(response["results"]) > 0
    except Exception as e:
        print("⚠️  Notion 查询失败: {}".format(e))
        return False

def create_notion_page(title, source, url):
    try:
        properties = {
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": "{} {}".format(source["icon"], title[:2000])
                        }
                    }
                ]
            },
            "Source": {
                "select": {
                    "name": source["name"]
                }
            },
            "URL": {
                "url": url
            },
            "Status": {
                "select": {
                    "name": "To Read"
                }
            },
            "Date Added": {
                "date": {
                    "start": datetime.now().isoformat()
                }
            }
        }
        response = notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties
        )
        print("✅ 同步成功: {}".format(title))
        return True
    except Exception as e:
        print("❌ 同步失败: {}".format(title))
        print("   错误信息: {}".format(str(e)[:200]))
        return False

def main():
    print("🚀 开始同步Anthropic新闻到Notion")
    print("=" * 50)
    total_synced = 0
    total_skipped = 0
    for source in SOURCES:
        articles = fetch_source_articles(source)
        for title, url in articles:
            if is_already_recorded(url):
                print("ℹ️  已存在，跳过: {}".format(title))
                total_skipped += 1
                continue
            if create_notion_page(title, source, url):
                total_synced += 1
    print("\n" + "=" * 50)
    print("🎉 全部同步完成！")
    print("✅ 新增: {} 篇资讯".format(total_synced))
    print("ℹ️  跳过: {} 篇（已存在）".format(total_skipped))

if __name__ == "__main__":
    main()
