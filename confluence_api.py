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
    print("錯誤：缺少環境變數")
    sys.exit(1)

# 網址淨化
parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_target_dates():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    friday = monday + timedelta(days=4)
    return {
        "monday_str": monday.strftime("%Y-%m-%d"),
        "sunday_str": sunday.strftime("%Y-%m-%d"),
        "filename": friday.strftime("%Y%m%d")
    }

def debug_permissions():
    """當找不到週報時，執行此診斷：查看帳號到底看得到什麼"""
    print("\n=== 啟動權限診斷模式 ===")
    print(f"正在檢查帳號 {USERNAME} 能看到的所有空間與頁面...")
    
    # 嘗試列出任意頁面 (不限標題)
    url = f"{API_ENDPOINT}/search"
    params = {'cql': 'type=page', 'limit': 5}
    
    try:
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        results = response.json().get('results', [])
        
        if not results:
            print("😱 嚴重警告：API 回傳 0 個頁面。")
            print("這代表此 API Token 的帳號可能沒有任何空間的檢視權限。")
            print("請確認：您是否已將此帳號加入 Confluence 的存取權限群組？")
        else:
            print(f"✅ 帳號權限正常，能看到 {len(results)} 個頁面，例如：")
            for page in results:
                print(f" - {page['title']} (Space: {page.get('space', {}).get('name', 'Unknown')})")
            print("結論：權限沒問題，是搜尋關鍵字 'WeeklyReport' 有誤，或該空間未開放給此帳號。")
            
    except Exception as e:
        print(f"診斷失敗: {e}")

def find_latest_report():
    print("正在搜尋最新週報...")
    
    # 【修正點 1】使用萬用字元 *，並放寬搜尋條件
    # 搜尋標題包含 "WeeklyReport" 開頭的所有頁面
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    
    url = f"{API_ENDPOINT}/search"
    params = {
        'cql': cql,
        'limit': 1,
        'expand': 'body.storage,ancestors,space,version'
    }
    
    try:
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        response.raise_for_status()
        results = response.json().get('results', [])
        
        if not results:
            print("⚠️ 搜尋無結果 (WeeklyReport*)。")
            # 執行診斷
            debug_permissions()
            sys.exit(1)
        
        latest = results[0]
        print(f"✅ 找到最新週報: {latest['title']} (ID: {latest['id']})")
        print(f"   位於空間: {latest['space']['name']} (Key: {latest['space']['key']})")
        return latest
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ API 請求失敗: {e}")
        sys.exit(1)

def create_new_report(latest_page, dates):
    new_title = f"WeeklyReport_{dates['filename']}"
    print(f"\n準備建立新頁面: {new_title}")
    
    # 檢查是否已存在
    check_url = f"{API_ENDPOINT}/search"
    check_params = {'cql': f'title = "{new_title}"'}
    check_resp = requests.get(check_url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=check_params)
    if check_resp.json().get('results'):
        print(f"⚠️ 跳過：頁面 '{new_title}' 已經存在！")
        return

    original_body = latest_page['body']['storage']['value']
    
    # --- 日期替換 (針對 JQL) ---
    # JQL 在 storage format 中通常是被編碼的，例如：created >= "2024-01-01"
    # 我們嘗試用 Regex 替換所有 YYYY-MM-DD
    
    new_body = original_body
    found_dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", original_body)
    
    if len(found_dates) >= 2:
        # 假設前兩個日期是 JQL 區間
        # 這裡做一個簡單的優化：確保我們替換的是看起來像 JQL 的部分
        # 或者直接替換前兩個發現的日期
        old_start, old_end = found_dates[0], found_dates[1]
        print(f"將日期 {old_start} -> {dates['monday_str']}")
        print(f"將日期 {old_end}   -> {dates['sunday_str']}")
        
        new_body = new_body.replace(old_start, dates['monday_str'], 1)
        new_body = new_body.replace(old_end, dates['sunday_str'], 1)
    else:
        print("ℹ️ 內文無日期格式，將直接複製內容。")

    # --- 準備建立 ---
    ancestors = []
    if latest_page.get('ancestors'):
        ancestors.append({'id': latest_page['ancestors'][-1]['id']})
    
    payload = {
        "title": new_title,
        "type": "page",
        "space": {"key": latest_page['space']['key']},
        "ancestors": ancestors,
        "body": {
            "storage": {
                "value": new_body,
                "representation": "storage"
            }
        }
    }
    
    try:
        response = requests.post(
            API_ENDPOINT, 
            auth=HTTPBasicAuth(USERNAME, API_TOKEN),
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload)
        )
        response.raise_for_status()
        data = response.json()
        
        webui = data['_links']['webui']
        full_link = f"{BASE_URL}/wiki{webui}" if not webui.startswith('/wiki') else f"{BASE_URL}{webui}"
        
        print(f"🎉 成功建立！連結: {full_link}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(f"回應: {response.text}")

def main():
    dates = get_target_dates()
    print(f"=== Confluence API 自動週報 (v4.0 萬用字元版) ===")
    print(f"目標: {dates['filename']} ({dates['monday_str']} ~ {dates['sunday_str']})")
    find_latest_report_page = find_latest_report()
    create_new_report(find_latest_report_page, dates)

if __name__ == "__main__":
    main()
