import os
import re
import time
import requests
from urllib.parse import urljoin
from datetime import datetime, timedelta
from dotenv import load_dotenv
from notion_client import Client

# 加载环境变量
load_dotenv()

# 配置
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-3-5-sonnet-20240620")
LLM_API_FORMAT = os.getenv("LLM_API_FORMAT", "openai")

# 抓取配置
FETCH_TIMEOUT = 30
FETCH_RETRY_COUNT = 2
MAX_ARTICLES_PER_SOURCE = 10
DEFAULT_SOURCE_ICON = "🤖"

# 资讯源配置
SOURCES = [
    {
        "name": "Anthropic",
        "url": "https://www.anthropic.com/news",
        "icon": "🧠",
        "link_pattern": r"/news/[a-z0-9-]+$",
    },
    {
        "name": "OpenAI",
        "url": "https://openai.com/news",
        "icon": "🔴",
        "link_pattern": r"/news/[a-z0-9-]+$",
    },
    {
        "name": "Google DeepMind",
        "url": "https://deepmind.google/discover/blog/",
        "icon": "🔵",
        "link_pattern": r"/discover/blog/[a-z0-9-]+$",
    },
]

# Notion客户端
notion = Client(auth=NOTION_TOKEN)

def fetch_with_retry(url, headers=None, timeout=FETCH_TIMEOUT, retries=FETCH_RETRY_COUNT):
    """带重试的请求"""
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
            last_error = Exception(f"HTTP {response.status_code}")
        except requests.RequestException as e:
            last_error = e
        if i < retries:
            time.sleep(2)
    raise last_error or Exception("Unknown error")

def extract_article_links(html, base_url, source_name, source_config=None):
    """提取文章链接 - 专门适配现代前端渲染的页面"""
    articles = []
    seen_urls = set()

    # 1. 直接提取所有匹配pattern的链接，适配Next.js/React渲染的页面
    link_pattern = source_config.get("link_pattern") if source_config else None
    if link_pattern:
        # 从整个HTML里提取所有符合pattern的路径
        base_path_pattern = re.compile(r'["\'](/[a-z0-9/-]+)["\']', re.IGNORECASE)
        for m in base_path_pattern.finditer(html):
            path = m.group(1).strip()
            if re.match(link_pattern, path):
                url = urljoin(base_url, path).split("?")[0]
                if url not in seen_urls:
                    # 从路径生成标题
                    title = path.split("/")[-1].replace("-", " ").title()
                    title = re.sub(r'\d+', '', title).strip()  # 移除数字
                    if len(title) > 5:
                        articles.append((title, url))
                        seen_urls.add(url)
    
    # 2. 回退到a标签提取
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
        
        # 检查是否匹配pattern
        if link_pattern and not re.match(link_pattern, href.split("?")[0]):
            continue
        
        # 尝试提取标题
        title = None
        
        # 方式1: 从 a 标签文本
        text_match = re.search(r'>([^<]{5,})<', a_tag, re.IGNORECASE)
        if text_match:
            title = text_match.group(1).strip()
        
        # 方式2: 从 alt 属性（图片链接）
        if not title:
            alt_match = re.search(r'alt=["\']([^"\']+)["\']', a_tag, re.IGNORECASE)
            if alt_match and len(alt_match.group(1)) > 3:
                title = alt_match.group(1).strip()
        
        if title and len(title) > 5:
            articles.append((title, url))
            seen_urls.add(url)
    
    # 去重返回
    unique_articles = []
    seen_titles = set()
    for title, url in articles:
        clean_title = re.sub(r'\W+', '', title).lower()
        if clean_title not in seen_titles:
            seen_titles.add(clean_title)
            unique_articles.append((title, url))
    
    return unique_articles

def fetch_source_articles(source):
    """抓取单个源的文章列表"""
    print(f"\n📡 正在抓取: {source['icon']} {source['name']}")
    
    articles = []
    try:
        response = fetch_with_retry(source['url'], timeout=FETCH_TIMEOUT)
        articles = extract_article_links(response.text, source['url'], source['name'], source)
        print(f"✅ 抓取成功: 找到 {len(articles)} 篇文章")
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return []
    
    return articles[:MAX_ARTICLES_PER_SOURCE]

def is_already_recorded(url):
    """查询 Notion 数据库中是否已存在该 URL（去重）"""
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
        print(f"⚠️  Notion 查询失败: {e}")
        return False

def get_article_content(url):
    """简单提取文章正文摘要"""
    try:
        response = fetch_with_retry(url)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 移除不必要的标签
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        
        # 提取文本
        text = soup.get_text(separator='\n\n')
        # 清理多余空行
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        # 返回前300字作为摘要
        return text[:300] + "..." if len(text) > 300 else text
    except Exception as e:
        print(f"⚠️  抓取文章内容失败: {e}")
        return ""

def create_notion_page(title, source, url, content=""):
    """在Notion中创建新页面"""
    try:
        # 字段映射
        properties = {
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": f"{source['icon']} {title[:2000]}"
                        }
                    }
                ]
            },
            "Source": {
                "select": {
                    "name": source['name']
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
        
        # 构建正文
        children = []
        if content:
            children.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": content
                            }
                        }
                    ]
                }
            })
        
        # 创建页面
        response = notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties,
            children=children[:100]
        )
        
        print(f"✅ 同步成功: {title}")
        return True
    except Exception as e:
        print(f"❌ 同步失败: {title}")
        print(f"   错误信息: {str(e)[:200]}")
        return False

def main():
    print("🚀 开始同步AI资讯到Notion")
    print("=" * 50)
    
    total_synced = 0
    total_skipped = 0
    
    for source in SOURCES:
        articles = fetch_source_articles(source)
        
        for title, url in articles:
            # 检查是否已存在
            if is_already_recorded(url):
                print(f"ℹ️  已存在，跳过: {title}")
                total_skipped += 1
                continue
            
            # 抓取内容
            content = get_article_content(url)
            
            # 创建Notion页面
            if create_notion_page(title, source, url, content):
                total_synced += 1
    
    print("\n" + "=" * 50)
    print("🎉 全部同步完成！")
    print(f"✅ 新增: {total_synced} 篇资讯")
    print(f"ℹ️  跳过: {total_skipped} 篇（已存在）")

if __name__ == "__main__":
    main()
EOF && \
# 先测试下Anthropic的抓取是否正常，单独写个测试脚本
cat > test_anthropic.py << 'TESTEOF'
import re
import requests
from urllib.parse import urljoin

def test_anthropic_scrape():
    print("🔍 测试抓取Anthropic新闻页面...")
    url = "https://www.anthropic.com/news"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        print(f"✅ 页面获取成功，状态码: {response.status_code}")
        print(f"📄 页面大小: {len(response.text)} 字节")
        
        # 提取所有/news/链接
        pattern = r'["\'](/news/[a-z0-9-]+)["\']'
        links = re.findall(pattern, response.text, re.IGNORECASE)
        unique_links = list(set(links))
        
        print(f"\n🔗 找到 {len(unique_links)} 个新闻链接:")
        for i, link in enumerate(unique_links[:10]):  # 只显示前10个
            full_url = urljoin(url, link)
            title = link.split("/")[-1].replace("-", " ").title()
            print(f"  {i+1}. {title[:50]}")
            print(f"     {full_url}")
        
        if len(unique_links) > 10:
            print(f"  ... 还有 {len(unique_links) - 10} 个链接")
        
        return len(unique_links) > 0
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return False

if __name__ == "__main__":
    success = test_anthropic_scrape()
    if success:
        print("\n🎉 测试成功！抓取逻辑正常工作")
    else:
        print("\n❌ 测试失败，需要进一步排查")
TESTEOF && \
# 运行测试
python3 test_anthropic.py
