import os
import requests
import json
import re
import sys
import copy
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup, Tag, NavigableString

# --- 設定區 ---
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")
KEEP_LIMIT = 5  # 保留最新的幾筆資料

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# --- 1. 搜尋週報與專案連結 (維持不變) ---
def find_latest_report():
    print("正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    url = f"{API_ENDPOINT}/search"
    params = {'cql': cql, 'limit': 1, 'expand': 'body.view'}
    response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    response.raise_for_status()
    results = response.json().get('results', [])
    if not results:
        print("⚠️ 找不到週報")
        sys.exit(1)
    return results[0]

def extract_first_project_link(report_body):
    soup = BeautifulSoup(report_body, 'html.parser')
    tables = soup.find_all('table')
    for table in tables:
        headers = []
        header_row = table.find('tr')
        if not header_row: continue
        for cell in header_row.find_all(['th', 'td']):
            headers.append(cell.get_text().strip())
        
        if "Project" in headers:
            proj_idx = headers.index("Project")
            rows = table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) > proj_idx:
                    link_tag = cols[proj_idx].find('a')
                    if link_tag:
                        page_id = link_tag.get('data-linked-resource-id')
                        if page_id:
                            print(f"🎯 鎖定目標 (透過 data-id): {page_id}")
                            return {'id': page_id}
                        href = link_tag.get('href', '')
                        if 'pageId=' in href:
                            qs = parse_qs(urlparse(href).query)
                            if 'pageId' in qs: return {'id': qs['pageId'][0]}
                        match = re.search(r'/pages/(\d+)/', href)
                        if match: return {'id': match.group(1)}
                        title = link_tag.get_text().strip()
                        print(f"⚠️ 警告：無法解析 ID，使用標題: {title}")
                        return {'title': title}
    print("⚠️ 找不到 Project 連結")
    return None

def get_page_by_id(page_id):
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version'}
    resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    if resp.status_code == 200: return resp.json()
    return None

def get_page_by_title(title):
    url = f"{API_ENDPOINT}"
    params = {'title': title, 'expand': 'body.storage,version'}
    resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    results = resp.json().get('results', [])
    if results: return results[0]
    if not title.startswith("WeeklyStatus_"):
        alt_title = f"WeeklyStatus_{title}"
        print(f"   嘗試猜測標題: {alt_title}")
        params['title'] = alt_title
        resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        results = resp.json().get('results', [])
        if results: 
            print("   ✅ 猜測成功！")
            return results[0]
    return None

# --- 2. 內容處理邏輯 ---

def is_date_header(text):
    """檢查文字是否包含日期格式 [YYYY/MM/DD]"""
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text))

def has_red_text(tag):
    """檢查這個標籤是否有紅字"""
    if not isinstance(tag, Tag): return False
    if tag.has_attr('style'):
        style = tag['style'].lower()
        if 'rgb(255, 0, 0)' in style or '#ff0000' in style or 'red' in style: return True
    if tag.name == 'font' and (tag.get('color') == 'red' or tag.get('color') == '#ff0000'): return True
    for child in tag.descendants:
        if isinstance(child, Tag):
            if child.has_attr('style'):
                style = child['style'].lower()
                if 'rgb(255, 0, 0)' in style or '#ff0000' in style: return True
            if child.name == 'font' and (child.get('color') == 'red' or child.get('color') == '#ff0000'): return True
    return False

def split_cell_content(cell_soup):
    """將格子內的內容切分成 Entry"""
    entries = []
    current_entry = []
    for child in cell_soup.contents:
        if isinstance(child, NavigableString) and not child.strip():
            if current_entry: current_entry.append(child)
            continue
        text = child.get_text() if isinstance(child, Tag) else str(child)
        if is_date_header(text):
            if current_entry: entries.append(current_entry)
            current_entry = [child]
        else:
            current_entry.append(child)
    if current_entry: entries.append(current_entry)
    return entries

def check_entry_red(entry_nodes):
    """檢查 Entry 是否有紅字"""
    for node in entry_nodes:
        if isinstance(node, Tag):
            if has_red_text(node): return True
    return False

# --- 新增：處理 Expand Macro 的輔助函式 ---
def get_or_create_history_table(soup, main_table):
    """
    尋找含有 'history' 標題的 expand macro。
    如果找不到，就建立一個新的，並插在 main_table 之後。
    回傳該 macro 內部的 table。
    """
    # 1. 搜尋現有的 expand macro
    macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "expand"})
    target_macro = None
    
    for m in macros:
        # 檢查參數 title 是否包含 history
        title_param = m.find('ac:parameter', attrs={"ac:name": "title"})
        if title_param and "history" in title_param.get_text().lower():
            target_macro = m
            break
    
    # 2. 如果沒找到，建立新的結構
    if not target_macro:
        print("     🆕 找不到 History Expand 區塊，正在建立...")
        target_macro = soup.new_tag('ac:structured-macro', attrs={"ac:name": "expand"})
        
        # 設定標題參數
        p_title = soup.new_tag('ac:parameter', attrs={"ac:name": "title"})
        p_title.string = "history"
        target_macro.append(p_title)
        
        # 建立 Body
        body = soup.new_tag('ac:rich-text-body')
        target_macro.append(body)
        
        # 插入到 Main Table 之後
        if main_table.parent:
            main_table.insert_after(target_macro)
            # 加個換行美觀一點
            target_macro.insert_before(soup.new_tag('p'))
    
    # 3. 取得或建立 Macro 內部的 Table
    body = target_macro.find('ac:rich-text-body')
    hist_table = body.find('table')
    
    if not hist_table:
        hist_table = soup.new_tag('table')
        # 複製 Main Table 的表頭 (thead)
        main_thead_row = main_table.find('tr')
        if main_thead_row:
            hist_table.append(copy.copy(main_thead_row))
        body.append(hist_table)
        
    return hist_table

def clean_project_page_content(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    changed = False
    
    # 1. 找到主表格
    main_table = None
    all_tables = soup.find_all('table')
    
    # 排除在 expand macro 裡面的表格，先找最外層的
    for table in all_tables:
        # 簡單檢查：如果這個 table 的祖先有 ac:structured-macro，那它可能是 history 表格，先跳過
        if table.find_parent('ac:structured-macro'):
            continue

        headers = [th.get_text().strip() for th in table.find_all('th')]
        if "Item" in headers and "Update" in headers:
            main_table = table
            break
            
    if not main_table:
        print("   ⚠️ 找不到主表格 (Item/Update)，跳過處理")
        return None

    print("   🔍 找到主表格，開始分析 Rows...")
    
    tbody = main_table.find('tbody') or main_table
    rows = tbody.find_all('tr')
    
    header_row = rows[0]
    headers = [cell.get_text().strip() for cell in header_row.find_all(['th', 'td'])]
    try:
        item_idx = headers.index("Item")
        update_idx = headers.index("Update")
    except ValueError:
        return None

    # 用來暫存 History Table 的參照，避免每行都重找
    history_table_ref = None

    for row in rows[1:]:
        cols = row.find_all('td')
        if len(cols) <= max(item_idx, update_idx): continue
        
        item_name = cols[item_idx].get_text().strip()
        update_cell = cols[update_idx]
        
        # A. 切割內容
        entries = split_cell_content(update_cell)
        if len(entries) <= KEEP_LIMIT:
            continue
            
        print(f"      Item [{item_name}]: 共有 {len(entries)} 筆紀錄，準備清理...")
        
        # B. 篩選
        keep_entries = []
        archive_entries = []
        count = 0
        for entry in entries:
            is_red = check_entry_red(entry)
            if is_red:
                keep_entries.append(entry)
                continue
            
            if count < KEEP_LIMIT:
                keep_entries.append(entry)
                count += 1
            else:
                archive_entries.append(entry)
        
        if not archive_entries:
            continue
            
        print(f"      ✂️ 將歸檔 {len(archive_entries)} 筆資料到 History Expand...")
        changed = True
        
        # C. 更新主表格
        update_cell.clear()
        for entry in keep_entries:
            for node in entry:
                update_cell.append(node)
                
        # D. 處理 History (Expand Macro)
        if history_table_ref is None:
            # 只有在第一次需要搬移時才去尋找/建立 History 結構
            history_table_ref = get_or_create_history_table(soup, main_table)
            
        # 在 History 表格中找對應 Item 的 Row
        hist_rows = history_table_ref.find_all('tr')
        target_hist_row = None
        
        for h_row in hist_rows:
            h_cols = h_row.find_all('td')
            if not h_cols: continue
            # 比對 Item 名稱
            if h_cols[item_idx].get_text().strip() == item_name:
                target_hist_row = h_row
                break
        
        if not target_hist_row:
            target_hist_row = soup.new_tag('tr')
            for _ in range(len(headers)):
                target_hist_row.append(soup.new_tag('td'))
            target_hist_row.find_all('td')[item_idx].string = item_name
            history_table_ref.append(target_hist_row)
            
        hist_update_cell = target_hist_row.find_all('td')[update_idx]
        if hist_update_cell.contents:
            hist_update_cell.append(soup.new_tag('br'))
            
        for entry in archive_entries:
            for node in entry:
                hist_update_cell.append(node)

    return str(soup) if changed else None

def update_page(page_data, new_content):
    print(f"💾 正在儲存頁面: {page_data['title']} (靜默模式)...")
    url = f"{API_ENDPOINT}/{page_data['id']}"
    payload = {
        "version": {"number": page_data['version']['number'] + 1, "minorEdit": True},
        "title": page_data['title'],
        "type": "page",
        "body": {
            "storage": {
                "value": new_content,
                "representation": "storage"
            }
        }
    }
    resp = requests.put(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), 
                       headers=get_headers(), data=json.dumps(payload))
    resp.raise_for_status()
    print("✅ 更新成功！")

def main():
    print("=== Confluence 專案頁面整理機器人 (V3: Expand Macro) ===")
    
    report = find_latest_report()
    target_info = extract_first_project_link(report['body']['view']['value'])
    
    if not target_info:
        print("結束：沒有找到可處理的專案連結。")
        return

    if 'id' in target_info:
        page_data = get_page_by_id(target_info['id'])
    else:
        page_data = get_page_by_title(target_info['title'])
        
    if not page_data:
        print(f"❌ 最終失敗：無法找到對應頁面")
        return
        
    print(f"📖 讀取頁面: {page_data['title']} (ID: {page_data['id']})")
    
    new_content = clean_project_page_content(page_data['body']['storage']['value'])
    
    if new_content:
        update_page(page_data, new_content)
    else:
        print("👌 頁面無需變更")

if __name__ == "__main__":
    main()
