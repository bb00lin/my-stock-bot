import os
import requests
import json
import re
import sys
from datetime import datetime
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup, Tag

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

def find_latest_report():
    """找到最新的週報 (View 格式)"""
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
    """從 HTML 中抓取 Project 欄位的第一個連結 (強力解析版)"""
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
            
            # 找第一列有資料的 row
            for row in rows[1:]:
                cols = row.find_all('td')
                if len(cols) > proj_idx:
                    link_tag = cols[proj_idx].find('a')
                    
                    if link_tag:
                        # 方法 1: 嘗試抓 data-linked-resource-id (最準)
                        page_id = link_tag.get('data-linked-resource-id')
                        if page_id:
                            print(f"🎯 鎖定目標 (透過 data-id): {page_id}")
                            return {'id': page_id}
                        
                        # 方法 2: 分析 href 網址
                        href = link_tag.get('href', '')
                        print(f"   ℹ️ 分析連結: {href}")
                        
                        # 情況 A: ...?pageId=12345
                        if 'pageId=' in href:
                            parsed_url = urlparse(href)
                            qs = parse_qs(parsed_url.query)
                            if 'pageId' in qs:
                                page_id = qs['pageId'][0]
                                print(f"🎯 鎖定目標 (透過 href pageId): {page_id}")
                                return {'id': page_id}
                        
                        # 情況 B: /pages/12345/Title
                        match = re.search(r'/pages/(\d+)/', href)
                        if match:
                            page_id = match.group(1)
                            print(f"🎯 鎖定目標 (透過 href path): {page_id}")
                            return {'id': page_id}

                        # 方法 3: 如果真的都沒有 ID，只好抓文字 (但這次我們知道這可能不準)
                        title = link_tag.get_text().strip()
                        print(f"⚠️ 警告：無法從連結解析 ID，嘗試使用文字標題: {title}")
                        # 這裡我們做一個大膽的猜測：如果文字是 'AhGW'，通常標題是 'WeeklyStatus_AhGW'
                        # 但為了保險，我們先回傳文字，讓後面 try error
                        return {'title': title}

    print("⚠️ 找不到 Project 連結")
    return None

def get_page_by_id(page_id):
    """透過 ID 取得頁面資訊"""
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version'}
    resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    if resp.status_code == 200:
        return resp.json()
    print(f"❌ 透過 ID {page_id} 找不到頁面")
    return None

def get_page_by_title(title):
    """透過標題取得頁面資訊"""
    url = f"{API_ENDPOINT}"
    params = {'title': title, 'expand': 'body.storage,version'}
    resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    results = resp.json().get('results', [])
    if results:
        return results[0]
    
    # 自動嘗試補上 WeeklyStatus_ 前綴 (針對您的命名習慣做的補救)
    if not title.startswith("WeeklyStatus_"):
        alt_title = f"WeeklyStatus_{title}"
        print(f"   嘗試猜測標題: {alt_title}")
        params['title'] = alt_title
        resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        results = resp.json().get('results', [])
        if results:
            print(f"   ✅ 猜測成功！")
            return results[0]

    return None

def is_red_row(tr):
    """判斷紅字"""
    tags_with_style = tr.find_all(lambda tag: tag.has_attr('style'))
    for tag in tags_with_style:
        style = tag['style'].lower()
        if 'rgb(255, 0, 0)' in style or '#ff0000' in style:
            return True
    if tr.find('font', color="red") or tr.find('font', color="#ff0000"):
        return True
    return False

def clean_project_page_content(html_content):
    """核心邏輯：瘦身 + 歸檔"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    history_header = soup.find(lambda tag: tag.name in ['h1', 'h2'] and 'History' in tag.get_text())
    
    if not history_header:
        print("   ℹ️ 找不到 History 區塊，正在建立...")
        history_header = soup.new_tag('h1')
        history_header.string = "History"
        soup.append(history_header)
    
    all_headers = soup.find_all(['h3', 'h4']) 
    changed = False
    
    for header in all_headers:
        # 簡單判定：如果在 History 之後就不處理
        if history_header and header.sourceline and history_header.sourceline:
             if header.sourceline > history_header.sourceline: continue
            
        header_text = header.get_text().strip()
        if header_text.lower() in ['history', 'work item table']: continue
            
        next_node = header.find_next_sibling()
        target_table = None
        while next_node:
            if next_node.name == 'table':
                target_table = next_node
                break
            if next_node.name in ['h1', 'h2', 'h3', 'h4']: break
            next_node = next_node.find_next_sibling()
            
        if not target_table: continue
            
        print(f"   🔍 檢查項目: {header_text}")
        
        tbody = target_table.find('tbody')
        if not tbody: continue
        rows = tbody.find_all('tr')
        if not rows: continue
        
        data_rows = rows[1:] 
        keep_rows = []
        archive_rows = []
        
        count = 0
        for row in data_rows:
            if is_red_row(row):
                keep_rows.append(row)
                print("      🔴 發現紅字，強制保留")
                continue
            
            if count < KEEP_LIMIT:
                keep_rows.append(row)
                count += 1
            else:
                archive_rows.append(row)
        
        if archive_rows:
            print(f"      ✂️ 需歸檔 {len(archive_rows)} 筆資料...")
            changed = True
            
            for row in archive_rows:
                row.extract()
                
            # 放入 History
            hist_item_header = None
            curr = history_header.next_sibling
            while curr:
                if curr.name in ['h3', 'h4'] and curr.get_text().strip() == header_text:
                    hist_item_header = curr
                    break
                curr = curr.next_sibling
            
            hist_table = None
            if hist_item_header:
                curr = hist_item_header.next_sibling
                while curr:
                    if curr.name == 'table':
                        hist_table = curr
                        break
                    if curr.name in ['h1', 'h2', 'h3', 'h4']: break
                    curr = curr.next_sibling
            else:
                print(f"      🆕 History 中無 [{header_text}]，正在新建...")
                new_h4 = soup.new_tag(header.name)
                new_h4.string = header_text
                soup.append(new_h4)
                
                hist_table = soup.new_tag('table')
                orig_thead = rows[0]
                import copy
                new_thead = copy.copy(orig_thead) 
                hist_table.append(new_thead)
                soup.append(hist_table)
            
            if not hist_table.find('tbody'):
                hist_table.append(soup.new_tag('tbody'))
                
            for row in archive_rows:
                hist_table.append(row)
                
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
    print("=== Confluence 專案頁面整理機器人 (Test Mode: 1st Link) ===")
    
    report = find_latest_report()
    target_info = extract_first_project_link(report['body']['view']['value'])
    
    if not target_info:
        print("結束：沒有找到可處理的專案連結。")
        return

    # 這裡做了雙重保險：有 ID 用 ID，沒 ID 用標題猜
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
