import os
import requests
import json
import re
import sys
from datetime import datetime, timedelta, date
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse

# --- 設定區 ---
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數")
    sys.exit(1)

parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def find_latest_report():
    print("正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    url = f"{API_ENDPOINT}/search"
    params = {'cql': cql, 'limit': 1, 'expand': 'body.storage,ancestors,space'}
    
    try:
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        response.raise_for_status()
        results = response.json().get('results', [])
        if not results:
            print("⚠️ 找不到任何基準週報。")
            sys.exit(1)
        latest = results[0]
        print(f"✅ 找到基準週報: {latest['title']} (ID: {latest['id']})")
        return latest
    except Exception as e:
        print(f"❌ 搜尋失敗: {e}")
        sys.exit(1)

def calculate_next_date(latest_title):
    """
    計算下一期的檔名與日期區間
    """
    match = re.search(r"(\d{8})", latest_title)
    # 預設使用本週作為備案
    today = datetime.now().date()
    friday = today + timedelta(days=(4 - today.weekday()))
    
    if match:
        last_date_str = match.group(1)
        try:
            last_date_obj = datetime.strptime(last_date_str, "%Y%m%d").date()
            friday = last_date_obj + timedelta(days=7)
        except ValueError: pass
    
    # 根據新的週五 (檔名)，推算該週的週一與週日
    # 週報檔名通常是週五，所以週一 = 週五 - 4天
    target_monday = friday - timedelta(days=4)
    target_sunday = friday + timedelta(days=2)
    
    return {
        "filename": friday.strftime("%Y%m%d"),
        "monday": target_monday,
        "sunday": target_sunday
    }

def update_jql_dates_smart(content, new_monday_obj, new_sunday_obj):
    """
    v7.0 核心修正：不依賴特定語法 (如 updated >=)，而是直接針對日期字串進行替換。
    能夠處理 "2026-1-19" (單碼) 與 "2026-01-19" (雙碼) 的差異。
    """
    print(f"正在執行智慧日期替換...")
    
    # 1. 找出內容中所有看起來像日期的字串 (YYYY-M-D 或 YYYY-MM-DD)
    # Regex 解釋: 4位數字 - 1到2位數字 - 1到2位數字
    date_pattern = re.compile(r'(\d{4})-(\d{1,2})-(\d{1,2})')
    
    # 2. 分析這些日期，找出哪些是「舊週一」，哪些是「舊週日」
    # 我們假設舊週報裡的日期，大部分都落在「上一週」的區間內
    # 邏輯：
    #   舊週一應該是: new_monday - 7 days
    #   舊週日應該是: new_sunday - 7 days
    
    old_monday_target = new_monday_obj - timedelta(days=7)
    old_sunday_target = new_sunday_obj - timedelta(days=7)
    
    print(f"目標：將 {old_monday_target} 附近的日期換成 {new_monday_obj}")
    print(f"目標：將 {old_sunday_target} 附近的日期換成 {new_sunday_obj}")
    
    def replace_callback(match):
        full_str = match.group(0) # 例如 "2026-1-19" 或 "2026-01-19"
        
        try:
            # 嘗試解析這個日期
            found_date = datetime.strptime(full_str, "%Y-%m-%d" if "-" in full_str else "%Y%m%d").date()
            
            # 判斷這個日期是不是「舊週一」 (允許前後 1 天的誤差，以防萬一)
            if abs((found_date - old_monday_target).days) <= 1:
                # 替換成新週一 (保持原本格式嗎？不，統一改成標準格式 YYYY-MM-DD 最保險)
                return new_monday_obj.strftime("%Y-%m-%d")
            
            # 判斷這個日期是不是「舊週日」
            if abs((found_date - old_sunday_target).days) <= 1:
                return new_sunday_obj.strftime("%Y-%m-%d")
                
        except ValueError:
            pass
            
        return full_str # 如果不符合條件，保持原樣

    # 3. 執行全域替換
    new_content = date_pattern.sub(replace_callback, content)
    
    return new_content

def create_new_report(latest_page):
    # 1. 計算日期
    next_dates = calculate_next_date(latest_page['title'])
    new_title = f"WeeklyReport_{next_dates['filename']}"
    print(f"準備建立: {new_title}")
    print(f"新週期: {next_dates['monday']} ~ {next_dates['sunday']}")
    
    # 2. 檢查重複
    check_url = f"{API_ENDPOINT}/search"
    check_params = {'cql': f'title = "{new_title}"'}
    check_resp = requests.get(check_url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=check_params)
    if check_resp.json().get('results'):
        print(f"⚠️ 跳過：頁面 '{new_title}' 已經存在！")
        return

    # 3. 處理內容
    original_body = latest_page['body']['storage']['value']
    
    # 使用 v7.0 的智慧替換函數
    new_body = update_jql_dates_smart(
        original_body, 
        next_dates['monday'], 
        next_dates['sunday']
    )
    
    # 4. 建立頁面
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
        link = f"{BASE_URL}/wiki{webui}" if not webui.startswith('/wiki') else f"{BASE_URL}{webui}"
        
        print(f"🎉 成功建立！連結: {link}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(response.text)

def main():
    print(f"=== Confluence API 自動週報 (v7.0 智慧日期替換版) ===")
    latest_page = find_latest_report()
    create_new_report(latest_page)

if __name__ == "__main__":
    main()
