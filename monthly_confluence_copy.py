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
# 1. 設定區 (使用與原腳本相同的環境變數)
# ==========================================
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

# 父頁面標題，用來定位基準點
PARENT_PAGE_TITLE = "Personal Tasks"

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("❌ 錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

# 處理 URL
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
    """
    1. 找到 'Personal Tasks'
    2. 找到底下格式為 YYYYMM 的子頁面
    3. 回傳月份最大的一個
    """
    print(f"正在搜尋父頁面: {PARENT_PAGE_TITLE}...")
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
        # 檢查是否為 6 位數字 (例如 202512)
        if re.match(r'^\d{6}$', title):
            monthly_pages.append(child)
    
    if not monthly_pages:
        print("⚠️ 在 Personal Tasks 下找不到任何 YYYYMM 格式的頁面。")
        sys.exit(1)

    # 排序找到最新的月份
    monthly_pages.sort(key=lambda x: x['title'], reverse=True)
    latest_page = monthly_pages[0]
    
    # 這裡我們需要重新取得一次 latest_page 的詳細內容 (包含 body.storage)，因為 child API 給的資訊較少
    full_latest_page = get_page_id_by_title(latest_page['title'])
    
    print(f"📅 找到最新月份頁面: {full_latest_page['title']} (ID: {full_latest_page['id']})")
    return full_latest_page

def increment_date_in_text(text):
    """
    將文字中的日期 (YYYY-MM-DD 或 YYYY/MM/DD) 加 1 個月
    """
    date_pattern = re.compile(r'(\d{4})([-/])(\d{1,2})([-/])(\d{1,2})')

    def replace_date(match):
        year, sep1, month, sep2, day = match.groups()
        try:
            current_date = datetime(int(year), int(month), int(day))
            new_date = current_date + relativedelta(months=1)
            # 保持原始分隔符號
            return f"{new_date.year}{sep1}{new_date.month:02d}{sep2}{new_date.day:02d}"
        except ValueError:
            return match.group(0)

    return date_pattern.sub(replace_date, text)

def process_jql_content(html_content):
    """
    解析 HTML，只修改 Jira Macro (JQL) 中的日期
    """
    print("正在處理 JQL 日期遞增...")
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 找到所有 Jira Macro
    jira_macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "jira"})
    modified_count = 0
    
    for macro in jira_macros:
        jql_param = macro.find('ac:parameter', attrs={"ac:name": "jql"})
        if jql_param and jql_param.string:
            original_jql = jql_param.string
            new_jql = increment_date_in_text(original_jql)
            
            if original_jql != new_jql:
                # 注意：BeautifulSoup 修改 string 的方式
                jql_param.string.replace_with(new_jql)
                modified_count += 1
                # print(f"   Debug: {original_jql} -> {new_jql}")

    print(f"📊 共修改了 {modified_count} 個 JQL 日期")
    return str(soup)

def create_new_month_page(latest_page):
    # 1. 計算新標題 (月份+1)
    current_title = latest_page['title']
    try:
        current_date_obj = datetime.strptime(current_title, "%Y%m")
        next_date_obj = current_date_obj + relativedelta(months=1)
        next_title = next_date_obj.strftime("%Y%m")
    except ValueError:
        print("❌ 標題日期格式解析錯誤，無法計算下個月。")
        sys.exit(1)

    print(f"🚀 目標建立新頁面: {next_title}")

    # 2. 檢查重複
    if get_page_id_by_title(next_title):
        print(f"⚠️ 跳過：頁面 '{next_title}' 已經存在！")
        return

    # 3. 處理內容
    original_body = latest_page['body']['storage']['value']
    new_body = process_jql_content(original_body)

    # 4. 準備 Payload
    # 取得 parent_id (Personal Tasks 的 ID)
    # latest_page['ancestors'] 列表的最後一個通常是直接父層
    if latest_page.get('ancestors'):
        parent_id = latest_page['ancestors'][-1]['id']
    else:
        # 如果取不到 ancestor，重新查詢 Personal Tasks 的 ID
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
        # 不通知追蹤者設定
        "version": {
            "number": 1,
            "minorEdit": True 
        }
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
        webui = data['_links']['webui']
        # 處理連結格式 (有時 API 回傳的 webui 包含 base url，有時不含)
        if webui.startswith('http'):
            link = webui
        else:
            link = f"{BASE_URL}/wiki{webui}" if not webui.startswith('/wiki') else f"{BASE_URL}{webui}"
        
        print(f"🎉 成功建立！連結: {link}")

    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(response.text)
        sys.exit(1)

def main():
    print(f"=== Confluence 月度任務自動化 (v1.0) ===")
    try:
        # 1. 找到最新的月份頁面
        latest_page = find_latest_monthly_page()
        # 2. 建立下個月的頁面 (含內容修改)
        create_new_month_page(latest_page)
    except Exception as e:
        print(f"執行中斷: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
