
import os, json
from notion_client import Client

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DB_ID = os.environ["NOTION_DB_ID"]
client = Client(auth=NOTION_TOKEN, notion_version=os.environ.get("Notion-Version", "2025-09-03"))

db = client.databases.retrieve(DB_ID)
title = " / ".join([t["text"]["content"] for t in db.get("title", [])]) or "(untitled)"
props = db.get("properties", {})

print(f"Activities DB title: {title}")
print("Properties (name -> type & id):")
print(json.dumps({k: {"type": v.get("type"), "id": v.get("id")} for k, v in props.items()},
                 indent=2, ensure_ascii=False))

