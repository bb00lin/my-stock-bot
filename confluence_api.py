import os
import requests
import json
import re
import sys
from datetime import date, timedelta
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse

# --- 設定區 ---
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

# --- 網址強力淨化 (v3.0) ---
# 強制解析出 scheme 和 netloc，捨棄所有後面的路徑
parsed = urlparse(RAW_URL)
# 確保是 https://domain.atlassian.net 這種格式
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"

print(f"原始輸入網址: {RAW_URL}")
print(f"淨化後基準網址: {BASE_URL}")

# Atlassian Cloud 標準 API 路徑
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

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
    
    url = f"{API_ENDPOINT}/search"
    
    params = {
        'cql': cql,
        'limit': 1,
        'expand': 'body.storage,ancestors,space'
    }
    
    try:
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        
        if response.status_code == 404:
            print(f"❌ 404 錯誤 - 請求網址: {response.url}")
            print("請檢查您的網域是否正確，或者該站點是否為 Cloud 版本。")
            sys.exit(1)
            
        response.raise_for_status()
        results = response.json().get('results', [])
        
        if not results:
            print("⚠️ 搜尋成功但無結果。")
            print("系統找不到任何標題包含 'WeeklyReport_20' 的頁面。")
            sys.exit(1)
        
        latest = results[0]
        print(f"✅ 找到最新週報: {latest['title']} (ID: {latest['id']})")
        return latest
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ API 請求失敗: {e}")
        if response.status_code == 401:
            print("💡 提示: 401 代表 API Token 無效或 Email 錯誤。")
            print("請確認您使用的是 'API Token' 而不是 '登入密碼'。")
        sys.exit(1)

def create_new_report(latest_page, dates):
    """基於舊內容建立新頁面"""
    new_title = f"WeeklyReport_{dates['filename']}"
    print(f"準備建立新頁面: {new_title}")
    
    original_body = latest_page['body']['storage']['value']
    
    # 日期替換邏輯
    found_dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", original_body)
    new_body = original_body
    
    if len(found_dates) >= 2:
        old_start = found_dates[0]
        old_end = found_dates[1]
        print(f"偵測到舊日期區間: {old_start} ~ {old_end}")
        new_body = new_body.replace(old_start, dates['monday_str'], 1)
        new_body = new_body.replace(old_end, dates['sunday_str'], 1)
        print(f"已替換為: {dates['monday_str']} ~ {dates['sunday_str']}")
    else:
        print("⚠️ 舊內容中找不到日期格式，直接複製內容。")

    # 準備 Payload
    ancestors = []
    if latest_page.get('ancestors'):
        ancestors.append({'id': latest_page['ancestors'][-1]['id']})
    
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
    
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(
            API_ENDPOINT, 
            auth=HTTPBasicAuth(USERNAME, API_TOKEN),
            headers=headers,
            data=json.dumps(payload)
        )
        response.raise_for_status()
        new_page_data = response.json()
        
        # 組合 WebUI 連結
        webui = new_page_data['_links']['webui']
        full_link = f"{BASE_URL}/wiki{webui}"
        
        print(f"🎉 成功建立頁面！")
        print(f"頁面 ID: {new_page_data['id']}")
        print(f"連結: {full_link}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(f"伺服器回應: {response.text}")
        if "title already exists" in response.text:
            print("💡 原因: 該標題的週報已經存在了！")

def main():
    dates = get_target_dates()
    print(f"=== Confluence API 自動週報腳本 (v3.0 強力淨化版) ===")
    print(f"目標日期: {dates['monday_str']} ~ {dates['sunday_str']}")
    
    try:
        latest_page = find_latest_report()
        create_new_report(latest_page, dates)
    except Exception as e:
        print(f"執行中斷: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
