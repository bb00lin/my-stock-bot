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
    print("錯誤：缺少環境變數")
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

# --- 2. 內容處理邏輯 (大幅修改) ---

def is_date_header(text):
    """檢查文字是否包含日期格式 [YYYY/MM/DD]"""
    # 寬鬆匹配：只要有 [數字/數字/數字] 就當作是開頭
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text))

def has_red_text(tag):
    """檢查這個標籤(包含子標籤)是否有紅字"""
    if not isinstance(tag, Tag): return False
    # 檢查 style
    if tag.has_attr('style'):
        style = tag['style'].lower()
        if 'rgb(255, 0, 0)' in style or '#ff0000' in style or 'red' in style:
            return True
    # 檢查 font tag
    if tag.name == 'font' and (tag.get('color') == 'red' or tag.get('color') == '#ff0000'):
        return True
    # 遞迴檢查子節點
    for child in tag.descendants:
        if isinstance(child, Tag):
            if child.has_attr('style'):
                style = child['style'].lower()
                if 'rgb(255, 0, 0)' in style or '#ff0000' in style: return True
            if child.name == 'font' and (child.get('color') == 'red' or child.get('color') == '#ff0000'):
                return True
    return False

def split_cell_content(cell_soup):
    """將格子內的內容切分成一個個 Entry (以日期開頭為界)"""
    entries = []
    current_entry = []
    
    # Confluence Storage Format 通常是 <p>[Date]</p><ul>...</ul> 或者是 <p>[Date]<br/>...</p>
    # 我們遍歷所有子節點
    for child in cell_soup.contents:
        if isinstance(child, NavigableString) and not child.strip():
            # 空白字串，附屬在上一段
            if current_entry: current_entry.append(child)
            continue
            
        text = child.get_text() if isinstance(child, Tag) else str(child)
        
        # 判斷是否為新的日期開頭
        # 1. 必須含有日期格式
        # 2. 通常日期是獨立的一行 (P tag) 或是文字的開頭
        if is_date_header(text):
            # 儲存上一筆
            if current_entry:
                entries.append(current_entry)
            # 開啟新的一筆
            current_entry = [child]
        else:
            # 不是日期開頭，歸入當前這一筆
            # 如果還沒有任何日期開頭(最上面的雜訊)，也先歸入 current
            current_entry.append(child)
            
    # 最後一筆
    if current_entry:
        entries.append(current_entry)
        
    return entries

def check_entry_red(entry_nodes):
    """檢查這一整筆 Entry 裡面有沒有紅字"""
    for node in entry_nodes:
        if isinstance(node, Tag):
            if has_red_text(node): return True
    return False

def clean_project_page_content(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    changed = False
    
    # 1. 確保有 History 標題
    history_header = soup.find(lambda tag: tag.name in ['h1', 'h2', 'h3', 'h4'] and 'History' in tag.get_text())
    if not history_header:
        print("   ℹ️ 建立 History 區塊...")
        history_header = soup.new_tag('h2')
        history_header.string = "History"
        soup.append(history_header)
        changed = True

    # 2. 找到主表格 (判斷依據: 表頭有 Item 和 Update)
    main_table = None
    all_tables = soup.find_all('table')
    
    for table in all_tables:
        # 檢查表頭
        headers = [th.get_text().strip() for th in table.find_all('th')]
        if "Item" in headers and "Update" in headers:
            # 且這個表格要在 History 之前 (如果有 History 的話)
            if history_header and table.sourceline and history_header.sourceline:
                if table.sourceline > history_header.sourceline: continue
            main_table = table
            break
            
    if not main_table:
        print("   ⚠️ 找不到主表格 (Item/Update)，跳過處理")
        return str(soup) if changed else None

    print("   🔍 找到主表格，開始分析 Rows...")
    
    # 3. 處理主表格的每一列
    tbody = main_table.find('tbody') or main_table
    rows = tbody.find_all('tr')
    
    # 找出欄位索引
    header_row = rows[0]
    headers = [cell.get_text().strip() for cell in header_row.find_all(['th', 'td'])]
    try:
        item_idx = headers.index("Item")
        update_idx = headers.index("Update")
    except ValueError:
        return str(soup) if changed else None

    # 準備 History 表格 (如果需要搬移才用到)
    history_table = None
    
    for row in rows[1:]: # 跳過表頭
        cols = row.find_all('td')
        if len(cols) <= max(item_idx, update_idx): continue
        
        item_name = cols[item_idx].get_text().strip()
        update_cell = cols[update_idx]
        
        # A. 切割內容
        entries = split_cell_content(update_cell)
        if len(entries) <= KEEP_LIMIT:
            continue # 數量未達標，跳過
            
        print(f"      Item [{item_name}]: 共有 {len(entries)} 筆紀錄，準備清理...")
        
        # B. 篩選 (保留 vs 歸檔)
        keep_entries = []
        archive_entries = []
        
        count = 0
        for entry in entries:
            is_red = check_entry_red(entry)
            if is_red:
                keep_entries.append(entry)
                # print("         🔴 紅字保留")
                continue
            
            if count < KEEP_LIMIT:
                keep_entries.append(entry)
                count += 1
            else:
                archive_entries.append(entry)
        
        if not archive_entries:
            continue
            
        print(f"      ✂️ 將歸檔 {len(archive_entries)} 筆資料...")
        changed = True
        
        # C. 更新主表格 (清空 -> 填入保留的)
        update_cell.clear()
        for entry in keep_entries:
            for node in entry:
                update_cell.append(node)
                
        # D. 處理 History
        # 尋找 History 表格 (在 History header 之後)
        if not history_table:
            # 嘗試尋找既有的
            curr = history_header.next_sibling
            while curr:
                if isinstance(curr, Tag) and curr.name == 'table':
                    # 檢查表頭是否正確
                    h_headers = [th.get_text().strip() for th in curr.find_all('th')]
                    if "Item" in h_headers and "Update" in h_headers:
                        history_table = curr
                        break
                curr = curr.next_sibling
            
            # 如果還是沒有，就新建一個
            if not history_table:
                print("      🆕 新建 History 表格...")
                history_table = soup.new_tag('table')
                # 複製表頭
                new_thead = copy.copy(rows[0])
                history_table.append(new_thead)
                # 插入到 History header 之後
                history_header.insert_after(history_table)
        
        # 在 History 表格中找對應 Item 的 Row
        hist_rows = history_table.find_all('tr')
        target_hist_row = None
        
        for h_row in hist_rows:
            h_cols = h_row.find_all('td')
            if not h_cols: continue
            if h_cols[item_idx].get_text().strip() == item_name:
                target_hist_row = h_row
                break
        
        if not target_hist_row:
            # 沒找到，新建一行
            target_hist_row = soup.new_tag('tr')
            # 補滿格子
            for _ in range(len(headers)):
                target_hist_row.append(soup.new_tag('td'))
            # 填入 Item Name
            target_hist_row.find_all('td')[item_idx].string = item_name
            history_table.append(target_hist_row)
            
        # 將資料塞入 History 的 Update 欄位
        hist_update_cell = target_hist_row.find_all('td')[update_idx]
        
        # 在塞入前，最好加個分隔 (例如換行)
        if hist_update_cell.contents:
            hist_update_cell.append(soup.new_tag('br'))
            
        for entry in archive_entries:
            for node in entry:
                # 注意：這裡要 copy 節點，因為原節點已經從主表格拔除
                # 但 append 會自動處理移動，所以直接 append 即可
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
    print("=== Confluence 專案頁面整理機器人 (V2: Cell Parsing) ===")
    
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
