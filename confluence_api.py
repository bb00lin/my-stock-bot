import os
import requests
import json
import re
import sys
from datetime import date, timedelta
from requests.auth import HTTPBasicAuth

# --- 設定區 (從環境變數讀取) ---
BASE_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS") # 這裡請放 API Token

if not BASE_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

# 確保 URL 結尾沒有斜線
BASE_URL = BASE_URL.rstrip('/')

def get_target_dates():
    """計算本週日期與檔名"""
    today = date.today()
    # 假設週報是週五出，或者您是週一跑腳本要算本週
    # 這裡沿用您之前的邏輯：找本週一到本週日
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
    # 使用 CQL (Confluence Query Language)
    cql = 'type=page AND title ~ "WeeklyReport_20" ORDER BY created DESC'
    url = f"{BASE_URL}/wiki/rest/api/content/search"
    
    params = {
        'cql': cql,
        'limit': 1,
        'expand': 'body.storage,ancestors,space' # 擴展取得內容、父頁面資訊、空間資訊
    }
    
    response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    response.raise_for_status()
    results = response.json().get('results', [])
    
    if not results:
        raise Exception("找不到任何符合 'WeeklyReport_20' 的頁面")
    
    latest = results[0]
    print(f"找到最新週報: {latest['title']} (ID: {latest['id']})")
    return latest

def create_new_report(latest_page, dates):
    """基於舊內容建立新頁面"""
    new_title = f"WeeklyReport_{dates['filename']}"
    print(f"準備建立新頁面: {new_title}")
    
    # 1. 處理內容 (Body)
    original_body = latest_page['body']['storage']['value']
    
    # 使用 Regex 替換日期
    # 尋找內容中所有的 YYYY-MM-DD 格式
    # 邏輯：找到舊的日期區間，替換成新的
    # 注意：這會替換頁面中「所有」符合格式的日期，通常這是我們想要的
    
    # 簡單暴力法：找出所有日期，排序後，假設最小的是開始日，最大的是結束日 (針對 JQL)
    # 或者直接替換 JQL 字串。Confluence 的 JQL 存在 macro 參數裡
    
    # 讓我們用更安全的方式：替換 JQL 中的日期
    # JQL 在 storage format 中通常長這樣: <ac:parameter ac:name="jql">... created >= 2026-01-05 ...</ac:parameter>
    
    def replace_dates(match):
        # 這裡單純把所有看到的日期格式，依序嘗試替換
        # 但為了保險，我們直接用新的 Monday 和 Sunday 覆蓋舊的區間
        # 假設舊內容有兩個日期，分別代表 start 和 end
        return match.group(0) # 暫時不改，下面用專用邏輯
    
    # 找出舊內容裡面的所有日期
    found_dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", original_body)
    new_body = original_body
    
    if len(found_dates) >= 2:
        # 假設前兩個日期就是 JQL 的區間 (這是基於您之前的 Selenium 邏輯)
        # 把它們替換掉
        old_start = found_dates[0]
        old_end = found_dates[1]
        
        print(f"偵測到舊日期區間: {old_start} ~ {old_end}")
        print(f"將替換為: {dates['monday_str']} ~ {dates['sunday_str']}")
        
        # 使用 replace 替換 (只替換前 1 次出現，避免改到不該改的)
        # 注意：如果 start 和 end 相同，要小心
        new_body = new_body.replace(old_start, dates['monday_str'], 1)
        new_body = new_body.replace(old_end, dates['sunday_str'], 1)
    else:
        print("警告：在舊內容中找不到足夠的日期格式，將直接複製內容而不修改日期。")

    # 2. 準備 Payload
    # 必須指定 Parent (ancestors)，否則頁面會跑去空間的根目錄
    ancestors = []
    if latest_page.get('ancestors'):
        # 通常最新的週報和舊週報會在同一個父頁面下
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
    
    # 3. 發送建立請求
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
        full_link = f"{BASE_URL}/wiki{new_page_data['_links']['webui']}"
        print(f"✅ 成功建立頁面！")
        print(f"頁面 ID: {new_page_data['id']}")
        print(f"連結: {full_link}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(f"回應內容: {response.text}")

def main():
    dates = get_target_dates()
    print(f"=== Confluence API 自動週報腳本 ===")
    print(f"目標日期: {dates['monday_str']} ~ {dates['sunday_str']}")
    
    try:
        latest_page = find_latest_report()
        create_new_report(latest_page, dates)
    except Exception as e:
        print(f"執行錯誤: {str(e)}")

if __name__ == "__main__":
    main()
