import os
import requests
import json
import re
import sys
from datetime import datetime, timedelta
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
    # 搜尋標題包含 "WeeklyReport" 的頁面，按建立時間倒序
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    
    url = f"{API_ENDPOINT}/search"
    params = {'cql': cql, 'limit': 1, 'expand': 'body.storage,ancestors,space'}
    
    try:
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        response.raise_for_status()
        results = response.json().get('results', [])
        
        if not results:
            print("⚠️ 找不到任何基準週報，無法推算下一期。")
            sys.exit(1)
        
        latest = results[0]
        print(f"✅ 找到基準週報: {latest['title']} (ID: {latest['id']})")
        return latest
    except Exception as e:
        print(f"❌ 搜尋失敗: {e}")
        sys.exit(1)

def calculate_next_date(latest_title):
    """
    從最新週報的標題解析日期，並推算下週五
    例如: WeeklyReport_20260123 -> 下一期 20260130
    """
    # 嘗試從標題抓取 8 碼數字
    match = re.search(r"(\d{8})", latest_title)
    if match:
        last_date_str = match.group(1)
        try:
            last_date = datetime.strptime(last_date_str, "%Y%m%d").date()
            
            # 邏輯：下一期 = 基準日 + 7天
            next_date = last_date + timedelta(days=7)
            
            # 計算該週的週一與週日 (用於 JQL 替換)
            # next_date 是週五
            monday = next_date - timedelta(days=4)
            sunday = next_date + timedelta(days=2)
            
            return {
                "filename": next_date.strftime("%Y%m%d"),
                "monday_str": monday.strftime("%Y-%m-%d"),
                "sunday_str": sunday.strftime("%Y-%m-%d")
            }
        except ValueError:
            pass
            
    # 如果標題無法解析，就退回使用「本週五」
    print("⚠️ 無法從標題解析日期，將使用本週日期作為基準。")
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    friday = monday + timedelta(days=4)
    return {
        "filename": friday.strftime("%Y%m%d"),
        "monday_str": monday.strftime("%Y-%m-%d"),
        "sunday_str": sunday.strftime("%Y-%m-%d")
    }

def create_new_report(latest_page):
    # 1. 計算下一期日期
    next_dates = calculate_next_date(latest_page['title'])
    new_title = f"WeeklyReport_{next_dates['filename']}"
    print(f"準備建立下一期週報: {new_title}")
    print(f"新週期區間: {next_dates['monday_str']} ~ {next_dates['sunday_str']}")
    
    # 2. 檢查是否已存在 (雙重確認)
    check_url = f"{API_ENDPOINT}/search"
    check_params = {'cql': f'title = "{new_title}"'}
    check_resp = requests.get(check_url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=check_params)
    if check_resp.json().get('results'):
        print(f"⚠️ 跳過：頁面 '{new_title}' 已經存在！")
        return

    # 3. 處理內容
    original_body = latest_page['body']['storage']['value']
    new_body = original_body
    
    found_dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", original_body)
    if len(found_dates) >= 2:
        old_start, old_end = found_dates[0], found_dates[1]
        print(f"替換 JQL 日期: {old_start} -> {next_dates['monday_str']}")
        
        new_body = new_body.replace(old_start, next_dates['monday_str'], 1)
        new_body = new_body.replace(old_end, next_dates['sunday_str'], 1)
    
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
    print(f"=== Confluence API 自動週報 (v5.0 智慧遞增版) ===")
    latest_page = find_latest_report()
    create_new_report(latest_page)

if __name__ == "__main__":
    main()
