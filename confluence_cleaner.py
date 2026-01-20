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

# --- V38: 複製與修剪邏輯 ---

def is_date_header(text):
    if not text: return False
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text[:50]))

# 檢查節點本身是否帶有紅色屬性 (精確定義)
def is_style_red(tag):
    if not isinstance(tag, Tag): return False
    red_patterns = [
        r'color:\s*red', r'#ff0000', r'#de350b', r'#bf2600', r'#ff5630', r'#ce0000', 
        r'#c9372c', r'#C9372C', 
        r'rgb\(\s*255', r'rgb\(\s*222', r'rgb\(\s*201', r'rgb\(\s*191', 
        r'--ds-text-danger', r'--ds-icon-accent-red'
    ]
    combined_regex = re.compile('|'.join(red_patterns), re.IGNORECASE)
    
    if tag.has_attr('style') and combined_regex.search(tag['style']): return True
    if tag.name == 'font' and tag.has_attr('color') and combined_regex.search(tag['color']): return True
    return False

# 檢查一個文字節點的父層鏈中是否有紅色樣式
def is_context_red(node):
    curr = node.parent
    while curr and curr.name not in ['td', 'body', 'html']:
        if is_style_red(curr): return True
        curr = curr.parent
    return False

# 【V38 核心】：修剪樹 (Prune Tree)
# 直接在傳入的 soup 物件上進行修改，移除黑字
def prune_non_red_content(soup_fragment):
    # 1. 找出所有文字節點 (Leaf Nodes)
    # 我們使用 list() 強制取出所有節點，避免在遍歷時修改結構導致跳過
    text_nodes = [t for t in soup_fragment.find_all(string=True)]
    
    for text_node in text_nodes:
        if not text_node.strip(): continue # 忽略空白排版
        
        # 判斷保留條件
        is_date = is_date_header(str(text_node))
        is_red = is_context_red(text_node)
        
        # 如果不是日期，且不是紅色 -> 它是黑字 -> 刪除
        if not is_date and not is_red:
            text_node.extract()

    # 2. 清理空容器 (Empty Containers)
    # 文字刪除後，可能會剩下空的 <p></p> 或 <li></li>，需要移除
    # 重複執行直到沒有空容器為止 (因為刪除子節點可能導致父節點變空)
    while True:
        # 尋找空標籤 (沒有文字內容且沒有圖片等其他資源)
        # 注意：<br> 換行符號如果不被保留，排版會亂，所以要小心
        # 這裡策略：如果標籤內沒有任何可見文字，就刪除
        
        # 找出所有標籤，由深到淺
        tags = soup_fragment.find_all(True)
        removed_count = 0
        
        for tag in tags:
            # 跳過 <br>, <img> 等空元素
            if tag.name in ['br', 'img', 'hr']: continue
            
            # 檢查是否還有內容
            if not tag.get_text(strip=True):
                # 確實空了，刪除
                tag.extract()
                removed_count += 1
        
        if removed_count == 0: break

    return soup_fragment

def split_cell_content(cell_soup):
    entries = []
    current_entry = []
    
    # 這裡的邏輯要稍微放寬，因為我們現在是整塊複製，
    # split 主要只是為了配合既有的程式架構計算 KEEP_LIMIT。
    # 為了保持格式，我們其實不需要真的 split 並重組，
    # 而是應該把整個 Cell 複製下來，然後修剪。
    
    # 但是，使用者的需求是 "只取前 5 個項目"。
    # 所以我們還是得辨識出 "項目"。
    
    # 簡單起見，V38 策略：
    # 1. 複製整個 Cell 內容。
    # 2. 對複製品進行「修剪黑字」。
    # 3. 修剪完後，內容已經是乾淨的紅字了。
    # 4. 直接把這個乾淨的內容當作一個 "大項目" 回傳即可。
    # 5. 這樣可以完美保留原本的排版。
    
    return [cell_soup] # 偽裝成一個項目，由外部處理

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

    print(f"   🔍 [{page_title}] 找到主表格，開始複製與修剪...")
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

        # 【V38 核心邏輯】
        # 1. 深層複製整個 Cell (保留所有格式：ul, li, strong, style...)
        cell_clone = copy.copy(update_cell) # copy Tag 會連同子樹一起複製
        
        # 2. 執行修剪：刪除所有黑字
        pruned_content = prune_non_red_content(cell_clone)
        
        # 3. 檢查修剪後是否還有實質內容
        if pruned_content.get_text(strip=True):
            # 這裡我們把修剪後的內容包裝成一個 list 傳出去
            # 為了配合 update_main_report_summary 的介面 (它預期一組 nodes)
            extracted_summary_items.append(list(pruned_content.contents))

    print(f"\r      Scanning Row {total_rows}/{total_rows} (Done)        ")
    if extracted_summary_items:
        print(f"      📌 本專案採集到紅字摘要 (格式保留)")
    
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
        p_items = project_data['items'] # 這是一堆 list of nodes
        if not p_items: continue
        
        print(f"   👉 [SUMMARY] 寫入專案: {p_name}")
        sys.stdout.flush()
        
        name_tag = soup.new_tag('p')
        strong = soup.new_tag('strong'); strong.string = p_name
        name_tag.append(strong)
        cursor.insert_after(name_tag); cursor = name_tag
        
        # 由於 p_items 現在是保留了完整結構的 fragments
        # 我們不要用 <p> 硬包，而是用 <div> 保持結構
        for entry_nodes in p_items:
            # entry_nodes 是一個 list，裡面可能是 <ul>, <p>, text 等混合
            container = soup.new_tag('div')
            for node in entry_nodes:
                container.append(copy.copy(node))
            
            cursor.insert_after(container); cursor = container
            
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
    print("=== Confluence Cleaner (V38: Clone & Prune) ===")
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
