import os
import requests
import json
import re
import sys
import html
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
    print("❌ 錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
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
    """透過 ID 取得完整頁面內容"""
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version,ancestors,space'}
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ 讀取頁面失敗 (ID: {page_id}): {e}")
    return None

def get_page_id_by_title(title):
    """透過標題搜尋 ID"""
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
    """取得所有子頁面"""
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
    latest_basic_info = monthly_pages[0]
    
    print(f"📅 找到最新月份標題: {latest_basic_info['title']} (ID: {latest_basic_info['id']})")
    
    # 使用 ID 獲取完整內容
    full_page = get_page_by_id(latest_basic_info['id'])
    return full_page

# ------------------------------------------
# 日期處理邏輯 (YYYY-MM-DD)
# ------------------------------------------
def increment_date_match(match):
    full_date = match.group(0)
    sep = match.group(2)
    try:
        fmt = f"%Y{sep}%m{sep}%d"
        dt = datetime.strptime(full_date, fmt)
        new_dt = dt + relativedelta(months=1)
        return new_dt.strftime(fmt)
    except ValueError:
        return full_date

# ------------------------------------------
# 【新增】NPI 標籤處理邏輯 (NPI_YYYYMM)
# ------------------------------------------
def increment_npi_match(match):
    prefix = match.group(1) # "NPI_"
    date_str = match.group(2) # "202512"
    try:
        # 解析 YYYYMM
        dt = datetime.strptime(date_str, "%Y%m")
        # 加一個月
        new_dt = dt + relativedelta(months=1)
        # 格式化回 YYYYMM
        new_date_str = new_dt.strftime("%Y%m")
        
        result = f"{prefix}{new_date_str}"
        # print(f"      👉 NPI更新: {match.group(0)} -> {result}")
        return result
    except ValueError:
        return match.group(0)

def process_content_all(html_content):
    """
    執行所有的內容替換邏輯：
    1. 日期格式 (2025-12-01)
    2. NPI 標籤 (NPI_202512)
    """
    print("🔧 正在處理內容 (包含日期與 NPI 標籤)...")
    
    # --- 1. 處理標準日期 (YYYY-MM-DD 或 YYYY/MM/DD) ---
    date_pattern = re.compile(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})')
    content_v1, count_date = date_pattern.subn(increment_date_match, html_content)
    
    # --- 2. 處理 NPI 標籤 (NPI_YYYYMM) ---
    # Regex 說明: (NPI_) 接 6位數字
    npi_pattern = re.compile(r'(NPI_)(\d{6})')
    content_final, count_npi = npi_pattern.subn(increment_npi_match, content_v1)
    
    print(f"📊 處理報告:")
    print(f"   - 修改了 {count_date} 個標準日期 (如 2025-12-01)")
    print(f"   - 修改了 {count_npi} 個 NPI 標籤 (如 NPI_202512)")
    
    if count_date == 0 and count_npi == 0:
        print("⚠️ 警告：沒有發現任何需修改的日期或標籤。")
    
    return content_final

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

    # 取得原始內容
    original_body = latest_page['body']['storage']['value']
    
    # 執行所有替換
    new_body = process_content_all(original_body)

    # 取得父層 ID
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
        # 【不通知追蹤者設定】
        "version": {
            "number": 1,
            "minorEdit": True
        },
        "status": "current"
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
    print(f"=== Confluence 月度 JQL 更新機器人 (v6.0 NPI支援版) ===")
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
