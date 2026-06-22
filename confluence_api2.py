import os
import requests
import json
import re
import sys
from datetime import datetime, timedelta, date
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse
from bs4 import BeautifulSoup  # ✅ 載入 BeautifulSoup 用來清洗 HTML

# --- 設定區 ---
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("❌ 錯誤：缺少環境變數 (請確認已設定 CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def find_latest_report():
    print("🔍 正在搜尋最新週報...")
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

def calculate_next_filename(latest_title):
    """
    從標題解析日期，並推算下週五的檔名 (YYYYMMDD)
    """
    match = re.search(r"(\d{8})", latest_title)
    if match:
        last_date_str = match.group(1)
        try:
            last_date_obj = datetime.strptime(last_date_str, "%Y%m%d").date()
            next_date = last_date_obj + timedelta(days=7)
            return next_date.strftime("%Y%m%d")
        except ValueError: 
            pass
            
    print("⚠️ 無法解析標題日期，使用本週五為基準。")
    today = datetime.now().date()
    friday = today + timedelta(days=(4 - today.weekday()))
    return friday.strftime("%Y%m%d")

def create_new_report(latest_page):
    # 1. 計算新檔名
    next_filename = calculate_next_filename(latest_page['title'])
    new_title = f"WeeklyReport_{next_filename}"
    print(f"📄 準備建立新頁面: {new_title}")
    
    # 2. 檢查重複
    check_url = f"{API_ENDPOINT}/search"
    check_params = {'cql': f'title = "{new_title}"'}
    check_resp = requests.get(check_url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=check_params)
    if check_resp.json().get('results'):
        print(f"⚠️ 跳過：頁面 '{new_title}' 已經存在！")
        return

    # ==========================================
    # 🌟 方案 B 核心：保留原排版，但強制清洗所有 Jira 任務與舊日誌
    # ==========================================
    original_body = latest_page['body']['storage']['value']
    soup = BeautifulSoup(original_body, 'html.parser')
    
    print("🧹 正在清洗版面，拔除舊有 Jira 任務與日誌區塊...")
    
    # 動作一：拔除所有 Jira 原生巨集 (避免 Jira 系統雙向連動)
    for macro in soup.find_all('ac:structured-macro', attrs={'ac:name': 'jira'}):
        macro.extract()
        
    # 動作二：拔除所有手動貼上的純 Jira 網址超連結 (只要網址包含 /browse/ 就拔除)
    for a_tag in soup.find_all('a'):
        href = a_tag.get('href', '')
        if '/browse/' in href:
            a_tag.extract()
            
    # 動作三：順手拔除我們日誌產生器塞進去的 daily-worklog- 區塊 (保持新一週的乾淨)
    for div in soup.find_all('div'):
        classes = div.get('class', [])
        if any(cls.startswith('daily-worklog-') for cls in classes):
            div.extract()

    # 將清洗後的 HTML 轉回字串
    new_body = str(soup)
    
    # 4. 準備建立頁面的層級與空間資料
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
    
    # 5. 發送請求建立頁面
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
        
        print(f"🎉 成功建立新週報！(已保留原版面，並清除所有 Jira 任務)")
        print(f"🌐 頁面連結: {link}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(response.text)
        sys.exit(1)

def main():
    print(f"=== Confluence API 自動週報 (版面保留清洗版) ===")
    try:
        latest_page = find_latest_report()
        create_new_report(latest_page)
    except Exception as e:
        print(f"執行中斷: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
