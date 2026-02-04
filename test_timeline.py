import os
import requests
import json
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup

# ==========================================
# 1. 設定區
# ==========================================
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

# 目標頁面 ID (Timeline 測試頁)
TARGET_PAGE_ID = "76775427" 

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("❌ 錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

parsed_url = RAW_URL.strip().rstrip('/')
# 處理有些 URL 結尾可能帶有 /wiki 的情況
if parsed_url.endswith('/wiki'):
    BASE_URL = parsed_url[:-5] # 移除結尾的 /wiki 以便組裝 API 路徑
else:
    BASE_URL = parsed_url

API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# ==========================================
# 2. 核心功能
# ==========================================

def get_page_content(page_id):
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version,space'}
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ 讀取失敗: {e}")
        return None

def add_one_month(date_str):
    formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y-%m", "%Y/%m"]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            new_dt = dt + relativedelta(months=1)
            if '-' in date_str: return new_dt.strftime("%Y-%m-%d")
            if '/' in date_str: return new_dt.strftime("%Y/%m/%d")
            return new_dt.strftime(fmt)
        except ValueError:
            continue
    return date_str

def modify_timeline_dates(html_content):
    print("🔧 正在解析 Timeline (Roadmap) 結構...")
    try:
        import lxml
        soup = BeautifulSoup(html_content, 'xml')
    except ImportError:
        print("❌ 錯誤：請先安裝 lxml 套件")
        sys.exit(1)

    bars = soup.find_all('ac:structured-macro', attrs={"ac:name": "roadmap-bar"})
    print(f"   🔎 找到 {len(bars)} 個 Timeline Bar")

    modified_count = 0
    for bar in bars:
        title_param = bar.find('ac:parameter', attrs={"ac:name": "title"})
        title = title_param.get_text() if title_param else "未命名"

        start_param = bar.find('ac:parameter', attrs={"ac:name": "startdate"})
        if start_param and start_param.string:
            old_start = start_param.string
            new_start = add_one_month(old_start)
            if old_start != new_start:
                start_param.string = new_start
                print(f"      🔄 [{title}] 開始: {old_start} -> {new_start}")
                modified_count += 1

        end_param = bar.find('ac:parameter', attrs={"ac:name": "enddate"})
        if end_param and end_param.string:
            old_end = end_param.string
            new_end = add_one_month(old_end)
            if old_end != new_end:
                end_param.string = new_end
                print(f"      🔄 [{title}] 結束: {old_end} -> {new_end}")
                modified_count += 1

    return str(soup), modified_count

def update_page(page_data, new_content):
    page_id = page_data['id']
    title = page_data['title']
    version = page_data['version']['number'] + 1
    
    payload = {
        "id": page_id,
        "type": "page",
        "title": title,
        "space": {"key": page_data['space']['key']},
        "body": {
            "storage": {
                "value": new_content,
                "representation": "storage"
            }
        },
        "version": {
            "number": version,
            "minorEdit": True
        }
    }

    url = f"{API_ENDPOINT}/{page_id}"
    try:
        r = requests.put(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), headers=get_headers(), data=json.dumps(payload))
        r.raise_for_status()
        print(f"✅ 頁面更新成功！(版本 v{version})")
    except Exception as e:
        print(f"❌ 更新失敗: {e}")
        print(r.text)

def main():
    print(f"=== Timeline 專項測試 (目標 ID: {TARGET_PAGE_ID}) ===")
    page_data = get_page_content(TARGET_PAGE_ID)
    if not page_data: return

    print(f"📄 讀取頁面成功: {page_data['title']}")
    original_body = page_data['body']['storage']['value']
    new_body, count = modify_timeline_dates(original_body)

    if count > 0:
        print(f"📊 共修改了 {count} 個時間點，準備上傳...")
        update_page(page_data, new_body)
    else:
        print("⚠️ 未發現可修改的 Timeline 日期")

if __name__ == "__main__":
    main()
