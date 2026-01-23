import os
import requests
import json
import re
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse
from bs4 import BeautifulSoup

# ==========================================
# 1. 設定區
# ==========================================
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

# 父頁面標題
PARENT_PAGE_TITLE = "Personal Tasks"

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("❌ 錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

parsed_url = urlparse(RAW_URL)
BASE_URL = f"{parsed_url.scheme}://{parsed_url.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# ==========================================
# 2. 核心功能函式
# ==========================================

def get_page_id_by_title(title):
    """透過標題搜尋頁面 ID"""
    url = f"{API_ENDPOINT}"
    params = {'title': title, 'expand': 'body.storage,version,ancestors,space'}
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        results = r.json().get('results', [])
        if results:
            return results[0]
    except Exception as e:
        print(f"❌ 搜尋頁面 '{title}' 失敗: {e}")
    return None

def get_child_pages(parent_id):
    """取得某頁面下的所有子頁面"""
    url = f"{API_ENDPOINT}/{parent_id}/child/page"
    params = {'limit': 100, 'expand': 'version'} 
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        return r.json().get('results', [])
    except Exception as e:
        print(f"❌ 取得子頁面失敗: {e}")
        return []

def find_latest_monthly_page():
    print(f"🔍 正在搜尋父頁面: {PARENT_PAGE_TITLE}...")
    parent_page = get_page_id_by_title(PARENT_PAGE_TITLE)
    if not parent_page:
        print(f"❌ 找不到父頁面: {PARENT_PAGE_TITLE}")
        sys.exit(1)

    parent_id = parent_page['id']
    print(f"✅ 找到父頁面 ID: {parent_id}")

    children = get_child_pages(parent_id)
    monthly_pages = []
    
    for child in children:
        title = child['title']
        # 嚴格匹配 6 位數字 (YYYYMM)
        if re.match(r'^\d{6}$', title):
            monthly_pages.append(child)
    
    if not monthly_pages:
        print("⚠️ 在 Personal Tasks 下找不到任何 YYYYMM 格式的頁面。")
        sys.exit(1)

    # 排序並取最新
    monthly_pages.sort(key=lambda x: x['title'], reverse=True)
    latest_page = monthly_pages[0]
    
    # 重新讀取完整內容 (含 body.storage)
    full_latest_page = get_page_id_by_title(latest_page['title'])
    
    print(f"📅 找到最新月份頁面: {full_latest_page['title']} (ID: {full_latest_page['id']})")
    return full_latest_page

def increment_date_match(match):
    """正則替換的回調函式：將匹配到的日期 +1 個月"""
    full_date = match.group(0) # e.g., 2025-11-01
    sep = match.group(2)       # e.g., - or /
    
    try:
        # 嘗試解析 YYYY-MM-DD 或 YYYY/MM/DD
        fmt = f"%Y{sep}%m{sep}%d"
        dt = datetime.strptime(full_date, fmt)
        
        # 加一個月
        new_dt = dt + relativedelta(months=1)
        
        # 轉回字串
        new_date_str = new_dt.strftime(fmt)
        # print(f"   Debug: 日期變更 {full_date} -> {new_date_str}")
        return new_date_str
    except ValueError:
        return full_date

def process_jql_content(html_content):
    """
    解析 Storage Format XML，找到 Jira Macro 的 JQL 參數並修改日期
    """
    print("🔧 正在解析頁面結構並修改 JQL...")
    
    # 使用 lxml-xml 或 html.parser 解析 Confluence Storage Format
    # Confluence 儲存格式其實是 XHTML/XML
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. 找到所有 Jira Macro
    # 在 Storage Format 中，標籤通常是 <ac:structured-macro ac:name="jira">
    jira_macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "jira"})
    
    total_dates_modified = 0
    
    for macro in jira_macros:
        # 2. 在 Macro 中找到 JQL 參數
        # <ac:parameter ac:name="jql">project = ...</ac:parameter>
        jql_param = macro.find('ac:parameter', attrs={"ac:name": "jql"})
        
        if jql_param and jql_param.string:
            original_jql = jql_param.string
            
            # 3. 使用 Regex 搜尋並替換日期
            # 匹配格式: 2025-11-01 或 2025/11/01
            date_pattern = re.compile(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})')
            
            new_jql, count = date_pattern.subn(increment_date_match, original_jql)
            
            if count > 0:
                print(f"   🔄 發現 JQL: {original_jql[:50]}...")
                print(f"      修改後: {new_jql[:50]}...")
                # 更新 BeautifulSoup 物件中的字串
                jql_param.string.replace_with(new_jql)
                total_dates_modified += count

    print(f"📊 總計修改了 {total_dates_modified} 個 JQL 日期")
    return str(soup)

def create_new_month_page(latest_page):
    # 1. 計算新標題
    current_title = latest_page['title']
    try:
        current_date_obj = datetime.strptime(current_title, "%Y%m")
        next_date_obj = current_date_obj + relativedelta(months=1)
        next_title = next_date_obj.strftime("%Y%m")
    except ValueError:
        print("❌ 標題日期格式錯誤")
        sys.exit(1)

    print(f"🚀 準備建立新頁面: {next_title}")

    # 2. 檢查是否已存在
    if get_page_id_by_title(next_title):
        print(f"⚠️ 跳過：頁面 '{next_title}' 已經存在！")
        return

    # 3. 處理內容
    original_body = latest_page['body']['storage']['value']
    new_body = process_jql_content(original_body)

    # 4. 準備建立 (含不通知設定)
    # 取得父層 ID
    if latest_page.get('ancestors'):
        parent_id = latest_page['ancestors'][-1]['id']
    else:
        # Fallback
        p_page = get_page_id_by_title(PARENT_PAGE_TITLE)
        parent_id = p_page['id']

    payload = {
        "type": "page",
        "title": next_title,
        "ancestors": [{"id": parent_id}],
        "space": {"key": latest_page['space']['key']},
        "body": {
            "storage": {
                "value": new_body,
                "representation": "storage"
            }
        },
        # 嘗試使用 version.minorEdit 來減少通知 (雖主要用於更新，但建議加上)
        "version": {
            "number": 1,
            "minorEdit": True
        },
        # Confluence Cloud 有時支援 status="current" 避免發送通知草稿，但這裡是直接發佈
        "status": "current"
    }

    # 5. 發送請求
    try:
        response = requests.post(
            API_ENDPOINT, 
            auth=HTTPBasicAuth(USERNAME, API_TOKEN),
            headers=get_headers(),
            data=json.dumps(payload)
        )
        response.raise_for_status()
        
        data = response.json()
        base_url = BASE_URL.rstrip('/')
        link_suffix = data['_links']['webui']
        full_link = f"{base_url}/wiki{link_suffix}" if not link_suffix.startswith('/wiki') else f"{base_url}{link_suffix}"
        
        print(f"🎉 成功建立！連結: {full_link}")

    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(f"錯誤回應: {response.text}")
        sys.exit(1)

def main():
    print(f"=== Confluence 月度 JQL 更新機器人 (v2.0) ===")
    try:
        latest_page = find_latest_monthly_page()
        create_new_month_page(latest_page)
    except Exception as e:
        print(f"執行中斷: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
