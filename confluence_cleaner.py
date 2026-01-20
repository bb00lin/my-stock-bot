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

# --- V37: 線性重組與過濾 ---

def is_date_text(text):
    if not text: return False
    # 寬鬆匹配日期格式 [YYYY/MM/DD]
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text[:50]))

# 檢查節點本身是否帶有紅色屬性 (精確定義)
def is_node_red(node):
    red_patterns = [
        r'color:\s*red', r'#ff0000', r'#de350b', r'#bf2600', r'#ff5630', r'#ce0000', 
        r'#c9372c', r'#C9372C', 
        r'rgb\(\s*255', r'rgb\(\s*222', r'rgb\(\s*201', r'rgb\(\s*191', 
        r'--ds-text-danger', r'--ds-icon-accent-red'
    ]
    combined_regex = re.compile('|'.join(red_patterns), re.IGNORECASE)
    
    # 檢查 style 屬性 或 font color
    if isinstance(node, Tag):
        if node.has_attr('style') and combined_regex.search(node['style']): return True
        if node.name == 'font' and node.has_attr('color') and combined_regex.search(node['color']): return True
        # 遞迴檢查子節點是否有紅色 (如果有子節點是紅的，這整塊就視為含紅)
        # 注意：這裡我們只看「屬性」，內容判斷留給主邏輯
    return False

# 遞迴將 HTML 攤平成「行」 (Nodes List)
# 每一行代表視覺上的一行 (被 br, p, div, li 切開)
def flatten_html_to_lines(node, current_line=None, all_lines=None):
    if current_line is None: current_line = []
    if all_lines is None: all_lines = []
    
    # 區塊元素，強制換行
    block_tags = ['p', 'div', 'li', 'br', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
    
    if isinstance(node, Tag):
        if node.name == 'br':
            if current_line: all_lines.append(current_line[:])
            current_line.clear()
            return
        
        is_block = node.name in block_tags
        if is_block and current_line:
            all_lines.append(current_line[:])
            current_line.clear()
            
        # 遞迴處理子節點
        for child in node.contents:
            flatten_html_to_lines(child, current_line, all_lines)
            
        if is_block and current_line:
            all_lines.append(current_line[:])
            current_line.clear()
            
    elif isinstance(node, NavigableString):
        if node.strip():
            # 複製節點以保留原始屬性 (顏色等)
            # 注意：NavigableString 本身沒顏色，顏色在父層。
            # 這裡我們需要一個技巧：保留父層的樣式資訊。
            # V37 簡化：直接存 node，之後判斷時往上找 parent 或在 flatten 時傳遞 context。
            # 但因為 BeautifulSoup 的 parent 屬性是動態的，copy 後會遺失。
            # 所以我們不 copy，直接存引用。
            current_line.append(node)

    return all_lines

# 檢查一個節點(及其父層)是否為紅色
def is_element_red_context(element):
    # 往上找直到 table cell (td)
    curr = element
    while curr and curr.name != 'td' and curr.name != 'body':
        if is_node_red(curr): return True
        curr = curr.parent
    return False

def clean_project_page_content(html_content, page_title):
    soup = BeautifulSoup(html_content, 'lxml')
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

    print(f"   🔍 [{page_title}] 找到主表格，執行線性重組...")
    sys.stdout.flush()
    rows = main_table.find_all('tr', recursive=False)
    if not rows and main_table.find('tbody', recursive=False):
        rows = main_table.find('tbody', recursive=False).find_all('tr', recursive=False)
    if not rows: return None, []

    header_row = rows[0]
    headers = [c.get_text().strip() for c in header_row.find_all(['th', 'td'], recursive=False)]
    try: item_idx = headers.index("Item"); update_idx = headers.index("Update")
    except ValueError: return None, []

    total_rows = len(rows) - 1
    
    for i, row in enumerate(rows[1:]):
        sys.stdout.write(f"\r      Scanning Row {i+1}/{total_rows} ...")
        sys.stdout.flush()
        cols = row.find_all('td', recursive=False)
        if len(cols) <= max(item_idx, update_idx): continue
        
        update_cell = cols[update_idx]
        if update_cell.find('table'): continue

        # --- V37 核心：線性分組邏輯 ---
        
        # 1. 取得所有「行」 (視覺上的每一行文字)
        raw_lines = []
        flatten_html_to_lines(update_cell, None, raw_lines)
        
        # 2. 進行分組 (按日期切分)
        groups = []
        current_group = {'header': [], 'items': []} # header 是節點列表, items 是列表的列表
        
        for line_nodes in raw_lines:
            # 取得這一行的純文字
            line_text = "".join([str(n) for n in line_nodes]).strip()
            
            if is_date_text(line_text):
                # 遇到新日期 -> 結算上一組
                if current_group['header']:
                    groups.append(current_group)
                
                # 開啟新組
                current_group = {'header': line_nodes, 'items': []}
            else:
                # 內容行 -> 加入當前組
                if line_nodes:
                    current_group['items'].append(line_nodes)
        
        # 加入最後一組
        if current_group['header']:
            groups.append(current_group)
            
        # 3. 過濾組 (只保留紅字項目)
        for group in groups:
            header_nodes = group['header']
            item_lines = group['items']
            
            valid_items = []
            
            # 檢查每個項目行是否為紅字
            for line_nodes in item_lines:
                is_line_red = False
                for node in line_nodes:
                    if is_element_red_context(node):
                        is_line_red = True
                        break
                
                if is_line_red:
                    valid_items.append(line_nodes)
            
            # 檢查標題是否為紅字
            header_is_red = False
            for node in header_nodes:
                if is_element_red_context(node):
                    header_is_red = True
                    break
            
            # 規則：如果有紅字項目，或者標題本身是紅的 -> 保留
            if valid_items or header_is_red:
                # 重組這個 Entry
                # 格式：Header + <br> + Item1 + <br> + Item2 ...
                reconstructed_entry = []
                
                # 加入 Header
                # 為了避免引用問題，這裡我們用 deepcopy，但要注意 NavigableString 的 context
                # 簡單起見，我們只複製節點本身，因為我們已經判定過顏色了
                for n in header_nodes: reconstructed_entry.append(copy.copy(n))
                
                # 加入 Items
                for item_line in valid_items:
                    reconstructed_entry.append(soup.new_tag('br')) # 換行
                    for n in item_line: reconstructed_entry.append(copy.copy(n))
                
                extracted_summary_items.append(reconstructed_entry)

    print(f"\r      Scanning Row {total_rows}/{total_rows} (Done)        ")
    if extracted_summary_items:
        print(f"      📌 本專案採集到 {len(extracted_summary_items)} 組紅字摘要")
    
    return None, extracted_summary_items # Read-Only

def update_page(page_data, new_content):
    pass

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
    print("=== Confluence Cleaner (V37: Linear Reconstructor) ===")
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
        print("👌 專案頁面無需變更 (唯讀模式)")

    print("-" * 30)
    if summary_collection: update_main_report_summary(main_report, summary_collection)
    else: print("📭 沒有紅字摘要，跳過更新。")

if __name__ == "__main__":
    main()
