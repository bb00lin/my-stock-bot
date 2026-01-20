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
MASTER_PAGE_ID = os.environ.get("MASTER_PAGE_ID")
KEEP_LIMIT = 5  # 保留最新的幾筆資料

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# --- 1. 搜尋週報與提取所有專案連結 ---
def find_latest_report():
    if MASTER_PAGE_ID:
        print(f"🎯 偵測到 MASTER_PAGE_ID ({MASTER_PAGE_ID})，直接讀取該頁面...")
        url = f"{API_ENDPOINT}/{MASTER_PAGE_ID}"
        params = {'expand': 'body.view,version'}
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        try:
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ 指定的 MASTER_PAGE_ID 讀取失敗: {e}")
            sys.exit(1)

    print("🔍 正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    url = f"{API_ENDPOINT}/search"
    params = {'cql': cql, 'limit': 1, 'expand': 'body.view'}
    
    response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    response.raise_for_status()
    results = response.json().get('results', [])
    
    if not results:
        print("⚠️ 錯誤：找不到週報。請確認標題或使用 MASTER_PAGE_ID。")
        sys.exit(1)
        
    print(f"✅ 搜尋成功，找到最新週報: {results[0]['title']}")
    return results[0]

def extract_all_project_links(report_body):
    # 使用 lxml 加速解析
    soup = BeautifulSoup(report_body, 'lxml')
    tables = soup.find_all('table')
    project_targets = []
    
    for table in tables:
        headers = []
        header_row = table.find('tr')
        if not header_row: continue
        
        for cell in header_row.find_all(['th', 'td']):
            headers.append(cell.get_text().strip())
        
        if "Project" in headers:
            print("✅ 找到 Project Status 表格，開始解析專案連結...")
            proj_idx = headers.index("Project")
            
            rows = table.find_all('tr')
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) > proj_idx:
                    links = cols[proj_idx].find_all('a')
                    for link in links:
                        page_id = link.get('data-linked-resource-id')
                        target = {}
                        if page_id:
                            target['id'] = page_id
                            target['name'] = link.get_text().strip()
                        else:
                            href = link.get('href', '')
                            if 'pageId=' in href:
                                qs = parse_qs(urlparse(href).query)
                                if 'pageId' in qs: 
                                    target['id'] = qs['pageId'][0]
                                    target['name'] = link.get_text().strip()
                            else:
                                match = re.search(r'/pages/(\d+)/', href)
                                if match: 
                                    target['id'] = match.group(1)
                                    target['name'] = link.get_text().strip()
                                else:
                                    title = link.get_text().strip()
                                    if title:
                                        target['title'] = title
                                        target['name'] = title
                        
                        if target and target not in project_targets:
                            project_targets.append(target)
            break 
    
    if not project_targets:
        print("⚠️ 警告：找不到任何專案連結")
        
    return project_targets

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
            return results[0]
    return None

# --- 2. 內容處理邏輯 (V11: Lazy Check) ---

def is_date_header(text):
    if not text: return False
    # 只檢查前 50 個字元，避免長字串 regex 效能問題
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text[:50]))

def has_red_text(tag):
    """
    暴力字串搜尋 (極速)
    """
    if not isinstance(tag, Tag): return False
    # 轉字串雖然有成本，但比遞迴 DOM 快，且只對 entries 做，次數少
    html_str = str(tag).lower()
    if 'rgb(255, 0, 0)' in html_str: return True
    if '#ff0000' in html_str: return True
    if 'color: red' in html_str: return True
    if 'color:red' in html_str: return True
    return False

def split_cell_content(cell_soup):
    entries = []
    current_entry = []
    
    # 遇到這些標籤，直接視為內容，絕對不是標題
    SKIP_CHECK_TAGS = ['table', 'tbody', 'tr', 'td', 'ul', 'ol', 'ac:structured-macro', 'ac:image']

    for child in cell_soup.contents:
        # 1. 忽略空白
        if isinstance(child, NavigableString) and not child.strip():
            if current_entry: current_entry.append(child)
            continue
        
        is_header = False
        
        # 2. 快速過濾複雜標籤
        if isinstance(child, Tag) and child.name in SKIP_CHECK_TAGS:
            is_header = False
        else:
            # 【V11 關鍵修正】：惰性文字讀取
            # 不要用 get_text() 讀取全部！那會遍歷裡面包的巨大表格。
            # 我們只看「第一段文字」就好。
            first_text = ""
            if isinstance(child, NavigableString):
                first_text = str(child).strip()
            elif isinstance(child, Tag):
                # stripped_strings 是 generator，next() 只會拿第一個，瞬間完成
                first_text = next(child.stripped_strings, '')
            
            if is_date_header(first_text):
                is_header = True

        # 3. 切割
        if is_header:
            if current_entry: entries.append(current_entry)
            current_entry = [child]
        else:
            current_entry.append(child)
            
    if current_entry: entries.append(current_entry)
    return entries

def check_entry_red(entry_nodes):
    for node in entry_nodes:
        if isinstance(node, Tag):
            if has_red_text(node): return True
    return False

def get_or_create_history_table(soup, main_table):
    macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "expand"})
    target_macro = None
    
    for m in macros:
        title_param = m.find('ac:parameter', attrs={"ac:name": "title"})
        if title_param and "history" in title_param.get_text().lower():
            target_macro = m
            break
    
    if not target_macro:
        target_macro = soup.new_tag('ac:structured-macro', attrs={"ac:name": "expand"})
        p_title = soup.new_tag('ac:parameter', attrs={"ac:name": "title"})
        p_title.string = "history"
        target_macro.append(p_title)
        body = soup.new_tag('ac:rich-text-body')
        target_macro.append(body)
        
        if main_table.parent:
            main_table.insert_after(target_macro)
            target_macro.insert_before(soup.new_tag('p'))
    
    body = target_macro.find('ac:rich-text-body')
    hist_table = body.find('table')
    
    if not hist_table:
        hist_table = soup.new_tag('table')
        # 複製表頭 (注意：recursive=False)
        main_thead_row = main_table.find('tr', recursive=False)
        if main_thead_row:
            hist_table.append(copy.copy(main_thead_row))
        body.append(hist_table)
        
    return hist_table

def clean_project_page_content(html_content, page_title):
    soup = BeautifulSoup(html_content, 'lxml')
    changed = False
    
    main_table = None
    all_tables = soup.find_all('table')
    
    for table in all_tables:
        if table.find_parent('ac:structured-macro'):
            continue
        headers = [th.get_text().strip() for th in table.find_all('th')]
        if "Item" in headers and "Update" in headers:
            main_table = table
            break
            
    if not main_table:
        print(f"   ⚠️  [{page_title}] 找不到主表格，跳過。")
        return None

    print(f"   🔍 [{page_title}] 找到主表格，開始極速分析...")
    sys.stdout.flush()
    
    tbody = main_table.find('tbody') or main_table
    
    # 【V10 修正保留】：recursive=False 避免抓到巢狀列
    rows = tbody.find_all('tr', recursive=False)
    
    if not rows:
        return None

    header_row = rows[0]
    headers = [cell.get_text().strip() for cell in header_row.find_all(['th', 'td'], recursive=False)]
    try:
        item_idx = headers.index("Item")
        update_idx = headers.index("Update")
    except ValueError:
        return None

    history_table_ref = None
    total_rows = len(rows) - 1
    
    for i, row in enumerate(rows[1:]):
        # 顯示進度
        if i % 2 == 0:
            sys.stdout.write(f"\r      處理 Item: {i+1}/{total_rows} ...")
            sys.stdout.flush()

        cols = row.find_all('td', recursive=False)
        
        if len(cols) <= max(item_idx, update_idx): continue
        
        # 只取第一段文字做標題，避免卡頓
        item_name_tag = cols[item_idx]
        item_name = next(item_name_tag.stripped_strings, item_name_tag.get_text())[:50]
        
        update_cell = cols[update_idx]
        
        # 呼叫極速切割
        entries = split_cell_content(update_cell)
        
        if len(entries) <= KEEP_LIMIT:
            continue
            
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
            
        changed = True
        
        update_cell.clear()
        for entry in keep_entries:
            for node in entry:
                update_cell.append(node)
                
        if history_table_ref is None:
            history_table_ref = get_or_create_history_table(soup, main_table)
            
        hist_rows = history_table_ref.find_all('tr', recursive=False)
        target_hist_row = None
        
        for h_row in hist_rows:
            h_cols = h_row.find_all('td', recursive=False)
            if not h_cols: continue
            
            # 比對 Item 名稱 (模糊比對)
            h_item_name = next(h_cols[item_idx].stripped_strings, h_cols[item_idx].get_text())[:50]
            if h_item_name == item_name:
                target_hist_row = h_row
                break
        
        if not target_hist_row:
            target_hist_row = soup.new_tag('tr')
            for _ in range(len(headers)):
                target_hist_row.append(soup.new_tag('td'))
            target_hist_row.find_all('td')[item_idx].string = item_name
            history_table_ref.append(target_hist_row)
            
        hist_update_cell = target_hist_row.find_all('td', recursive=False)[update_idx]
        if hist_update_cell.contents:
            hist_update_cell.append(soup.new_tag('br'))
            
        for entry in archive_entries:
            for node in entry:
                hist_update_cell.append(node)
    
    print(f"\r      處理 Item: {total_rows}/{total_rows} (完成)        ")
    sys.stdout.flush()
    
    return str(soup) if changed else None

def update_page(page_data, new_content):
    print(f"💾 正在儲存: {page_data['title']} (靜默模式)...")
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
    print("=== Confluence 專案頁面整理機器人 (V11: Lazy Check) ===")
    
    report = find_latest_report()
    project_targets = extract_all_project_links(report['body']['view']['value'])
    
    if not project_targets:
        print("結束：沒有找到任何專案連結。")
        return

    print(f"📋 總共找到 {len(project_targets)} 個專案目標")
    print("-" * 30)

    for target in project_targets:
        print(f"\n🚀 開始處理專案: {target['name']}")
        
        page_data = None
        if 'id' in target:
            page_data = get_page_by_id(target['id'])
        elif 'title' in target:
            page_data = get_page_by_title(target['title'])
            
        if not page_data:
            print(f"❌ 無法讀取頁面，跳過。")
            continue
            
        new_content = clean_project_page_content(page_data['body']['storage']['value'], page_data['title'])
        
        if new_content:
            update_page(page_data, new_content)
        else:
            print("👌 頁面無需變更")

if __name__ == "__main__":
    main()
