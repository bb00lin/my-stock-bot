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
                        pid = link.get('data-linked-resource-id')
                        name = link.get_text().strip()
                        target = {'name': name}
                        if pid:
                            target['id'] = pid
                        else:
                            href = link.get('href', '')
                            if 'pageId=' in href:
                                qs = parse_qs(urlparse(href).query)
                                if 'pageId' in qs: target['id'] = qs['pageId'][0]
                            else:
                                m = re.search(r'/pages/(\d+)/', href)
                                if m: target['id'] = m.group(1)
                                else: target['title'] = name
                        
                        if target.get('id') or target.get('title'):
                            if target not in project_targets: project_targets.append(target)
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
    if not title.startswith("WeeklyStatus_"):
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params={'title': f"WeeklyStatus_{title}", 'expand': 'body.storage,version'})
        res = r.json().get('results', [])
        if res: return res[0]
    return None

# --- V16 核心：結構快篩防禦機制 ---

def is_date_header(text):
    if not text: return False
    # 只取前 50 字元檢查，避免 regex 卡死
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text[:50]))

def is_safe_to_read_text(tag):
    """
    【V16 核心】決定一個標籤是否「安全」到可以讀取文字。
    如果標籤內包含大表格，讀取文字會觸發遍歷，導致卡頓。
    """
    # 1. 黑名單：這些標籤絕對不是標題
    BLOCK_TAGS = ['table', 'tbody', 'thead', 'tr', 'td', 'ul', 'ol', 'ac:structured-macro', 'ac:layout-section']
    if tag.name in BLOCK_TAGS:
        return False
    
    # 2. 結構快篩：檢查直接子節點
    # 如果直接子節點包含重型標籤，則判定此標籤為「容器」，不讀取文字
    for child in tag.children:
        if isinstance(child, Tag):
            if child.name in BLOCK_TAGS:
                return False
            # 額外檢查：如果是 div 包 div 包 table 的情況
            if child.name in ['div', 'p']:
                # 這裡只做淺層檢查，如果還有孫節點是 table，也放棄
                # find(recursive=False) 速度極快
                if child.find(BLOCK_TAGS, recursive=False):
                    return False

    # 3. 數量限制：如果子節點太多，可能也是大內容，跳過
    # 這裡轉 list 會有微小成本，但在大表格面前是救命稻草
    # 使用 sum(1 for _) 避免建立 list 佔用記憶體
    child_count = sum(1 for _ in tag.children)
    if child_count > 20: 
        return False

    return True

def split_cell_content(cell_soup):
    entries = []
    current_entry = []
    
    for child in cell_soup.contents:
        # 1. 忽略純空白
        if isinstance(child, NavigableString) and not child.strip():
            if current_entry: current_entry.append(child)
            continue
        
        is_header = False
        
        if isinstance(child, Tag):
            # 【V16 修正】：先做結構快篩，確認安全才讀文字
            if is_safe_to_read_text(child):
                # 這裡讀取文字相對安全
                txt = child.get_text().strip()
                if is_date_header(txt):
                    is_header = True
            else:
                # 不安全（包含表格等），直接視為內容，is_header = False
                pass
        
        elif isinstance(child, NavigableString):
            if is_date_header(str(child).strip()):
                is_header = True

        if is_header:
            if current_entry: entries.append(current_entry)
            current_entry = [child]
        else:
            current_entry.append(child)
            
    if current_entry: entries.append(current_entry)
    return entries

# --- 紅字檢查：依然保持安全模式 ---
def is_red_style(tag):
    if tag.has_attr('style'):
        s = tag['style'].lower()
        if 'rgb(255, 0, 0)' in s or '#ff0000' in s or 'color: red' in s: return True
    if tag.name == 'font' and (tag.get('color') == 'red' or tag.get('color') == '#ff0000'): return True
    return False

def has_red_text_safe(tag):
    if not isinstance(tag, Tag): return False
    if is_red_style(tag): return True
    
    # 禁區：絕對不進入大表格檢查紅字
    NO_GO = ['table', 'ac:structured-macro', 'tbody', 'thead', 'tr', 'td']
    if tag.name in NO_GO: return False

    for child in tag.children:
        if isinstance(child, Tag):
            if has_red_text_safe(child): return True
    return False

def check_entry_red(entry_nodes):
    for node in entry_nodes:
        if isinstance(node, Tag):
            if has_red_text_safe(node): return True
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
    
    # 使用 main_table 直接找 tr (兼容有無 tbody 的情況)
    # recursive=False 是關鍵
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
        # 每一行都印，確保沒死
        sys.stdout.write(f"\r      Processing Row {i+1}/{total_rows} ...")
        sys.stdout.flush()

        cols = row.find_all('td', recursive=False)
        if len(cols) <= max(item_idx, update_idx): continue
        
        # 安全取名
        item_name_tag = cols[item_idx]
        # 同樣使用安全檢查
        if is_safe_to_read_text(item_name_tag):
            item_name = item_name_tag.get_text().strip()[:50]
        else:
            item_name = "Complex Item Name"

        update_cell = cols[update_idx]
        
        # V16 執行結構快篩切割
        entries = split_cell_content(update_cell)
        
        if len(entries) <= KEEP_LIMIT: continue
            
        keep = []
        archive = []
        count = 0
        
        for entry in entries:
            # 紅字檢查
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
            
            # 這裡也要安全讀取
            h_name = ""
            if is_safe_to_read_text(hc[item_idx]):
                h_name = hc[item_idx].get_text().strip()[:50]
                
            if h_name == item_name:
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
    print("=== Confluence Cleaner (V16: Structure Quick-Scan) ===")
    report = find_latest_report()
    targets = extract_all_project_links(report['body']['view']['value'])
    if not targets: return
    print(f"📋 找到 {len(targets)} 個專案")
    for t in targets:
        print(f"\n🚀 {t['name']}")
        p = get_page_by_id(t['id']) if 'id' in t else get_page_by_title(t['title'])
        if not p:
            print("❌ 讀取失敗")
            continue
        new_c = clean_project_page_content(p['body']['storage']['value'], p['title'])
        if new_c: update_page(p, new_c)
        else: print("👌 無需變更")

if __name__ == "__main__":
    main()