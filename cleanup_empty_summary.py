"""清理 Notion 数据库中 Summary 为空的记录"""
import os
from dotenv import load_dotenv
from notion_client import Client

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)

def get_pages_without_summary():
    """查询所有 Summary 为空的页面"""
    response = notion.databases.query(
        database_id=NOTION_DATABASE_ID,
        filter={
            "property": "Summary",
            "rich_text": {
                "is_empty": True
            }
        }
    )
    return response.get("results", [])

def delete_page(page_id):
    """删除指定页面（Notion 中为归档到回收站）"""
    notion.pages.update(
        page_id=page_id,
        archived=True
    )

def main():
    pages = get_pages_without_summary()
    print("找到 {} 条 Summary 为空的记录".format(len(pages)))

    if not pages:
        print("没有需要清理的记录")
        return

    # 展示要删除的记录
    print("\n即将删除以下记录：")
    for i, page in enumerate(pages):
        props = page.get("properties", {})
        title_blocks = props.get("Title", {}).get("title", [])
        title = title_blocks[0]["plain_text"] if title_blocks else "(无标题)"
        url_blocks = props.get("URL", {})
        url = url_blocks.get("url", "(无链接)") if url_blocks.get("url") else "(无链接)"
        print("  {}. {} - {}".format(i + 1, title, url))

    confirm = input("\n确认删除以上 {} 条记录？(y/n): ".format(len(pages)))
    if confirm.lower() != "y":
        print("已取消")
        return

    deleted = 0
    for page in pages:
        page_id = page["id"]
        try:
            delete_page(page_id)
            deleted += 1
            print("  ✅ 已删除: {}".format(page_id))
        except Exception as e:
            print("  ❌ 删除失败: {} - {}".format(page_id, e))

    print("\n完成！共删除 {} 条记录".format(deleted))

if __name__ == "__main__":
    main()
