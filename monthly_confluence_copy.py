import os
import requests
import json
import re
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse

# ==========================================
# 1. 設定區
# ==========================================
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")
PARENT_PAGE_TITLE = "Personal Tasks"

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("❌ 錯誤：缺少環境變數")
    sys.exit(1)

parsed_url = urlparse(RAW_URL)
BASE_URL = f"{parsed_url.scheme}://{parsed_url.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# ==========================================
# 2. 核心功能
# ==========================================

def get_page_by_id(page_id):
    """直接透過 ID 取得頁面內容 (最準確)"""
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version,ancestors,space'}
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ 讀取頁面(ID: {page_id})失敗: {e}")
    return None

def get_page_id_by_title(title):
    """透過標題搜尋 (僅用於找父頁面)"""
    url = f"{API_ENDPOINT}"
    params = {'title': title, 'expand': 'body.storage,version,ancestors'}
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        results = r.json().get('results', [])
        if results: return results[0]
    except Exception as e:
        print(f"❌ 搜尋頁面 '{title}' 失敗: {e}")
    return None

def get_child_pages(parent_id):
    """取得子頁面列表"""
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

    # 排序取最新
    monthly_pages.sort(key=lambda x: x['title'], reverse=True)
    latest_basic_info = monthly_pages[0]
    
    print(f"📅 找到最新月份標題: {latest_basic_info['title']} (ID: {latest_basic_info['id']})")
    
    # 【關鍵修正】: 直接用 ID 抓取完整內容，而不是用標題搜尋 (避免抓到同名頁面)
    full_page = get_page_by_id(latest_basic_info['id'])
    
    return full_page

def increment_date_match(match):
    """正則替換: 日期 + 1個月"""
    full_date = match.group(0)
    sep = match.group(2)
    try:
        fmt = f"%Y{sep}%m{sep}%d"
        dt = datetime.strptime(full_date, fmt)
        new_dt = dt + relativedelta(months=1)
        new_str = new_dt.strftime(fmt)
        # print(f"   Debug: {full_date} -> {new_str}")
        return new_str
    except ValueError:
        return full_date

def process_jql_content_robust(html_content):
    """
    使用純文字暴力替換模式 (最穩健，不依賴 XML 解析結構)
    """
    print("🔧 正在處理內容 (Regex Mode)...")
    
    # 診斷：印出前 300 個字元確認抓對內容
    print(f"   👀 內容預覽 (前300字): {html_content[:300]}...")
    
    # 針對 JQL 中的日期格式 YYYY-MM-DD 或 YYYY/MM/DD
    # 格式: 4位數字 + 分隔符 + 1或2位數字 + 相同分隔符 + 1或2位數字
    date_pattern = re.compile(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})')
    
    # 執行替換
    new_content, count = date_pattern.subn(increment_date_match, html_content)
    
    print(f"📊 總計修改了 {count} 個日期")
    
    if count == 0:
        print("⚠️ 警告：沒有發現任何符合格式的日期。請確認來源頁面是否包含 JQL 表格。")
    
    return new_content

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

    # 檢查是否已存在 (避免重複建立)
    if get_page_id_by_title(next_title):
        print(f"⚠️ 跳過：頁面 '{next_title}' 已經存在！")
        return

    # 處理內容
    original_body = latest_page['body']['storage']['value']
    new_body = process_jql_content_robust(original_body)

    # 準備建立
    # 優先使用原頁面的 parent ID
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
    print(f"=== Confluence 月度 JQL 更新機器人 (v4.0 ID鎖定版) ===")
    try:
        latest_page = find_latest_monthly_page()
        if latest_page:
            create_new_month_page(latest_page)
        else:
            print("❌ 無法取得來源頁面資料")
    except Exception as e:
        print(f"執行中斷: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
