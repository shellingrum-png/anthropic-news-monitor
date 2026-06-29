import os
import re
import json
import time
import requests
from urllib.parse import urljoin
from datetime import datetime
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# LLM config
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

FETCH_TIMEOUT = 30
MAX_ARTICLES_PER_SOURCE = 20
SUMMARY_MAX_CHARS = 2000

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
        print("️  Notion 查询失败: {}".format(e))
        return False

def fetch_article_content(url):
    """通过 Jina AI Reader 抓取文章正文，失败时降级到直接请求"""
    # 优先用 Jina AI Reader（返回 Markdown）
    jina_url = "https://r.jina.ai/{}".format(url)
    try:
        resp = requests.get(jina_url, timeout=20)
        if resp.status_code == 200 and resp.text.strip():
            text = resp.text.strip()
            # 去掉 Jina 自动加的首行 title 和 URL 元信息
            lines = text.split("\n")
            # 跳过前两行（通常是标题行和 URL 行）
            if len(lines) > 2 and lines[0].startswith("#"):
                content = "\n".join(lines[2:])
            else:
                content = text
            if len(content) > 50:
                print("   📖 通过 Jina 抓取成功 ({} chars)".format(len(content)))
                return content[:10000]  # 截断避免 token 过多
    except Exception as e:
        print("   ⚠️ Jina 抓取失败: {}，尝试直接抓取".format(e))

    # 降级：直接抓取 HTML，提取正文
    try:
        resp = fetch_with_retry(url, timeout=20)
        # 简单提取：去掉 script/style，取 body 文本
        html = resp.text
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
        html = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", html).strip()
        if len(text) > 50:
            print("    直接抓取成功 ({} chars)".format(len(text)))
            return text[:10000]
    except Exception as e:
        print("   ❌ 正文抓取失败: {}".format(e))

    return ""

def generate_summary(content, title):
    """调用 LLM 生成结构化文章摘要"""
    if not LLM_API_KEY:
        return ""

    prompt = """你是 AI 领域的专业编辑。请为以下文章生成结构化的中文摘要。

严格按照以下格式输出：

1. 核心摘要（150字以内）
[用1-2句话概括文章的核心内容]

2. 关键技术点或产品亮点（3-5条）
- **[要点标题]**：[具体说明]
- **[要点标题]**：[具体说明]
- **[要点标题]**：[具体说明]

3. 对行业的影响或意义（50字以内）
[一句话总结影响或意义]

文章标题: {title}

文章内容:
{content}

请按上述格式输出摘要：""".format(title=title, content=content[:5000])

    try:
        resp = requests.post(
            "{}/chat/completions".format(LLM_BASE_URL.rstrip("/")),
            headers={
                "Authorization": "Bearer {}".format(LLM_API_KEY),
                "Content-Type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": "你是一个专业的科技新闻编辑，擅长用简洁的中文概括技术文章的核心要点，并按照结构化格式输出摘要。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 1500,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            summary = data["choices"][0]["message"]["content"].strip()
            print("   🤖 摘要生成成功 ({} chars)".format(len(summary)))
            return summary[:SUMMARY_MAX_CHARS]
        else:
            print("   ⚠️ LLM 调用失败: HTTP {} {}".format(resp.status_code, resp.text[:200]))
    except Exception as e:
        print("   ⚠️ LLM 调用异常: {}".format(e))

    return ""

def create_notion_page(title, source, url, summary=""):
    try:
        properties = {
            "Title": {
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
            "Date": {
                "date": {
                    "start": datetime.now().isoformat()
                }
            }
        }

        # 如果有摘要，写入 Summary 字段
        if summary:
            properties["Summary"] = {
                "rich_text": [
                    {
                        "text": {
                            "content": summary
                        }
                    }
                ]
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
    print("🚀 开始同步 Anthropic 新闻到 Notion")
    print("=" * 50)
    total_synced = 0
    total_skipped = 0
    total_failed = 0
    for source in SOURCES:
        articles = fetch_source_articles(source)
        for title, url in articles:
            if is_already_recorded(url):
                print("ℹ️  已存在，跳过: {}".format(title))
                total_skipped += 1
                continue

            # 1. 抓取正文
            content = fetch_article_content(url)

            # 2. 生成摘要
            summary = ""
            if content:
                print("   🔄 正在生成摘要...")
                summary = generate_summary(content, title)

            # 3. 写入 Notion
            if create_notion_page(title, source, url, summary):
                total_synced += 1
            else:
                total_failed += 1

            # 避免请求过快
            time.sleep(1)

    print("\n" + "=" * 50)
    print("🎉 全部同步完成！")
    print("✅ 新增: {} 篇资讯".format(total_synced))
    print(" 失败: {} 篇".format(total_failed))
    print("ℹ️  跳过: {} 篇（已存在）".format(total_skipped))

if __name__ == "__main__":
    main()
