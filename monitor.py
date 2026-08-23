"""
AI News Monitor - 多源 AI 新闻源抓取 + QQ邮箱【爆点】推送，AI 分析后写入 Notion
"""
import os
import re
import time
import imaplib
import email
import email.header
from datetime import datetime, timezone, timedelta
import requests
import xml.etree.ElementTree as ET

# ── 环境变量 ──
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_API_FORMAT = os.environ.get("LLM_API_FORMAT", "openai")

# QQ 邮箱 IMAP 配置
QQ_EMAIL = os.environ.get("QQ_EMAIL", "")
QQ_IMAP_PASSWORD = os.environ.get("QQ_IMAP_PASSWORD", "")
QQ_IMAP_SERVER = os.environ.get("QQ_IMAP_SERVER", "imap.qq.com")

MAX_ARTICLES_PER_SOURCE = 5
FETCH_TIMEOUT = 30
SUMMARY_MAX_CHARS = 2000

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# ── 新闻源配置 ──
SOURCES = [
    {"name": "Anthropic", "url": "https://www.anthropic.com/news"},
    {"name": "OpenAI", "url": "https://openai.com/news"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/discover"},
    {"name": "The Batch (吴恩达)", "url": "https://www.deeplearning.ai/the-batch"},
    {"name": "量子位", "url": "https://www.qbitai.com"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/"},
    {"name": "The Verge AI", "url": "https://www.theverge.com/ai-artificial-intelligence"},
]

# ── 导航/页脚噪声标题黑名单 ──
NAVIGATION_BLOCKLIST = [
    # 通用导航
    "skip to main content", "skip to content", "skip to footer", "skip navigation",
    "main content", "footer", "header", "navigation",
    # Anthropic
    "research", "policy", "try claude", "about", "careers", "safety",
    "try claude enterprise", "claude", "anthropic",
    # OpenAI
    "core", "products", "safety", "company",
    # DeepMind
    "explore models", "explore", "evals", "publications", "blog",
    "about us", "about google deepmind", "our technology", "impact",
    # TLDR
    "one daily email", "subscribe", "signup", "login",
    # The Batch
    "the batch", "deeplearning.ai",
    # 量子位/机器之心
    "首页", "导航", "菜单", "搜索", "登录", "注册",
    "关注我们", "关于我们", "联系方式",
    # 通用
    "read more", "learn more", "view all", "see all",
    "contact us", "privacy policy", "terms of service",
    "cookie policy", "accessibility",
]

# 日期模式：如 "Jun 26, 2026", "2026-06-26", "June 26" 等
DATE_ONLY_PATTERN = re.compile(
    r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s*\d{4}$',
    re.IGNORECASE
)

# ── 各源的文章 URL 模式（用于验证是否为真实文章） ──
# 只有匹配到这些模式的 URL 才会被当作文章
SOURCE_URL_PATTERNS = {
    "Anthropic": [
        r"anthropic\.com/news/",           # 文章页
    ],
    "OpenAI": [
        r"openai\.com/(research|news|index)/",  # 文章/研究页
    ],
    "Google DeepMind": [
        r"deepmind\.google/discover/blog/",
        r"deepmind\.google/research/",
        r"deepmind\.google/technology/",
        r"blog\.google/innovation-and-ai/models-and-research/.*deepmind",
        r"blog\.google/innovation-and-ai/models-and-research/gemini",
    ],
    "TLDR AI": [
        r"tldr\.tech/ai/\d+",              # 日期路径文章
    ],
    "The Batch (吴恩达)": [
        r"deeplearning\.ai/the-batch/",
        r"deeplearning\.ai/batch-\d+",
    ],
    "量子位": [
        r"qbitai\.com/\d+\.html",          # 数字ID文章
        r"qbitai\.com/20\d{2}/\d+/",       # 日期路径
    ],
    "机器之心": [
        r"jiqizhixin\.com/articles/",
        r"jiqizhixin\.com/20\d{2}/\d+/",
    ],
    "TechCrunch AI": [
        r"techcrunch\.com/\d{4}/",
        r"techcrunch\.com/category/artificial-intelligence/",
    ],
    "The Verge AI": [
        r"theverge\.com/ai-artificial-intelligence/",
        r"theverge\.com/\d{4}/",
    ],
}

# ── 各源 RSS/Atom feed URL（优先于 Jina/直接抓取） ──
RSS_FEED_URLS = {
    "Anthropic": None,
    "OpenAI": "https://openai.com/blog/rss.xml",
    "Google DeepMind": "https://blog.google/innovation-and-ai/models-and-research/google-deepmind/rss",
    "量子位": "https://www.qbitai.com/feed",
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "The Batch (吴恩达)": None,
}


# ═══════════════════════════════════════════
#  标题验证工具
# ═══════════════════════════════════════════

def _is_valid_article_title(title, url):
    """
    判断一个 (title, url) 是否为真实文章而非导航/噪声。
    返回 True 表示是有效文章。
    """
    title_lower = title.lower().strip()

    # 1. 黑名单匹配
    if title_lower in NAVIGATION_BLOCKLIST:
        return False

    # 2. 纯日期标题
    if DATE_ONLY_PATTERN.match(title):
        return False

    # 3. 仅包含标点/数字/括号的无效标题
    if re.match(r'^[\d\s\(\)\-\:\.\,一-鿿]+$', title) and len(title) < 20:
        return False

    # 4. 源特定 URL 模式验证
    # 如果当前 URL 不匹配该源的任何文章模式，则跳过
    # 这能过滤掉 "Research", "Policy" 等导航页链接
    url_matched = False
    for source_name, patterns in SOURCE_URL_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, url):
                url_matched = True
                break
        if url_matched:
            break

    return url_matched


# ═══════════════════════════════════════════
#  Notion 操作（直接用 HTTP，避开 notion-client 版本问题）
# ═══════════════════════════════════════════

def is_already_recorded(url):
    """查询 Notion 数据库中是否已存在该 URL（去重）"""
    if not DATABASE_ID:
        raise ValueError("NOTION_DATABASE_ID is not set")
    query_url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {"filter": {"property": "URL", "url": {"equals": url}}}
    response = requests.post(query_url, headers=NOTION_HEADERS, json=payload)
    if response.status_code == 200:
        return len(response.json().get("results", [])) > 0
    print(f"  ⚠️ Notion 查询失败: {response.status_code} {response.text[:200]}")
    return False


def write_to_notion(title, url, summary, source="", is_email=False):
    """将分析结果写入 Notion 数据库"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    create_url = "https://api.notion.com/v1/pages"
    props = {
        "Title": {"title": [{"text": {"content": title}}]},
        "URL": {"url": url},
        "Summary": {"rich_text": [{"type": "text", "text": {"content": summary[:SUMMARY_MAX_CHARS]}}]},
        "Date": {"date": {"start": now}},
    }
    if source:
        props["Source"] = {"select": {"name": source}}
    if is_email:
        props["Status"] = {"select": {"name": "🔥 必推"}}

    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": props,
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": "AI 分析报告"}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": summary}}]}},
        ],
    }
    response = requests.post(create_url, headers=NOTION_HEADERS, json=payload)
    if response.status_code == 200:
        print(f"  ✅ 写入成功: {title[:60]}")
        return True
    else:
        print(f"  ❌ 写入失败: {response.status_code} {response.text[:200]}")
        return False


# ═══════════════════════════════════════════
#  RSS / Atom feed 抓取
# ═══════════════════════════════════════════

# ── RSS 文章的描述缓存（URL → 描述文本），Jina 抓正文失败时作为 fallback ──
_rss_descriptions = {}


def _fetch_rss_feed(source_name, feed_url):
    """
    从 RSS/Atom feed 抓取文章列表。
    同时支持 RSS 2.0（<item><link>）和 Atom（<entry><link href=>）两种格式。
    同时提取 description/content 存入 _rss_descriptions 供 fallback。
    """
    try:
        resp = requests.get(feed_url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        })
        if resp.status_code != 200:
            return None
    except requests.RequestException:
        return None

    articles = []
    seen_urls = set()

    # ── RSS 2.0 格式：用正则解析（避免 XML 命名空间问题） ──
    for m in re.finditer(r'<item>(.*?)</item>', resp.text, re.DOTALL):
        block = m.group(1)
        title_m = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', block, re.DOTALL)
        url_m = re.search(r'<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>', block)
        if not title_m or not url_m:
            continue
        title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
        url = url_m.group(1).strip()
        if title and url and url not in seen_urls and len(title) > 3:
            articles.append((title, url))
            seen_urls.add(url)
            # 提取 description / content:encoded 作为 fallback
            desc_m = re.search(r'<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>', block, re.DOTALL)
            content_m = re.search(r'<content:encoded>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content:encoded>', block, re.DOTALL)
            raw_desc = ""
            if content_m:
                raw_desc = content_m.group(1)
            elif desc_m:
                raw_desc = desc_m.group(1)
            if raw_desc:
                clean = re.sub(r'<[^>]+>', ' ', raw_desc)
                clean = re.sub(r'\s+', ' ', clean).strip()
                if len(clean) > 30:
                    _rss_descriptions[url] = clean
        if len(articles) >= MAX_ARTICLES_PER_SOURCE:
            return articles

    # ── Atom 格式：用正则解析（<entry> + <link rel="alternate" href="...">） ──
    if not articles:
        for m in re.finditer(r'<entry>(.*?)</entry>', resp.text, re.DOTALL):
            block = m.group(1)
            title_m = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', block, re.DOTALL)
            url_m = re.search(r'<link[^>]+rel=["\']alternate["\'][^>]+href=["\']([^"\']+)["\']', block)
            if not url_m:
                url_m = re.search(r'<link[^>]+href=["\']([^"\']+)["\']', block)
            if not title_m or not url_m:
                continue
            title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
            url = url_m.group(1).strip()
            if title and url and url not in seen_urls and len(title) > 3:
                articles.append((title, url))
                seen_urls.add(url)
                # 提取 summary / content 作为 fallback
                desc_m = re.search(r'<summary[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</summary>', block, re.DOTALL)
                content_m = re.search(r'<content[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content>', block, re.DOTALL)
                raw_desc = ""
                if content_m:
                    raw_desc = content_m.group(1)
                elif desc_m:
                    raw_desc = desc_m.group(1)
                if raw_desc:
                    clean = re.sub(r'<[^>]+>', ' ', raw_desc)
                    clean = re.sub(r'\s+', ' ', clean).strip()
                    if len(clean) > 30:
                        _rss_descriptions[url] = clean
            if len(articles) >= MAX_ARTICLES_PER_SOURCE:
                return articles

    return articles if articles else None


# ═══════════════════════════════════════════
#  新闻源抓取（RSS → Jina AI Reader → 备用直抓）
# ═══════════════════════════════════════════

def fetch_source_articles(source):
    """抓取单个源的文章列表。优先走 RSS feed，失败再走 Jina，最后直接抓 HTML"""
    # ── 0. RSS feed 优先（最稳定，不受 Cloudflare 影响） ──
    feed_url = RSS_FEED_URLS.get(source["name"])
    if feed_url:
        rss_articles = _fetch_rss_feed(source["name"], feed_url)
        if rss_articles:
            print(f"   📡 RSS feed 成功，获取 {len(rss_articles)} 篇")
            return rss_articles
        else:
            print(f"   ⚠️ RSS feed 失败，回退到 Jina/直接抓取")

    # ── 1. Jina AI Reader ──
    jina_url = f"https://r.jina.ai/{source['url']}"
    raw_text = None

    try:
        response = requests.get(jina_url, timeout=30)
        if response.status_code == 200 and len(response.text.strip()) > 200:
            raw_text = response.text
            # 检测 Jina 返回的是否为错误/空页面
            error_signs = ["just a moment", "cloudflare", "captcha", "access denied", "openresty"]
            if any(s in raw_text.lower() for s in error_signs):
                raw_text = None
    except requests.RequestException:
        pass

    # Jina 失败时的备用方案：直接请求 + HTML 解析
    if not raw_text:
        raw_text = _fetch_source_direct(source)
        if not raw_text:
            print(f"   ⚠️ {source['name']}: 所有抓取方式均失败，跳过")
            return []

    articles = []
    seen_urls = set()

    # 提取所有 Markdown 链接 [Title](URL)
    for m in re.finditer(r'\[([^\]]{3,})\]\((https?://[^\s)]+)\)', raw_text):
        title = m.group(1).strip()
        url = m.group(2).split("?")[0]

        if not _is_valid_article_title(title, url):
            continue

        if url not in seen_urls:
            articles.append((title, url))
            seen_urls.add(url)

    # 如果正则没匹配到，尝试找纯 URL
    if not articles:
        for m in re.finditer(r"https://[a-z0-9.-]+\.[a-z]{2,}(/[^\s\"')]+)?", raw_text):
            url = m.group(0)
            if not _is_valid_article_title("", url):
                continue
            if any(skip in url for skip in [".png", ".jpg", ".gif", "mailto:", "javascript:"]):
                continue
            title = url.split("/")[-1].replace("-", " ").replace("_", " ")
            if len(title) < 5:
                continue
            if url not in seen_urls:
                articles.append((title, url))
                seen_urls.add(url)

    return articles[:MAX_ARTICLES_PER_SOURCE]


def _fetch_source_direct(source):
    """
    备用抓取：直接请求页面并解析 HTML（当 Jina 失败时）。
    目前仅对 Anthropic 和 OpenAI 有效（SSR 渲染，HTML 中包含文章链接）。
    返回 markdown 格式文本以便复用现有解析逻辑。
    """
    source_name = source["name"]
    url = source["url"]

    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        })
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    html = resp.text
    markdown_lines = []

    if source_name == "Anthropic":
        # Anthropic news 页面：Next.js SSR，HTML 中包含 /news/xxx 链接
        links = re.findall(r'href="(/news/[^"]+)"', html)
        seen = set()
        for path in links:
            if path in seen:
                continue
            seen.add(path)
            # 尝试从 HTML 中提取标题
            # 通常结构：<a href="/news/xxx">Title</a>
            title_match = re.search(rf'<a[^>]*href="{re.escape(path)}"[^>]*>(.*?)</a>', html)
            if title_match:
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                if title and len(title) > 3:
                    full_url = f"https://www.anthropic.com{path}"
                    markdown_lines.append(f"[{title}]({full_url})")

    elif source_name == "OpenAI":
        # OpenAI news 页面
        links = re.findall(r'href="(/(?:research|news)/[^"]+)"', html)
        seen = set()
        for path in links:
            if path in seen:
                continue
            seen.add(path)
            title_match = re.search(rf'<a[^>]*href="{re.escape(path)}"[^>]*>(.*?)</a>', html)
            if title_match:
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                if title and len(title) > 3:
                    full_url = f"https://openai.com{path}"
                    markdown_lines.append(f"[{title}]({full_url})")

    elif source_name == "Google DeepMind":
        # DeepMind discover 页面：Next.js SSR，包含 /discover/blog/xxx 相对链接
        links = re.findall(r'href="((?:https://deepmind\.google)?/discover/blog/[^"#?]+)"', html)
        seen = set()
        for link in links:
            # 统一转成绝对路径
            if link.startswith("/"):
                full_url = f"https://deepmind.google{link}"
            else:
                full_url = link
            # 去掉 utm 参数
            full_url = full_url.split("?")[0]
            if full_url in seen:
                continue
            seen.add(full_url)
            # 提取标题
            path_escaped = re.escape(link)
            title_match = re.search(rf'<a[^>]*href="{path_escaped}"[^>]*>(.*?)</a>', html, re.DOTALL)
            if title_match:
                title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
                if title and len(title) > 3:
                    markdown_lines.append(f"[{title}]({full_url})")

    elif source_name == "The Batch (吴恩达)":
        # deeplearning.ai The Batch 页面：
        # 文章链接是覆盖层 <a>，标题在 aria-label 属性里
        # 格式：<a class="absolute inset-0" aria-label="TITLE" href="/the-batch/issue-367"></a>
        matches = re.findall(
            r'<a[^>]+aria-label="([^"]+)"[^>]+href="(/the-batch/[^"]+)"[^>]*>',
            html
        )
        # 也尝试反向顺序（href 在 aria-label 前面）
        matches += re.findall(
            r'<a[^>]+href="(/the-batch/[^"]+)"[^>]+aria-label="([^"]+)"[^>]*>',
            html
        )
        seen = set()
        for m in matches:
            # 统一格式 (title, path)
            if m[0].startswith("/"):
                path, title = m[1], m[0]
            else:
                title, path = m[0], m[1]
            # 跳过非文章页
            if path in ("/the-batch", "/the-batch/") or "/tag/" in path:
                continue
            if path.endswith("/about") or path.endswith("/search"):
                continue
            if "/page/" in path:
                continue
            if path in seen:
                continue
            seen.add(path)
            title = title.strip()
            if title and len(title) > 3:
                full_url = f"https://www.deeplearning.ai{path}"
                markdown_lines.append(f"[{title}]({full_url})")

    return "\n".join(markdown_lines) if markdown_lines else None


# ═══════════════════════════════════════════
#  QQ 邮箱抓取
# ═══════════════════════════════════════════

def decode_header_value(header_value):
    """解码邮件头中的编码字符串"""
    if not header_value:
        return ""
    decoded_parts = email.header.decode_header(header_value)
    result = ""
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result += part.decode(charset or "utf-8", errors="replace")
        else:
            result += part
    return result


def fetch_qq_email_articles():
    """从 QQ 邮箱抓取标题包含【爆点】🔥 [必推] 的邮件"""
    if not QQ_EMAIL or not QQ_IMAP_PASSWORD:
        print("  ⚠️ QQ 邮箱未配置，跳过")
        return []

    articles = []
    try:
        mail = imaplib.IMAP4_SSL(QQ_IMAP_SERVER, 993)
        mail.login(QQ_EMAIL, QQ_IMAP_PASSWORD)
        mail.select("INBOX")

        # 搜索最近 7 天的邮件
        since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        if status != "OK":
            print("  ⚠️ 邮件搜索失败")
            mail.logout()
            return []

        email_ids = messages[0].split()
        print(f"   找到 {len(email_ids)} 封邮件，开始筛选...")

        for eid in email_ids:
            status, msg_data = mail.fetch(eid, "(RFC822)")
            if status != "OK":
                continue
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)

            subject = decode_header_value(msg.get("Subject", ""))
            if "【爆点】" in subject or "[必推]" in subject:
                # 提取正文中的链接
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

                # 从正文提取 URL
                urls = re.findall(r"https?://[^\s<>\"]+", body)
                if urls:
                    url = urls[0]
                    articles.append((subject, url))
                    print(f"    📩 命中: {subject[:50]}")
                else:
                    articles.append((subject, ""))
                    print(f"    📩 命中(无链接): {subject[:50]}")

        mail.logout()
        print(f"  ✅ 共找到 {len(articles)} 封爆点邮件")

    except imaplib.IMAP4.error as e:
        print(f"  ❌ QQ 邮箱登录失败: {e}")
    except Exception as e:
        print(f"  ❌ QQ 邮箱抓取异常: {e}")

    return articles


# ═══════════════════════════════════════════
#  LLM 分析
# ═══════════════════════════════════════════

def analyze_article(article_url, rss_fallback_text=None):
    """抓取文章正文并调用 LLM 分析。返回 None 表示内容无效（403/无正文等），应跳过入库。
    如果 Jina/直接抓取均失败，且有 rss_fallback_text（来自 RSS description），则用 fallback 分析。"""
    # 先用 Jina 抓取
    resp = requests.get(f"https://r.jina.ai/{article_url}", timeout=30)
    if resp.status_code != 200:
        print(f"     Jina 抓取失败: HTTP {resp.status_code}，尝试直接抓取")
        # 备用：直接抓取原始 URL
        try:
            resp2 = requests.get(article_url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            })
            if resp2.status_code == 200:
                # 用简单方法提取文本
                article_content = re.sub(r'<[^>]+>', ' ', resp2.text)
                article_content = re.sub(r'\s+', ' ', article_content).strip()
            else:
                print(f"     直接抓取也失败: HTTP {resp2.status_code}")
                article_content = None
        except requests.RequestException as e:
            print(f"     直接抓取异常: {e}")
            article_content = None
    else:
        article_content = resp.text.strip()

    # 检测 403/404/反爬 等无效内容
    error_patterns = [
        "403", "404", "401", "Access Denied", "访问受限", "Forbidden",
        "openresty", "Ray ID", "cf-error", "captcha", "验证", "人机验证",
        "just a moment", "cloudflare", "security check",
    ]
    if article_content and any(p in article_content.lower() for p in error_patterns):
        print(f"    ⚠️ 内容被反爬拦截，尝试 RSS 摘要 fallback")
        article_content = None

    # 正文无效时，使用 RSS 描述作为 fallback
    if (not article_content or len(article_content) < 100) and rss_fallback_text:
        print(f"    📡 使用 RSS 摘要作为内容来源 ({len(rss_fallback_text)} chars)")
        article_content = rss_fallback_text

    # 最终仍无内容，跳过
    if not article_content or len(article_content) < 100:
        print(f"    ⛔ 无可用内容，跳过")
        return None

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


def analyze_email_content(subject, body):
    """对邮件正文进行 LLM 分析"""
    content = body[:10000] if body else subject
    prompt = (
        "以下是一封 AI 领域的重要推送邮件，请用中文进行分析，输出包含：\n"
        "1. 核心摘要（150字以内）\n"
        "2. 关键点（3-5条）\n"
        "3. 对行业的影响或意义（50字以内）\n\n"
        f"邮件标题: {subject}\n"
        f"邮件内容: {content}\n"
    )
    if LLM_API_FORMAT == "anthropic":
        return _call_anthropic_api(prompt)
    else:
        return _call_openai_api(prompt)


def _call_openai_api(prompt):
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    data = {"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "max_tokens": 1500}
    response = requests.post(f"{LLM_BASE_URL}/chat/completions", headers=headers, json=data, timeout=60)
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    raise Exception(f"LLM API failed: {response.status_code} {response.text[:200]}")


def _call_anthropic_api(prompt):
    headers = {"x-api-key": LLM_API_KEY, "Content-Type": "application/json", "anthropic-version": "2023-06-01"}
    data = {"model": LLM_MODEL, "max_tokens": 2048, "messages": [{"role": "user", "content": prompt}]}
    response = requests.post(f"{LLM_BASE_URL}/v1/messages", headers=headers, json=data, timeout=60)
    if response.status_code == 200:
        return response.json()["content"][0]["text"]
    raise Exception(f"LLM API failed: {response.status_code} {response.text[:200]}")


# ═══════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════

def main():
    if not NOTION_TOKEN or not DATABASE_ID:
        print("❌ NOTION_TOKEN 或 NOTION_DATABASE_ID 未配置")
        return

    total_new = 0
    total_seen = 0
    total_failed = 0

    # ── 1. 抓取新闻源 ──
    print("\n" + "=" * 50)
    print(" 开始抓取 AI 新闻源")
    print("=" * 50)
    for source in SOURCES:
        print(f"\n {source['name']} ({source['url']})")
        articles = fetch_source_articles(source)
        print(f"  找到 {len(articles)} 篇文章")
        for title, url in articles:
            if not url:
                continue
            if is_already_recorded(url):
                print(f"    ⏭️ 已存在: {title[:50]}")
                total_seen += 1
                continue
            print(f"    🆕 新文章: {title[:50]}")
            try:
                fallback = _rss_descriptions.get(url)
                summary = analyze_article(url, rss_fallback_text=fallback)
                if summary is None:
                    print(f"    ⏭️ 内容无效，跳过: {title[:50]}")
                    continue
                if write_to_notion(title, url, summary, source=source["name"]):
                    total_new += 1
                else:
                    total_failed += 1
            except Exception as e:
                print(f"     处理失败: {e}")
                total_failed += 1
            time.sleep(1)

    # ── 2. 抓取 QQ 邮箱爆点 ──
    print("\n" + "=" * 50)
    print("📧 开始抓取 QQ 邮箱爆点推送")
    print("=" * 50)
    email_articles = fetch_qq_email_articles()
    for title, url in email_articles:
        display_title = f" {title}"
        if url and is_already_recorded(url):
            print(f"    ⏭️ 已存在: {title[:50]}")
            total_seen += 1
            continue
        print(f"    🆕 爆点邮件: {title[:50]}")
        try:
            # 如果有 URL，抓取正文分析；否则直接用邮件标题分析
            if url:
                summary = analyze_article(url)
                if summary is None:
                    print(f"    ⏭️ 链接内容无效，跳过: {title[:50]}")
                    continue
            else:
                summary = analyze_email_content(title, "")
            if write_to_notion(display_title, url or f"email://{title}", summary, source="QQ邮箱", is_email=True):
                total_new += 1
            else:
                total_failed += 1
        except Exception as e:
            print(f"     处理失败: {e}")
            total_failed += 1
        time.sleep(1)

    # ── 3. 汇总 ──
    print("\n" + "=" * 50)
    print("🎉 全部完成！")
    print(f"✅ 新增: {total_new} 篇")
    print(f"⏭️  跳过: {total_seen} 篇（已存在）")
    print(f"❌ 失败: {total_failed} 篇")
    print("=" * 50)

    # 全部失败（有新文章但全写不进去）时返回非 0，让 GitHub Actions 显示失败
    if total_new == 0 and total_failed > 0:
        import sys
        sys.exit(1)


if __name__ == "__main__":
    main()
