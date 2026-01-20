import os
import requests
import json
import re
import sys
import copy
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse, parse_qs, unquote
from bs4 import BeautifulSoup, Tag, NavigableString

# --- 設定區 ---
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")
MASTER_PAGE_ID = os.environ.get("MASTER_PAGE_ID")
KEEP_LIMIT = 5 

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數")
    sys.exit(1)

parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# --- 1. 搜尋週報 ---
def find_latest_report():
    if MASTER_PAGE_ID:
        print(f"🎯 偵測到 MASTER_PAGE_ID ({MASTER_PAGE_ID})")
        url = f"{API_ENDPOINT}/{MASTER_PAGE_ID}"
        params = {'expand': 'body.view,version'}
        try:
            r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            print(f"❌ 讀取失敗: {e}")
            sys.exit(1)

    print("🔍 正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    url = f"{API_ENDPOINT}/search"
    params = {'cql': cql, 'limit': 1, 'expand': 'body.view'}
    r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    r.raise_for_status()
    results = r.json().get('results', [])
    if not results:
        print("⚠️ 錯誤：找不到週報")
        sys.exit(1)
    print(f"✅ 搜尋成功: {results[0]['title']}")
    return results[0]

def extract_all_project_links(report_body):
    soup = BeautifulSoup(report_body, 'lxml')
    tables = soup.find_all('table')
    project_targets = []
    
    for table in tables:
        h_row = table.find('tr')
        if not h_row: continue
        headers = [c.get_text().strip() for c in h_row.find_all(['th', 'td'])]
        
        if "Project" in headers:
            print("✅ 找到 Project Status 表格")
            proj_idx = headers.index("Project")
            for row in table.find_all('tr')[1:]:
                cols = row.find_all('td')
                if len(cols) > proj_idx:
                    for link in cols[proj_idx].find_all('a'):
                        link_text = link.get_text().strip()
                        href = link.get('href', '')
                        
                        target = {'name': link_text} # 預設名稱用文字，方便辨識
                        
                        # 策略 1: 嘗試取得 data-linked-resource-id (最準)
                        pid = link.get('data-linked-resource-id')
                        if pid:
                            target['id'] = pid
                        else:
                            # 策略 2: 分析 href 網址
                            # 情況 A: URL 包含 pageId 參數
                            if 'pageId=' in href:
                                qs = parse_qs(urlparse(href).query)
                                if 'pageId' in qs: 
                                    target['id'] = qs['pageId'][0]
                            
                            # 情況 B: URL 包含 /pages/123456/ (Confluence 標準格式)
                            # Regex 改良：抓取 /pages/ 後面的數字
                            elif '/pages/' in href:
                                m = re.search(r'/pages/(\d+)', href)
                                if m: 
                                    target['id'] = m.group(1)
                                else:
                                    # 沒 ID，嘗試抓最後一段當標題 (解碼 URL)
                                    # 例如 .../WeeklyStatus_QCA2066+MP -> WeeklyStatus_QCA2066[MP]
                                    clean_title = unquote(href.split('/')[-1]).replace('+', ' ')
                                    target['title'] = clean_title

                            # 情況 C: 一般顯示連結 /display/Space/Page+Title
                            elif '/display/' in href:
                                clean_title = unquote(href.split('/')[-1]).replace('+', ' ')
                                target['title'] = clean_title
                                
                            # 策略 3: 真的什麼都沒有，才用連結文字去猜
                            else:
                                target['title'] = link_text
                        
                        if target.get('id') or target.get('title'):
                            # 避免重複加入
                            is_exist = False
                            for t in project_targets:
                                if t.get('id') == target.get('id') and target.get('id'): is_exist = True
                                if t.get('title') == target.get('title') and target.get('title'): is_exist = True
                            
                            if not is_exist:
                                project_targets.append(target)
            break 
    return project_targets

def get_page_by_id(page_id):
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version'}
    r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    return r.json() if r.status_code == 200 else None

def get_page_by_title(title):
    url = f"{API_ENDPOINT}"
    params = {'title': title, 'expand': 'body.storage,version'}
    r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    res = r.json().get('results', [])
    if res: return res[0]
    
    # 自動補全 WeeklyStatus_ 前綴 (如果網址解析出來的標題本身就沒帶)
    if not title.startswith("WeeklyStatus_"):
        print(f"   嘗試補全標題: WeeklyStatus_{title}")
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params={'title': f"WeeklyStatus_{title}", 'expand': 'body.storage,version'})
        res = r.json().get('results', [])
        if res: return res[0]
    return None

# --- V18 避雷針模式邏輯 ---

def is_date_header(text):
    if not text: return False
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text[:50]))

def split_cell_content(cell_soup):
    entries = []
    current_entry = []
    for child in cell_soup.contents:
        if isinstance(child, NavigableString) and not child.strip():
            if current_entry: current_entry.append(child)
            continue
        
        is_header = False
        if isinstance(child, Tag) and child.name in ['p', 'span', 'div']:
            txt = child.get_text().strip()
            if is_date_header(txt): is_header = True
        elif isinstance(child, NavigableString):
            if is_date_header(str(child).strip()): is_header = True

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
            s = str(node).lower()
            if 'color: red' in s or 'rgb(255, 0, 0)' in s or '#ff0000' in s: return True
    return False

def get_or_create_history_table(soup, main_table):
    macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "expand"})
    target_macro = None
    for m in macros:
        t = m.find('ac:parameter', attrs={"ac:name": "title"})
        if t and "history" in t.get_text().lower():
            target_macro = m
            break
    
    if not target_macro:
        target_macro = soup.new_tag('ac:structured-macro', attrs={"ac:name": "expand"})
        p = soup.new_tag('ac:parameter', attrs={"ac:name": "title"})
        p.string = "history"
        target_macro.append(p)
        body = soup.new_tag('ac:rich-text-body')
        target_macro.append(body)
        if main_table.parent:
            main_table.insert_after(target_macro)
            target_macro.insert_before(soup.new_tag('p'))
    
    body = target_macro.find('ac:rich-text-body')
    hist_table = body.find('table')
    if not hist_table:
        hist_table = soup.new_tag('table')
        thead = main_table.find('tr', recursive=False)
        if thead: hist_table.append(copy.copy(thead))
        body.append(hist_table)
    return hist_table

def clean_project_page_content(html_content, page_title):
    soup = BeautifulSoup(html_content, 'lxml')
    changed = False
    
    main_table = None
    all_tables = soup.find_all('table')
    for t in all_tables:
        if t.find_parent('ac:structured-macro'): continue
        headers = [c.get_text().strip() for c in t.find_all('th')]
        if "Item" in headers and "Update" in headers:
            main_table = t
            break
            
    if not main_table:
        print(f"   ⚠️  [{page_title}] 找不到主表格，跳過。")
        return None

    print(f"   🔍 [{page_title}] 找到主表格，分析中...")
    sys.stdout.flush()
    
    rows = main_table.find_all('tr', recursive=False)
    if not rows and main_table.find('tbody', recursive=False):
        rows = main_table.find('tbody', recursive=False).find_all('tr', recursive=False)

    if not rows: return None

    header_row = rows[0]
    headers = [c.get_text().strip() for c in header_row.find_all(['th', 'td'], recursive=False)]
    try:
        item_idx = headers.index("Item")
        update_idx = headers.index("Update")
    except ValueError: return None

    history_table_ref = None
    total_rows = len(rows) - 1
    
    for i, row in enumerate(rows[1:]):
        sys.stdout.write(f"\r      Processing Row {i+1}/{total_rows} ...")
        sys.stdout.flush()

        cols = row.find_all('td', recursive=False)
        if len(cols) <= max(item_idx, update_idx): continue
        
        update_cell = cols[update_idx]
        if update_cell.find('table'):
            print(f" [SKIP Heavy Table] ", end='')
            continue

        item_name = cols[item_idx].get_text().strip()[:50]
        entries = split_cell_content(update_cell)
        
        if len(entries) <= KEEP_LIMIT: continue
            
        keep = []
        archive = []
        count = 0
        
        for entry in entries:
            if check_entry_red(entry):
                keep.append(entry)
                continue
            if count < KEEP_LIMIT:
                keep.append(entry)
                count += 1
            else:
                archive.append(entry)
        
        if not archive: continue
        changed = True
        
        update_cell.clear()
        for e in keep:
            for n in e: update_cell.append(n)
            
        if not history_table_ref:
            history_table_ref = get_or_create_history_table(soup, main_table)
            
        hist_rows = history_table_ref.find_all('tr', recursive=False)
        target_row = None
        for hr in hist_rows:
            hc = hr.find_all('td', recursive=False)
            if not hc: continue
            if hc[item_idx].get_text().strip()[:50] == item_name:
                target_row = hr
                break
        
        if not target_row:
            target_row = soup.new_tag('tr')
            for _ in range(len(headers)): target_row.append(soup.new_tag('td'))
            target_row.find_all('td')[item_idx].string = item_name
            history_table_ref.append(target_row)
            
        dest = target_row.find_all('td', recursive=False)[update_idx]
        if dest.contents: dest.append(soup.new_tag('br'))
        for e in archive:
            for n in e: dest.append(n)
    
    print(f"\r      Processing Row {total_rows}/{total_rows} (Done)        ")
    sys.stdout.flush()
    return str(soup) if changed else None

def update_page(page_data, new_content):
    print(f"💾 儲存: {page_data['title']}...")
    url = f"{API_ENDPOINT}/{page_data['id']}"
    payload = {
        "version": {"number": page_data['version']['number'] + 1, "minorEdit": True},
        "title": page_data['title'],
        "type": "page",
        "body": {"storage": {"value": new_content, "representation": "storage"}}
    }
    requests.put(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), headers=get_headers(), data=json.dumps(payload)).raise_for_status()
    print("✅ 成功！")

def main():
    print("=== Confluence Cleaner (V19: Smart Link Parsing) ===")
    report = find_latest_report()
    targets = extract_all_project_links(report['body']['view']['value'])
    if not targets: return
    print(f"📋 找到 {len(targets)} 個專案")
    for t in targets:
        print(f"\n🚀 {t['name']}")
        p = None
        if 'id' in t:
            p = get_page_by_id(t['id'])
        elif 'title' in t:
            # 優先使用解析出來的標題
            print(f"   使用解析標題: {t['title']}")
            p = get_page_by_title(t['title'])
            
        if not p:
            print("❌ 讀取失敗")
            continue
        new_c = clean_project_page_content(p['body']['storage']['value'], p['title'])
        if new_c: update_page(p, new_c)
        else: print("👌 無需變更")

if __name__ == "__main__":
    main()
