"""
AI News Monitor - 多源 AI 新闻抓取 + QQ邮箱【爆点】推送，AI 分析后写入 Notion
"""
import os
import re
import time
import imaplib
import email
import email.header
from datetime import datetime, timezone, timedelta
import requests

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
    {"name": "TLDR AI", "url": "https://tldr.tech/ai"},
    {"name": "The Batch (吴恩达)", "url": "https://www.deeplearning.ai/the-batch"},
    {"name": "量子位", "url": "https://www.qbitai.com"},
    {"name": "机器之心", "url": "https://www.jiqizhixin.com"},
]


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
#  新闻源抓取（Jina AI Reader）
# ═══════════════════════════════════════════

def fetch_source_articles(source):
    """抓取单个源的文章列表"""
    jina_url = f"https://r.jina.ai/{source['url']}"
    try:
        response = requests.get(jina_url, timeout=30)
        if response.status_code != 200:
            print(f"   抓取失败 {source['name']}: HTTP {response.status_code}")
            return []
    except requests.RequestException as e:
        print(f"   抓取失败 {source['name']}: {e}")
        return []

    raw_text = response.text
    articles = []
    seen_urls = set()

    # 提取所有 Markdown 链接 [Title](URL)
    for m in re.finditer(r'\[([^\]]{3,})\]\((https?://[^\s)]+)\)', raw_text):
        title = m.group(1).strip()
        url = m.group(2).split("?")[0]
        if len(title) < 5 or len(title) > 200 or url.startswith("#") or ".png" in url or ".jpg" in url or ".gif" in url or "mailto:" in url or "javascript:" in url or "tel:" in url:
            continue
        if url not in seen_urls:
            articles.append((title, url))
            seen_urls.add(url)

    # 如果正则没匹配到，尝试找纯 URL
    if not articles:
        for m in re.finditer(r"https://[a-z0-9.-]+\.[a-z]{2,}(/[^\s\"')]+)?", raw_text):
            url = m.group(0)
            if any(skip in url for skip in [".png", ".jpg", ".gif", "mailto:", "javascript:"]):
                continue
            title = url.split("/")[-1].replace("-", " ").replace("_", " ")
            if url not in seen_urls:
                articles.append((title, url))
                seen_urls.add(url)

    return articles[:MAX_ARTICLES_PER_SOURCE]


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

def analyze_article(article_url):
    """抓取文章正文并调用 LLM 分析"""
    article_content = requests.get(f"https://r.jina.ai/{article_url}", timeout=30).text
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
                summary = analyze_article(url)
                if write_to_notion(title, url, summary, source=source["name"]):
                    total_new += 1
                else:
                    total_failed += 1
            except Exception as e:
                print(f"    ❌ 处理失败: {e}")
                total_failed += 1
            time.sleep(1)

    # ── 2. 抓取 QQ 邮箱爆点
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
            else:
                summary = analyze_email_content(title, "")
            if write_to_notion(display_title, url or f"email://{title}", summary, source="QQ邮箱", is_email=True):
                total_new += 1
            else:
                total_failed += 1
        except Exception as e:
            print(f"    ❌ 处理失败: {e}")
            total_failed += 1
        time.sleep(1)

    # ── 3. 汇总 ──
    print("\n" + "=" * 50)
    print("🎉 全部完成！")
    print(f"✅ 新增: {total_new} 篇")
    print(f"⏭️  跳过: {total_seen} 篇（已存在）")
    print(f"❌ 失败: {total_failed} 篇")
    print("=" * 50)


if __name__ == "__main__":
    main()
