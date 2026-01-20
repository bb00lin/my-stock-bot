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
HOST_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{HOST_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# --- 1. 搜尋週報 ---
def find_latest_report():
    if MASTER_PAGE_ID:
        print(f"🎯 偵測到 MASTER_PAGE_ID ({MASTER_PAGE_ID})")
        url = f"{API_ENDPOINT}/{MASTER_PAGE_ID}"
        params = {'expand': 'body.view,body.storage,version'}
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
    params = {'cql': cql, 'limit': 1, 'expand': 'body.view,body.storage,version'}
    r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    r.raise_for_status()
    results = r.json().get('results', [])
    if not results:
        print("⚠️ 錯誤：找不到週報")
        sys.exit(1)
    print(f"✅ 搜尋成功: {results[0]['title']}")
    return results[0]

def resolve_real_page_id(href_link):
    if not href_link: return None
    if href_link.startswith('/'): full_url = f"{HOST_URL}{href_link}"
    else: full_url = href_link
    if 'pageId=' in full_url:
        qs = parse_qs(urlparse(full_url).query)
        if 'pageId' in qs: return qs['pageId'][0]
    m = re.search(r'/pages/(\d+)', full_url)
    if m: return m.group(1)
    try:
        r = requests.head(full_url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), allow_redirects=True, timeout=10)
        final_url = r.url
        qs = parse_qs(urlparse(final_url).query)
        if 'pageId' in qs: return qs['pageId'][0]
        m = re.search(r'/pages/(\d+)', final_url)
        if m: return m.group(1)
    except: pass
    return None

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
                        target = {'name': link.get_text().strip()}
                        pid = link.get('data-linked-resource-id')
                        if pid: target['id'] = pid
                        else:
                            real_id = resolve_real_page_id(link.get('href', ''))
                            if real_id: target['id'] = real_id
                            else: target['title'] = target['name']
                        if target.get('id') or target.get('title'):
                            exists = False
                            for t in project_targets:
                                if t.get('id') and t['id'] == target.get('id'): exists = True
                            if not exists: project_targets.append(target)
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
        print(f"   嘗試補全標題: WeeklyStatus_{title}")
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params={'title': f"WeeklyStatus_{title}", 'expand': 'body.storage,version'})
        res = r.json().get('results', [])
        if res: return res[0]
    return None

# --- V33 核心：遞迴清洗邏輯 ---

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

# 檢查標籤「本身」是否設定了紅色 (V32定義)
def is_tag_directly_red(tag):
    if not isinstance(tag, Tag): return False
    
    red_patterns = [
        r'color:\s*red', r'#ff0000', r'#de350b', r'#bf2600', r'#ff5630', r'#ce0000', 
        r'#c9372c', r'#C9372C', 
        r'rgb\(\s*255', r'rgb\(\s*222', r'rgb\(\s*201', r'rgb\(\s*191', 
        r'--ds-text-danger', r'--ds-icon-accent-red'
    ]
    combined_regex = re.compile('|'.join(red_patterns), re.IGNORECASE)
    
    # 檢查 style 屬性
    if tag.has_attr('style'):
        if combined_regex.search(tag['style']): return True
    
    # 檢查 font color
    if tag.name == 'font' and tag.has_attr('color'):
        if combined_regex.search(tag['color']): return True
        
    return False

# 【V33 核彈級清洗】：遞迴刪除非紅字
def recursive_prune_non_red(node):
    """
    遞迴檢查：
    1. 如果節點本身是紅的 -> 保留整顆樹 (回傳 node)
    2. 如果節點是文字 -> 刪除 (回傳 None) (因為如果它是紅的，它早就被父層的紅標籤包住並在第1步返回了)
    3. 如果節點是標籤但沒顏色 -> 檢查它的子節點，只保留紅色的子節點。如果子節點全死光，自己也自殺。
    """
    
    # 1. 如果是 Tag 且自帶紅色 -> 保留
    if isinstance(node, Tag) and is_tag_directly_red(node):
        return node
        
    # 2. 如果是文字節點 (走到這裡代表父層沒紅色) -> 刪除 (這是黑字!)
    if isinstance(node, NavigableString):
        if not node.strip(): return node # 保留空白排版
        return None # 刪除黑字內容
        
    # 3. 如果是普通 Tag (p, div, span, ul, li...) -> 檢查子節點
    if isinstance(node, Tag):
        # 建立新副本以免破壞原始結構
        new_node = copy.copy(node)
        new_node.clear() # 清空子節點，準備重組
        
        has_survivor = False
        for child in node.contents:
            survivor = recursive_prune_non_red(child)
            if survivor:
                new_node.append(copy.copy(survivor)) # 必須 copy 避免參考錯誤
                has_survivor = True
        
        # 如果有子節點存活，就保留這個容器；否則刪除
        if has_survivor:
            return new_node
        else:
            return None
            
    return None

def get_or_create_history_table(soup, main_table):
    macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "expand"})
    target_macro = None
    for m in macros:
        t = m.find('ac:parameter', attrs={"ac:name": "title"})
        if t and "history" in t.get_text().lower(): target_macro = m; break
    if not target_macro:
        target_macro = soup.new_tag('ac:structured-macro', attrs={"ac:name": "expand"})
        p = soup.new_tag('ac:parameter', attrs={"ac:name": "title"}); p.string = "history"
        target_macro.append(p)
        body = soup.new_tag('ac:rich-text-body'); target_macro.append(body)
        if main_table.parent: main_table.insert_after(target_macro); target_macro.insert_before(soup.new_tag('p'))
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
    extracted_summary_items = []
    
    main_table = None
    all_tables = soup.find_all('table')
    for t in all_tables:
        if t.find_parent('ac:structured-macro'): continue
        headers = [c.get_text().strip() for c in t.find_all('th')]
        if "Item" in headers and "Update" in headers: main_table = t; break
    if not main_table:
        print(f"   ⚠️  [{page_title}] 找不到主表格，跳過。")
        return None, []

    print(f"   🔍 [{page_title}] 找到主表格...")
    sys.stdout.flush()
    rows = main_table.find_all('tr', recursive=False)
    if not rows and main_table.find('tbody', recursive=False):
        rows = main_table.find('tbody', recursive=False).find_all('tr', recursive=False)
    if not rows: return None, []

    header_row = rows[0]
    headers = [c.get_text().strip() for c in header_row.find_all(['th', 'td'], recursive=False)]
    try: item_idx = headers.index("Item"); update_idx = headers.index("Update")
    except ValueError: return None, []

    history_table_ref = None
    total_rows = len(rows) - 1
    
    for i, row in enumerate(rows[1:]):
        sys.stdout.write(f"\r      Processing Row {i+1}/{total_rows} ...")
        sys.stdout.flush()
        cols = row.find_all('td', recursive=False)
        if len(cols) <= max(item_idx, update_idx): continue
        
        update_cell = cols[update_idx]
        if update_cell.find('table'): continue

        item_name = cols[item_idx].get_text().strip()[:50]
        entries = split_cell_content(update_cell)
        
        # 【V33】使用新的遞迴清洗
        cleaned_entries = []
        for entry in entries:
            # entry 是一組節點 (日期 + 內容)，我們一個個洗
            cleaned_group = []
            
            # 先檢查標題 (日期)
            header = entry[0]
            # 標題特例：如果標題本身是紅的，保留；如果是黑的，但下面有紅字，也保留
            body_nodes = entry[1:]
            surviving_body = []
            
            for node in body_nodes:
                survivor = recursive_prune_non_red(node)
                if survivor: surviving_body.append(survivor)
            
            header_is_red = False
            if isinstance(header, Tag) and is_tag_directly_red(header): header_is_red = True
            if isinstance(header, Tag) and header.find(is_tag_directly_red): header_is_red = True # 檢查子層
            
            # 如果有內容存活，或者標題本身是紅的 -> 保留這組
            if surviving_body or header_is_red:
                cleaned_group.append(header) # 標題照舊保留 (即使是黑的，只要下面有紅字就留)
                cleaned_group.extend(surviving_body)
                cleaned_entries.append(cleaned_group)
                # 收集摘要
                extracted_summary_items.append(copy.deepcopy(cleaned_group))

        keep = cleaned_entries[:KEEP_LIMIT]
        archive = cleaned_entries[KEEP_LIMIT:]
        
        if not entries and not cleaned_entries: continue

        changed = True
        update_cell.clear()
        for e in keep:
            for n in e: update_cell.append(n)
        
        if archive:
            if not history_table_ref: history_table_ref = get_or_create_history_table(soup, main_table)
            hist_rows = history_table_ref.find_all('tr', recursive=False)
            target_row = None
            for hr in hist_rows:
                hc = hr.find_all('td', recursive=False)
                if not hc: continue
                if hc[item_idx].get_text().strip()[:50] == item_name: target_row = hr; break
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
    if extracted_summary_items:
        print(f"      📌 本專案發現 {len(extracted_summary_items)} 組紅字摘要")
    return (str(soup) if changed else None), extracted_summary_items

def update_page(page_data, new_content):
    print(f"💾 儲存專案: {page_data['title']}...")
    url = f"{API_ENDPOINT}/{page_data['id']}"
    payload = {
        "version": {"number": page_data['version']['number'] + 1, "minorEdit": True},
        "title": page_data['title'],
        "type": "page",
        "body": {"storage": {"value": new_content, "representation": "storage"}}
    }
    requests.put(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), headers=get_headers(), data=json.dumps(payload)).raise_for_status()
    print("✅ 成功！")

def update_main_report_summary(main_report_data, summary_data):
    if not summary_data:
        print("📭 沒有紅字摘要，跳過更新。")
        return
    print(f"\n📝 正在更新主週報指定區塊: {main_report_data['title']}...")
    sys.stdout.flush()
    
    html_content = main_report_data['body']['storage']['value']
    soup = BeautifulSoup(html_content, 'lxml')
    SEPARATOR = "-------------------------------------"
    separators = []
    sep_pattern = re.compile(r'-{20,}')
    for tag in soup.find_all(string=sep_pattern):
        parent = tag.find_parent(['p', 'div'])
        if parent: separators.append(parent)
        else: separators.append(tag)
    
    target_start = None
    if len(separators) >= 2:
        print("   ✅ 找到現有區塊，準備清空並覆寫...")
        target_start = separators[-2]
        target_end = separators[-1]
        curr = target_start.next_sibling
        while curr and curr != target_end:
            next_node = curr.next_sibling
            if isinstance(curr, Tag) or isinstance(curr, NavigableString): curr.extract()
            curr = next_node
    else:
        print("   ⚠️ 未找到完整區塊，將在頁面最下方新增...")
        target_start = soup.new_tag('p'); target_start.string = SEPARATOR
        target_end = soup.new_tag('p'); target_end.string = SEPARATOR
        soup.append(target_start); soup.append(target_end)
    
    cursor = target_start
    for project_data in summary_data:
        p_name = project_data['project']
        p_items = project_data['items']
        if not p_items: continue
        
        print(f"   👉 [SUMMARY] 寫入專案: {p_name}")
        sys.stdout.flush()
        
        name_tag = soup.new_tag('p')
        strong = soup.new_tag('strong'); strong.string = p_name
        name_tag.append(strong)
        cursor.insert_after(name_tag); cursor = name_tag
        
        for entry_nodes in p_items:
            # Preview Log
            preview_txt = "".join([n.get_text() if hasattr(n, 'get_text') else str(n) for n in entry_nodes]).strip().replace('\n', ' ')
            print(f"      + [寫入] {preview_txt[:60]}...")
            sys.stdout.flush() 

            item_container = soup.new_tag('p')
            for node in entry_nodes: item_container.append(copy.copy(node))
            cursor.insert_after(item_container); cursor = item_container
            
        spacer = soup.new_tag('p'); spacer.append(soup.new_tag('br'))
        cursor.insert_after(spacer); cursor = spacer

    print(f"💾 儲存主週報...")
    url = f"{API_ENDPOINT}/{main_report_data['id']}"
    payload = {
        "version": {"number": main_report_data['version']['number'] + 1, "minorEdit": True},
        "title": main_report_data['title'],
        "type": "page",
        "body": {"storage": {"value": str(soup), "representation": "storage"}}
    }
    requests.put(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), headers=get_headers(), data=json.dumps(payload)).raise_for_status()
    print("✅ 主週報更新成功！")

def main():
    print("=== Confluence Cleaner (V33: Recursive Pruning) ===")
    main_report = find_latest_report()
    targets = extract_all_project_links(main_report['body']['view']['value'])
    if not targets: return
    print(f"📋 找到 {len(targets)} 個專案")
    summary_collection = []
    for t in targets:
        print(f"\n🚀 {t['name']}")
        p = None
        if 'id' in t: p = get_page_by_id(t['id'])
        elif 'title' in t:
            print(f"   使用解析標題: {t['title']}")
            p = get_page_by_title(t['title'])
        if not p: print("❌ 讀取失敗"); continue
        new_c, red_items = clean_project_page_content(p['body']['storage']['value'], p['title'])
        if red_items:
            summary_collection.append({'project': t['name'], 'items': red_items})
        if new_c: update_page(p, new_c)
        else: print("👌 專案頁面無需變更")
    print("-" * 30)
    if summary_collection: update_main_report_summary(main_report, summary_collection)
    else: print("📭 沒有紅字摘要，跳過更新。")

if __name__ == "__main__":
    main()
