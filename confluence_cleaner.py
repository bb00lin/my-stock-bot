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

# --- V32 邏輯 ---

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

# 【V32 關鍵修改】：精確定義紅色，排除黑色 (rgb 23...)
def is_node_red(node):
    red_patterns = [
        r'color:\s*red', r'#ff0000', r'#de350b', r'#bf2600', r'#ff5630', r'#ce0000', 
        r'#c9372c', r'#C9372C', 
        r'rgb\(\s*255', # 255, x, x
        r'rgb\(\s*222', # 222, x, x
        r'rgb\(\s*201', # 201, x, x
        r'rgb\(\s*191', # 191, x, x
        r'--ds-text-danger', # Atlassian Variable
        r'--ds-icon-accent-red'
    ]
    # 移除之前的 r'color:\s*rgb\(\s*2'，因為那會匹配到 rgb(23,...)
    
    combined_regex = re.compile('|'.join(red_patterns), re.IGNORECASE)
    return bool(combined_regex.search(str(node)))

def deep_clean_node(node):
    if not isinstance(node, Tag):
        return node, is_node_red(node)

    if node.name in ['ul', 'ol']:
        new_list = BeautifulSoup(features='lxml').new_tag(node.name)
        has_red_child = False
        for li in node.find_all('li', recursive=False):
            if is_node_red(li):
                new_list.append(copy.copy(li))
                has_red_child = True
        if has_red_child: return new_list, True
        else: return None, False
    
    if is_node_red(node): return node, True
    else: return node, False

def filter_entry_red_only_deep(entry_nodes):
    if not entry_nodes: return None
    header = entry_nodes[0]
    body_nodes = entry_nodes[1:]
    kept_body = []
    
    for node in body_nodes:
        cleaned_node, kept = deep_clean_node(node)
        if kept and cleaned_node: kept_body.append(cleaned_node)
    
    header_is_red = is_node_red(header)
    if kept_body: return [header] + kept_body
    elif header_is_red: return [header]
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
        
        filtered_entries = []
        for entry in entries:
            clean_entry = filter_entry_red_only_deep(entry)
            if clean_entry:
                filtered_entries.append(clean_entry)
                extracted_summary_items.append(copy.deepcopy(clean_entry))

        keep = filtered_entries[:KEEP_LIMIT]
        archive = filtered_entries[KEEP_LIMIT:]
        
        # 只要原始不為空，我們就覆寫（為了刪除黑字）
        if not entries: continue 

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
    sys.stdout.flush() # 強制刷新 Log
    
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
        sys.stdout.flush() # 強制刷新
        
        name_tag = soup.new_tag('p')
        strong = soup.new_tag('strong'); strong.string = p_name
        name_tag.append(strong)
        cursor.insert_after(name_tag); cursor = name_tag
        
        for entry_nodes in p_items:
            # Preview Log
            preview_txt = "".join([n.get_text() if hasattr(n, 'get_text') else str(n) for n in entry_nodes]).strip().replace('\n', ' ')
            print(f"      + [寫入] {preview_txt[:60]}...")
            sys.stdout.flush() # 強制刷新

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
    print("=== Confluence Cleaner (V32: Precision Color & Flush) ===")
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
