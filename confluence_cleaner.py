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
    print("錯誤：缺少環境變數 (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# --- 1. 搜尋週報 ---
def find_latest_report():
    if MASTER_PAGE_ID:
        print(f"🎯 偵測到 MASTER_PAGE_ID ({MASTER_PAGE_ID})，直接讀取...")
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
        print("⚠️ 錯誤：找不到週報。")
        sys.exit(1)
    print(f"✅ 搜尋成功: {results[0]['title']}")
    return results[0]

def extract_all_project_links(report_body):
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
            print("✅ 找到 Project Status 表格，解析中...")
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
    if not project_targets: print("⚠️ 警告：找不到任何專案連結")
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
        params['title'] = alt_title
        resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        results = resp.json().get('results', [])
        if results: return results[0]
    return None

# --- V13 核心：絕對黑箱模式 ---

def is_date_header(text):
    if not text: return False
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text[:30]))

def has_red_text(tag):
    if not isinstance(tag, Tag): return False
    # 使用 find 短路查找，這是最快的方法
    def is_red_style(node):
        if isinstance(node, Tag):
            if node.has_attr('style'):
                s = node['style'].lower()
                if 'rgb(255, 0, 0)' in s or '#ff0000' in s or 'color: red' in s: return True
            if node.name == 'font' and (node.get('color') == 'red' or node.get('color') == '#ff0000'): return True
        return False
    if is_red_style(tag): return True
    if tag.find(is_red_style): return True
    return False

def split_cell_content(cell_soup):
    entries = []
    current_entry = []
    
    # 1. 複雜標籤黑名單：看到這些直接跳過，絕對不讀取內容
    # 這能保證程式不會被大表格卡死
    COMPLEX_TAGS = ['table', 'tbody', 'thead', 'tr', 'td', 'ul', 'ol', 'ac:structured-macro', 'ac:image']
    
    # 2. 簡單標籤白名單：只有這些標籤才值得檢查是否為日期
    SIMPLE_TAGS = ['p', 'span', 'strong', 'em', 'h1', 'h2', 'h3', 'h4', 'div']

    for child in cell_soup.contents:
        if isinstance(child, NavigableString) and not child.strip():
            if current_entry: current_entry.append(child)
            continue
        
        is_header = False
        
        # 【V13 核心】：嚴格的類型檢查
        if isinstance(child, Tag):
            # 如果是複雜標籤 (如表格)，直接視為內容，跳過檢查
            if child.name in COMPLEX_TAGS:
                is_header = False
            
            # 如果是簡單標籤，才檢查文字
            elif child.name in SIMPLE_TAGS:
                # 再次確認：如果簡單標籤裡面包了複雜標籤 (例如 div 包 table)，也直接跳過
                if child.find(COMPLEX_TAGS):
                    is_header = False
                else:
                    # 只有在確定結構簡單時，才讀取文字
                    txt = child.get_text().strip()
                    if is_date_header(txt):
                        is_header = True
        
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

def check_entry_red(entry_nodes):
    for node in entry_nodes:
        if isinstance(node, Tag):
            if has_red_text(node): return True
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
        headers = [th.get_text().strip() for th in t.find_all('th')]
        if "Item" in headers and "Update" in headers:
            main_table = t
            break
            
    if not main_table:
        print(f"   ⚠️  [{page_title}] 找不到主表格，跳過。")
        return None

    print(f"   🔍 [{page_title}] 找到主表格，分析中...")
    sys.stdout.flush()
    
    # 使用 main_table 直接找 tr (兼容有無 tbody 的情況)
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
        if i % 1 == 0: # 每一行都印出進度，確保沒卡死
            sys.stdout.write(f"\r      Processing Row {i+1}/{total_rows} ...")
            sys.stdout.flush()

        cols = row.find_all('td', recursive=False)
        if len(cols) <= max(item_idx, update_idx): continue
        
        # 簡單取名
        item_name = cols[item_idx].get_text().strip()[:50]
        update_cell = cols[update_idx]
        
        # 執行 V13 極速切割
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
    print("=== Confluence Cleaner (V13: Black Box Mode) ===")
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
