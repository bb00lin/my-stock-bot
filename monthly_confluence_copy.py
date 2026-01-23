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
        if re.match(r'^\d{6}$', title):
            monthly_pages.append(child)
    
    if not monthly_pages:
        print("⚠️ 在 Personal Tasks 下找不到任何 YYYYMM 格式的頁面。")
        sys.exit(1)

    monthly_pages.sort(key=lambda x: x['title'], reverse=True)
    latest_page = monthly_pages[0]
    
    full_latest_page = get_page_id_by_title(latest_page['title'])
    
    print(f"📅 找到最新月份頁面: {full_latest_page['title']} (ID: {full_latest_page['id']})")
    return full_latest_page

def increment_date_match(match):
    """正則替換的回調函式：將匹配到的日期 +1 個月"""
    full_date = match.group(0) # e.g., 2025-11-01
    sep = match.group(2)       # e.g., - or /
    
    try:
        fmt = f"%Y{sep}%m{sep}%d"
        dt = datetime.strptime(full_date, fmt)
        new_dt = dt + relativedelta(months=1)
        new_date_str = new_dt.strftime(fmt)
        print(f"      👉 日期變更: {full_date} -> {new_date_str}")
        return new_date_str
    except ValueError:
        return full_date

def process_jql_content(html_content):
    """
    解析 Storage Format XML，找到 Jira Macro 的 JQL 參數並修改日期
    """
    print("🔧 正在解析頁面結構 (XML Mode)...")
    
    # 【關鍵修正】使用 'xml' 解析器 (需要 pip install lxml)
    try:
        soup = BeautifulSoup(html_content, 'xml')
    except Exception as e:
        print(f"⚠️ XML 解析失敗，嘗試退回 html.parser: {e}")
        soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. 找到所有 Jira Macro
    jira_macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "jira"})
    print(f"   🔎 頁面中發現 {len(jira_macros)} 個 Jira 表格")
    
    total_dates_modified = 0
    
    for i, macro in enumerate(jira_macros):
        # 2. 在 Macro 中找到 JQL 參數
        jql_param = macro.find('ac:parameter', attrs={"ac:name": "jql"})
        
        if jql_param:
            original_jql = jql_param.get_text() # 使用 get_text() 確保抓到內容
            
            # 簡單過濾掉空白的
            if not original_jql.strip():
                continue

            # print(f"   📄 表格[{i+1}] JQL 原文: {original_jql[:60]}...")
            
            # 3. 使用 Regex 搜尋並替換日期
            date_pattern = re.compile(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})')
            
            new_jql, count = date_pattern.subn(increment_date_match, original_jql)
            
            if count > 0:
                # 更新 BeautifulSoup 物件中的字串
                jql_param.string = new_jql
                total_dates_modified += count
            else:
                print(f"      ⚠️ 表格[{i+1}] 未發現符合格式的日期 (YYYY-MM-DD)")

    print(f"📊 總計修改了 {total_dates_modified} 個 JQL 日期")
    return str(soup)

def create_new_month_page(latest_page):
    current_title = latest_page['title']
    try:
        current_date_obj = datetime.strptime(current_title, "%Y%m")
        next_date_obj = current_date_obj + relativedelta(months=1)
        next_title = next_date_obj.strftime("%Y%m")
    except ValueError:
        print("❌ 標題日期格式錯誤")
        sys.exit(1)

    print(f"🚀 準備建立新頁面: {next_title}")

    if get_page_id_by_title(next_title):
        print(f"⚠️ 跳過：頁面 '{next_title}' 已經存在！")
        return

    original_body = latest_page['body']['storage']['value']
    new_body = process_jql_content(original_body)

    if latest_page.get('ancestors'):
        parent_id = latest_page['ancestors'][-1]['id']
    else:
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
        "version": {
            "number": 1,
            "minorEdit": True
        }
    }

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
    print(f"=== Confluence 月度 JQL 更新機器人 (v2.1 Debug版) ===")
    try:
        latest_page = find_latest_monthly_page()
        create_new_month_page(latest_page)
    except Exception as e:
        print(f"執行中斷: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
