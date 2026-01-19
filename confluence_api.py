import os
import requests
import json
import re
import sys
from datetime import date, timedelta
from requests.auth import HTTPBasicAuth

# --- 設定區 ---
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

# --- 智慧網址修正 (v2.0) ---
# 確保我們只拿到最乾淨的域名 (Domain)，例如 https://qsiaiot.atlassian.net
# 1. 移除結尾斜線
BASE_URL = RAW_URL.rstrip('/')
# 2. 如果使用者填了 /wiki 結尾，把它切掉
if BASE_URL.endswith("/wiki"):
    BASE_URL = BASE_URL[:-5]

# 現在 BASE_URL 保證是 https://your-site.atlassian.net
print(f"API 基準網址: {BASE_URL}")

def get_target_dates():
    """計算本週日期與檔名"""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    friday = monday + timedelta(days=4)
    return {
        "monday_str": monday.strftime("%Y-%m-%d"),
        "sunday_str": sunday.strftime("%Y-%m-%d"),
        "filename": friday.strftime("%Y%m%d")
    }

def find_latest_report():
    """搜尋標題符合 WeeklyReport_20... 的最新頁面"""
    print("正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport_20" ORDER BY created DESC'
    
    # 正確組建 API 路徑
    url = f"{BASE_URL}/wiki/rest/api/content/search"
    
    params = {
        'cql': cql,
        'limit': 1,
        'expand': 'body.storage,ancestors,space'
    }
    
    try:
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        
        # 如果是 404，印出我們到底打去哪裡了，方便除錯
        if response.status_code == 404:
            print(f"❌ 404 錯誤 - 請求網址: {response.url}")
            
        response.raise_for_status()
        results = response.json().get('results', [])
        
        if not results:
            print("⚠️ 搜尋成功但無結果。這可能是因為：")
            print("1. 真的沒有標題含 'WeeklyReport_20' 的頁面。")
            print("2. API Token 權限不足以看到該空間。")
            raise Exception("找不到任何符合 'WeeklyReport_20' 的頁面")
        
        latest = results[0]
        print(f"✅ 找到最新週報: {latest['title']} (ID: {latest['id']})")
        return latest
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ API 請求失敗: {e}")
        if response.status_code == 401:
            print("💡 提示: 401 通常代表 API Token 無效或 Email 錯誤。")
        sys.exit(1)

def create_new_report(latest_page, dates):
    """基於舊內容建立新頁面"""
    new_title = f"WeeklyReport_{dates['filename']}"
    print(f"準備建立新頁面: {new_title}")
    
    original_body = latest_page['body']['storage']['value']
    
    # --- 日期替換邏輯 ---
    # 尋找所有 YYYY-MM-DD
    found_dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", original_body)
    new_body = original_body
    
    if len(found_dates) >= 2:
        old_start = found_dates[0]
        old_end = found_dates[1]
        print(f"偵測到舊日期區間: {old_start} ~ {old_end}")
        
        # 只替換前兩個出現的日期 (避免誤傷內文)
        new_body = new_body.replace(old_start, dates['monday_str'], 1)
        new_body = new_body.replace(old_end, dates['sunday_str'], 1)
        print(f"已替換為: {dates['monday_str']} ~ {dates['sunday_str']}")
    else:
        print("⚠️ 警告：舊內容中找不到足夠的日期格式，將直接複製內容。")

    # --- 準備 Payload ---
    ancestors = []
    if latest_page.get('ancestors'):
        parent_id = latest_page['ancestors'][-1]['id']
        ancestors.append({'id': parent_id})
    
    space_key = latest_page['space']['key']
    
    payload = {
        "title": new_title,
        "type": "page",
        "space": {"key": space_key},
        "ancestors": ancestors,
        "body": {
            "storage": {
                "value": new_body,
                "representation": "storage"
            }
        }
    }
    
    create_url = f"{BASE_URL}/wiki/rest/api/content"
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(
            create_url, 
            auth=HTTPBasicAuth(USERNAME, API_TOKEN),
            headers=headers,
            data=json.dumps(payload)
        )
        response.raise_for_status()
        new_page_data = response.json()
        
        # 組合網址 (處理 webui 可能沒有 /wiki 開頭的情況)
        webui = new_page_data['_links']['webui']
        if not webui.startswith('/wiki'):
            webui = '/wiki' + webui
        full_link = f"{BASE_URL}{webui}"
        
        print(f"🎉 成功建立頁面！")
        print(f"頁面 ID: {new_page_data['id']}")
        print(f"連結: {full_link}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        # 印出詳細錯誤訊息 (通常包含為什麼失敗，例如標題重複)
        print(f"伺服器回應: {response.text}")

def main():
    dates = get_target_dates()
    print(f"=== Confluence API 自動週報腳本 (v2.0 URL修正版) ===")
    print(f"目標日期: {dates['monday_str']} ~ {dates['sunday_str']}")
    
    try:
        latest_page = find_latest_report()
        create_new_report(latest_page, dates)
    except Exception as e:
        print(f"執行中斷: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
