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

# --- 1. 搜尋週報與提取所有專案連結 (新邏輯) ---
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

def extract_all_project_links(report_body):
    """
    解析週報內容，找到含有 'Project' 欄位的表格，
    並回傳該欄位中所有的頁面連結列表。
    """
    soup = BeautifulSoup(report_body, 'html.parser')
    tables = soup.find_all('table')
    
    project_targets = []
    found_table = False

    for table in tables:
        # 1. 檢查表頭是否含有 "Project"
        headers = []
        header_row = table.find('tr')
        if not header_row: continue
        
        for cell in header_row.find_all(['th', 'td']):
            headers.append(cell.get_text().strip())
        
        if "Project" in headers:
            print("✅ 找到 Project Status 表格，開始解析專案連結...")
            found_table = True
            proj_idx = headers.index("Project")
            
            # 2. 遍歷該表格的所有列
            rows = table.find_all('tr')
            for row in rows[1:]: # 跳過表頭
                cols = row.find_all('td')
                if len(cols) > proj_idx:
                    # 抓取該格內所有的連結 (可能有由多個專案)
                    links = cols[proj_idx].find_all('a')
                    for link in links:
                        page_id = link.get('data-linked-resource-id')
                        target = {}
                        
                        if page_id:
                            target['id'] = page_id
                            target['name'] = link.get_text().strip()
                        else:
                            # 處理沒有 data-id 的傳統連結
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
                                    # 如果真的找不到 ID，就存標題讓後面去猜
                                    title = link.get_text().strip()
                                    if title:
                                        target['title'] = title
                                        target['name'] = title
                        
                        if target:
                            # 避免重複添加
                            if target not in project_targets:
                                project_targets.append(target)
            
            # 找到第一個符合的表格就停止，不再往下找 (避開 Work Item Table)
            break
    
    if not found_table:
        print("⚠️ 在週報中找不到含有 'Project' 欄位的表格。")
        
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
    
    # 嘗試加上 WeeklyStatus_ 前綴
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
    return bool(re.search(r'\[\d{4}/\d{1,2}/\d{1,2}\]', text))

def has_red_text(tag):
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

def get_clean_item_name(td_tag):
    """
    從 Item 欄位提取乾淨的名稱。
    過濾掉 Status Macro (complete/incomplete) 和圖片。
    """
    # 複製一份以免修改到原始 soup
    temp_tag = copy.copy(td_tag)
    
    # 移除 status macro
    for status in temp_tag.find_all('ac:structured-macro', attrs={"ac:name": "status"}):
        status.decompose()
        
    # 移除 image (有的時候會有 icon)
    for img in temp_tag.find_all('ac:image'):
        img.decompose()
        
    # 取出純文字，並將多餘的空白縮減
    text = temp_tag.get_text(separator=' ', strip=True)
    
    # 如果清空後變沒字了 (例如原本只有一個 status macro)，那還是回傳原本的，避免空白
    if not text:
        return td_tag.get_text(strip=True)
        
    return text

def split_cell_content(cell_soup):
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
        main_thead_row = main_table.find('tr')
        if main_thead_row:
            hist_table.append(copy.copy(main_thead_row))
        body.append(hist_table)
        
    return hist_table

def clean_project_page_content(html_content, page_title):
    soup = BeautifulSoup(html_content, 'html.parser')
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
        print(f"   ⚠️  [{page_title}] 找不到主表格 (Item/Update)，跳過。")
        return None

    print(f"   🔍 [{page_title}] 找到主表格，分析中...")
    
    tbody = main_table.find('tbody') or main_table
    rows = tbody.find_all('tr')
    
    header_row = rows[0]
    headers = [cell.get_text().strip() for cell in header_row.find_all(['th', 'td'])]
    try:
        item_idx = headers.index("Item")
        update_idx = headers.index("Update")
    except ValueError:
        return None

    history_table_ref = None

    for row in rows[1:]:
        cols = row.find_all('td')
        if len(cols) <= max(item_idx, update_idx): continue
        
        # 使用新的函式取得乾淨的 Item Name
        raw_item_cell = cols[item_idx]
        clean_item_name = get_clean_item_name(raw_item_cell)
        
        update_cell = cols[update_idx]
        
        entries = split_cell_content(update_cell)
        if len(entries) <= KEEP_LIMIT:
            continue
            
        print(f"      Item [{clean_item_name}]: 發現 {len(entries)} 筆紀錄，正在清理...")
        
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
            
        print(f"      ✂️  搬移 {len(archive_entries)} 筆舊資料到 History...")
        changed = True
        
        update_cell.clear()
        for entry in keep_entries:
            for node in entry:
                update_cell.append(node)
                
        if history_table_ref is None:
            history_table_ref = get_or_create_history_table(soup, main_table)
            
        hist_rows = history_table_ref.find_all('tr')
        target_hist_row = None
        
        # 在 History 表格中比對 Item Name (同樣用乾淨名稱比對)
        for h_row in hist_rows:
            h_cols = h_row.find_all('td')
            if not h_cols: continue
            
            h_item_name = get_clean_item_name(h_cols[item_idx])
            if h_item_name == clean_item_name:
                target_hist_row = h_row
                break
        
        if not target_hist_row:
            target_hist_row = soup.new_tag('tr')
            for _ in range(len(headers)):
                target_hist_row.append(soup.new_tag('td'))
            
            # 在新的一列填入乾淨的名稱 (不包含 status macro)
            target_hist_row.find_all('td')[item_idx].string = clean_item_name
            history_table_ref.append(target_hist_row)
            
        hist_update_cell = target_hist_row.find_all('td')[update_idx]
        if hist_update_cell.contents:
            hist_update_cell.append(soup.new_tag('br'))
            
        for entry in archive_entries:
            for node in entry:
                hist_update_cell.append(node)

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
    print("=== Confluence 專案頁面整理機器人 (V4: Batch & Clean) ===")
    
    report = find_latest_report()
    
    # 1. 抓取所有目標專案
    project_targets = extract_all_project_links(report['body']['view']['value'])
    
    if not project_targets:
        print("結束：沒有找到任何專案連結。")
        return

    print(f"📋 總共找到 {len(project_targets)} 個專案目標：{[p['name'] for p in project_targets]}")
    print("-" * 30)

    # 2. 批量處理
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
