"""
AI News Monitor - 多源 AI 新闻抓取，AI 分析后写入 Notion
"""

from datetime import datetime, timezone
import os
import re
import requests
import time
from urllib.parse import urljoin

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# LLM 配置：兼容 OpenAI / Anthropic 格式的 API
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_API_FORMAT = os.environ.get("LLM_API_FORMAT", "openai")

# 每个源最多抓取的文章数（避免一次触发太多 LLM 调用）
MAX_ARTICLES_PER_SOURCE = 3

# 抓取重试配置
FETCH_TIMEOUT = 15
FETCH_RETRY_COUNT = 2

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# 通用请求头
COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# 新闻源配置
SOURCES = [
    {
        "name": "Anthropic",
        "url": "https://www.anthropic.com/news",
        "link_pattern": r"/news/[a-z0-9-]+$",
    },
    {
        "name": "OpenAI",
        "url": "https://openai.com/news",
        "link_pattern": r"/news/[a-z0-9-]+$",
        # OpenAI 用 h3 当标题，在链接附近找标题
        "title_nearby": r'<h[2-4][^>]*>([^<]{5,})</h',
    },
    {
        "name": "Google DeepMind",
        "url": "https://deepmind.google/discover",
        "link_pattern": r"/discover/.*",
    },
    {
        "name": "TLDR AI",
        "url": "https://tldr.tech/ai",
        "link_pattern": r"/ai/[0-9]+$",
    },
    {
        "name": "The Batch (吴恩达)",
        "url": "https://www.deeplearning.ai/the-batch",
        "link_pattern": r"/the-batch/[a-z0-9-]+$",
    },
    {
        "name": "量子位",
        "url": "https://www.qbitai.com",
        "link_pattern": r"/\d+/.*\.html$",
        "title_nearby": 'title=[\x27\"]([^\x27\"]{10,})[\x27\"]',  # 从 a 标签的 title 属性取
    },
    {
        "name": "机器之心",
        "url": "https://www.jiqizhixin.com",
        "link_pattern": r"/articles/[a-z0-9-]+$",
    },
]


def fetch_with_retry(url, headers=None, timeout=FETCH_TIMEOUT, retries=FETCH_RETRY_COUNT):
    """带重试的请求"""
    last_error = None
    for i in range(retries + 1):
        try:
            response = requests.get(url, headers=headers or COMMON_HEADERS, timeout=timeout)
            if response.status_code == 200:
                return response
            last_error = Exception(f"HTTP {response.status_code}")
        except requests.RequestException as e:
            last_error = e
        if i < retries:
            time.sleep(2)
    raise last_error or Exception("Unknown error")


def extract_article_links(html, base_url, source_name, source_config=None):
    """提取文章链接 - 支持多种页面结构和源定制配置"""
    articles = []
    seen_urls = set()
    title_nearby_pattern = source_config.get('title_nearby') if source_config else None

    # 1. 先试 r.jina.ai 格式（Markdown 链接）
    for m in re.finditer(r'\[([^\]]{5,})\]\((https?://[^\s)]+)\)', html):
        title = m.group(1).strip()
        url = m.group(2).split("?")[0]
        if is_valid_article_link(url, title, base_url) and url not in seen_urls:
            articles.append((title, url))
            seen_urls.add(url)

    # 2. 通用 HTML 链接提取（支持现代前端渲染）
    # 遍历所有 a 标签
    for a_match in re.finditer(r'<a[^>]+>', html, re.IGNORECASE):
        a_tag = a_match.group(0)
        
        # 提取 href
        href_match = re.search(r'href=["\']([^"\']+)["\']', a_tag, re.IGNORECASE)
        if not href_match:
            continue
        
        href = href_match.group(1)
        
        # 跳过锚点和JS
        if href.startswith('#') or href.startswith('javascript:') or href.startswith('mailto:'):
            continue
        
        # 处理相对路径和绝对路径
        if href.startswith('http'):
            url = href.split("?")[0]
        elif href.startswith('/'):
            url = urljoin(base_url, href).split("?")[0]
        else:
            continue
        
        if url in seen_urls:
            continue
        
        # 尝试提取标题
        title = None
        
        # 方式1: 从 alt 属性（图片链接）
        alt_match = re.search(r'alt=["\']([^"\']+)["\']', a_tag, re.IGNORECASE)
        if alt_match and len(alt_match.group(1)) > 3:
            title = alt_match.group(1).strip()
        
        # 方式2: 匹配完整的 <a...>text</a> 结构
        if not title:
            a_full_pattern = re.escape(a_tag) + r'([^<]{4,})</a>'
            a_full_match = re.search(a_full_pattern, html[a_match.start():a_match.start() + 1000], re.IGNORECASE | re.DOTALL)
            if a_full_match:
                text = re.sub(r'<[^>]+>', '', a_full_match.group(1)).strip()
                if len(text) > 3:
                    title = text
        
        # 方式3: 从 a 标签后面的标题标签 (h2/h3/h4)
        if not title:
            pos = a_match.end()
            nearby = html[pos:pos + 500]
            heading_match = re.search(r'<h[2-4][^>]*>([^<]+)</h', nearby, re.IGNORECASE)
            if heading_match:
                title = heading_match.group(1).strip()
        
        # 方式4: 从 aria-label
        if not title:
            aria_match = re.search(r'aria-label=["\']([^"\']+)["\']', a_tag, re.IGNORECASE)
            if aria_match and len(aria_match.group(1)) > 3:
                title = aria_match.group(1).strip()
        
        # 方式5: 从 URL 推导标题（过滤掉 .html 等后缀）
        if not title:
            path = url.rstrip('/').split('/')[-1]
            path = re.sub(r'\.(html|htm|php|jsp)$', '', path, flags=re.IGNORECASE)
            if path and len(path) > 3 and not path.isdigit():
                title = path.replace('-', ' ').replace('_', ' ').title()
            else:
                # 如果是数字ID，尝试从页面其他位置找标题
                continue
        
        # 清理标题（移除日期前缀、多余空格）
        title = re.sub(r'^[A-Z][a-z]{2} \d{1,2},? \d{4}\s*', '', title)  # 移除 "Jun 1, 2026"
        title = re.sub(r'^(Announcements|Research|News)\s*', '', title)  # 移除分类前缀
        title = title.strip()
        
        # 方式6: 使用源配置的定制化标题提取模式
        if (not title or len(title) < 5) and title_nearby_pattern:
            nearby_pos = a_match.start()
            nearby = html[max(0, nearby_pos - 200):nearby_pos + 500]
            nearby_match = re.search(title_nearby_pattern, nearby)
            if nearby_match:
                title = nearby_match.group(1).strip()
        
        # 验证并添加
        if len(title) >= 5 and is_valid_article_link(url, title, base_url) and url not in seen_urls:
            articles.append((title, url))
            seen_urls.add(url)

    return articles
    articles = []
    seen_urls = set()

    # 1. 先试 r.jina.ai 格式（Markdown 链接）
    for m in re.finditer(r'\[([^\]]{5,})\]\((https?://[^\s)]+)\)', html):
        title = m.group(1).strip()
        url = m.group(2).split("?")[0]

        if is_valid_article_link(url, title, base_url):
            if url not in seen_urls:
                articles.append((title, url))
                seen_urls.add(url)

    # 2. 如果没有找到，尝试直接从 HTML 中提取链接
    if not articles:
        for m in re.finditer(r'<a[^>]+href=["\'](https?://[^"\']+)["\'][^>]*>([^<]+)</a>', html, re.IGNORECASE):
            url = m.group(1).split("?")[0]
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if is_valid_article_link(url, title, base_url):
            if url not in seen_urls:
                articles.append((title, url))
                seen_urls.add(url)

    # 3. 提取相对路径链接
    if not articles:
        for m in re.finditer(r'href=["\'](/[^"\']+)["\'][^>]*>([^<]{5,})</a>', html, re.IGNORECASE):
            relative_url = m.group(1)
            title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
            url = urljoin(base_url, relative_url).split("?")[0]
            if is_valid_article_link(url, title, base_url):
                if url not in seen_urls:
                    articles.append((title, url))
                    seen_urls.add(url)

    return articles


def is_valid_article_link(url, title, base_url):
    """判断是否为有效文章链接"""
    if not title or len(title) < 5 or len(title) > 200:
        return False
    if not url or url.startswith("#"):
        return False
    
    # 排除资源文件
    if any(ext in url.lower() for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".ico"]):
        return False
    
    # 排除导航和操作链接
    if any(skip in url.lower() for skip in ["mailto:", "javascript:", "tel:", "/cdn-cgi/", "/category/", "/tag/", "/page/"]):
        return False
    if any(skip in title.lower() for skip in ["首页", "关于我们", "联系我们", "隐私政策", "使用条款", "登录", "注册", "订阅", "分享", "twitter", "linkedin", "github"]):
        return False
    
    return True


def fetch_source_articles(source):
    """抓取单个源的文章列表"""
    print(f"\n  正在抓取: {source['name']}")
    
    # 方法1: 尝试使用 r.jina.ai
    jina_url = f"https://r.jina.ai/{source['url']}"
    articles = []
    
    try:
        response = fetch_with_retry(jina_url, timeout=FETCH_TIMEOUT)
        raw_text = response.text
        articles = extract_article_links(raw_text, source['url'], source['name'])
        print(f"    [OK] 通过 Jina AI 抓取成功: {len(articles)} 篇")
    except Exception as e:
        print(f"    [!] Jina AI 失败: {e}")
        
        # 方法2: 直接抓取 HTML
        try:
            response = fetch_with_retry(source['url'], timeout=FETCH_TIMEOUT)
            articles = extract_article_links(response.text, source['url'], source['name'])
            print(f"    [OK] 直接抓取成功: {len(articles)} 篇")
        except Exception as e2:
            print(f"    [!] 直接抓取也失败: {e2}")
            return []
    
    # 过滤当前源的特定 pattern
    if source.get("link_pattern"):
        pattern = re.compile(source["link_pattern"])
        articles = [(t, u) for t, u in articles if pattern.search(u)]
    
    return articles[:MAX_ARTICLES_PER_SOURCE]


def is_already_recorded(url):
    """查询 Notion 数据库中是否已存在该 URL（去重）"""
    if not DATABASE_ID:
        print("    [!] NOTION_DATABASE_ID 未配置")
        return False

    query_url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {
        "filter": {
            "property": "URL",
            "url": {"equals": url},
        }
    }
    try:
        response = requests.post(query_url, headers=NOTION_HEADERS, json=payload, timeout=30)
        if response.status_code == 200:
            results = response.json().get("results", [])
            return len(results) > 0
        else:
            print(f"    [!] Notion 查询失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"    [!] Notion 查询异常: {e}")
        return False


def analyze_article(article_url):
    """抓取文章正文并调用 LLM 分析"""
    article_content = ""
    
    # 尝试抓取文章内容
    for fetch_method in ["jina", "direct"]:
        try:
            if fetch_method == "jina":
                fetch_url = f"https://r.jina.ai/{article_url}"
                response = fetch_with_retry(fetch_url, timeout=30)
            else:
                response = fetch_with_retry(article_url, timeout=30)
            
            article_content = response.text
            if len(article_content) > 500:
                break
        except Exception as e:
            print(f"      [{fetch_method}] 抓取失败: {e}")
            continue
    
    if not article_content or len(article_content) < 200:
        raise Exception("无法获取文章内容")

    if len(article_content) > 12000:
        article_content = article_content[:12000] + "\n\n[内容已截断...]"

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
        f"{LLM_BASE_URL}/chat/completions", headers=headers, json=data, timeout=120
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
        f"{LLM_BASE_URL}/v1/messages", headers=headers, json=data, timeout=120
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
        "Title": {"title": [{"text": {"content": title[:100]}}]},
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
    response = requests.post(create_url, headers=NOTION_HEADERS, json=payload, timeout=30)
    if response.status_code == 200:
        resp = response.json()
        print(f"    ✓ 已写入: {title[:60]}... (page_id: {resp.get('id', '?')})")
        return True
    else:
        print(f"    ✗ 写入失败: {response.status_code} {response.text[:200]}")
        return False


def main():
    try:
        total_new = 0
        total_seen = 0
        total_error = 0

        print("=" * 60)
        print("AI News Monitor 启动")
        print(f"时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        print(f"数据库ID: {DATABASE_ID}")
        print("=" * 60)

        for source in SOURCES:
            print(f"\n▶ {source['name']}")
            try:
                articles = fetch_source_articles(source)
            except Exception as e:
                print(f"  [!] 抓取异常: {e}")
                continue

            for title, url in articles:
                try:
                    if is_already_recorded(url):
                        print(f"    - 已存在: {title[:50]}...")
                        total_seen += 1
                        continue

                    print(f"    + 新文章: {title[:50]}...")
                    try:
                        summary = analyze_article(url)
                        if write_to_notion(title, url, summary, source=source["name"]):
                            total_new += 1
                    except Exception as e:
                        print(f"    ✗ 处理失败: {str(e)[:80]}")
                        total_error += 1
                        continue
                except Exception as e:
                    print(f"    ✗ 检查重复时出错: {e}")
                    total_error += 1

        print(f"\n{'=' * 60}")
        print("执行完成!")
        print(f"  新增: {total_new} 篇")
        print(f"  已存在: {total_seen} 篇")
        print(f"  失败: {total_error} 篇")
        print(f"{'=' * 60}")

    except Exception as e:
        print(f"\n严重错误: {e}")
        raise


if __name__ == "__main__":
    main()
