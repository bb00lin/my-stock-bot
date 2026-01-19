import os
import requests
import json
import re
import sys
from datetime import datetime
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse
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
    """找到最新的週報，並抓取 View 格式 (為了看見 Macro 產生的表格)"""
    print("正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    url = f"{API_ENDPOINT}/search"
    # 修改點：這裡改抓 'body.view' 而不是 'body.storage'
    params = {'cql': cql, 'limit': 1, 'expand': 'body.view'}
    
    response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    response.raise_for_status()
    results = response.json().get('results', [])
    if not results:
        print("⚠️ 找不到週報")
        sys.exit(1)
    return results[0]

def extract_first_project_link(report_body):
    """從週報 Rendered HTML 中抓取 Project 欄位的第一個連結"""
    soup = BeautifulSoup(report_body, 'html.parser')
    
    tables = soup.find_all('table')
    for table in tables:
        # 尋找表頭
        headers = []
        # 有些表格用 th, 有些用 td class="highlight"
        header_row = table.find('tr')
        if not header_row: continue
        
        for cell in header_row.find_all(['th', 'td']):
            headers.append(cell.get_text().strip())
            
        if "Project" in headers:
            proj_idx = headers.index("Project")
            
            # 找第一列有資料的 row
            rows = table.find_all('tr')
            for row in rows[1:]: # 跳過表頭
                cols = row.find_all('td')
                if len(cols) > proj_idx:
                    # 在 View 模式下，連結就是標準的 <a href="...">
                    link_tag = cols[proj_idx].find('a')
                    
                    if link_tag:
                        # 優先嘗試抓取 Page ID (最準確)
                        # Confluence View 連結通常帶有 data-linked-resource-id
                        page_id = link_tag.get('data-linked-resource-id')
                        
                        if page_id:
                            print(f"🎯 鎖定目標專案 (ID: {page_id})")
                            return {'id': page_id}
                        
                        # 如果沒有 ID，抓文字標題
                        title = link_tag.get_text().strip()
                        if title:
                            print(f"🎯 鎖定目標專案 (Title: {title})")
                            return {'title': title}

    print("⚠️ 在週報中找不到任何 Project 連結 (請確認表格標題是否為 'Project')")
    return None

def get_page_by_id(page_id):
    """透過 ID 取得頁面資訊 (Storage 格式，為了編輯)"""
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version'}
    resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    if resp.status_code == 200:
        return resp.json()
    return None

def get_page_by_title(title):
    """透過標題取得頁面資訊 (Storage 格式，為了編輯)"""
    url = f"{API_ENDPOINT}"
    params = {'title': title, 'expand': 'body.storage,version'}
    resp = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    results = resp.json().get('results', [])
    if results:
        return results[0]
    return None

def is_red_row(tr):
    """判斷這一行是否有紅字"""
    # 檢查 style 屬性中的顏色設定
    tags_with_style = tr.find_all(lambda tag: tag.has_attr('style'))
    for tag in tags_with_style:
        style = tag['style'].lower()
        if 'rgb(255, 0, 0)' in style or '#ff0000' in style:
            return True
    
    # 也有可能是在 <font color="red"> (舊版)
    if tr.find('font', color="red") or tr.find('font', color="#ff0000"):
        return True
        
    return False

def clean_project_page_content(html_content):
    """核心邏輯：瘦身 + 歸檔"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. 確保有 History 區塊
    history_header = soup.find(lambda tag: tag.name in ['h1', 'h2'] and 'History' in tag.get_text())
    
    if not history_header:
        print("   ℹ️ 找不到 History 區塊，正在建立...")
        history_header = soup.new_tag('h1')
        history_header.string = "History"
        soup.append(history_header)
    
    all_headers = soup.find_all(['h3', 'h4']) 
    
    changed = False
    
    for header in all_headers:
        # 檢查這個標題是否在 History 之後 (如果是，則不處理)
        if history_header and header.sourceline and history_header.sourceline:
             if header.sourceline > history_header.sourceline:
                continue
        # 備用：如果 sourceline 沒抓到，用遍歷法判斷 (略)
            
        header_text = header.get_text().strip()
        if header_text.lower() in ['history', 'work item table']:
            continue
            
        # 找這個標題緊接著的表格
        next_node = header.find_next_sibling()
        target_table = None
        while next_node:
            if next_node.name == 'table':
                target_table = next_node
                break
            if next_node.name in ['h1', 'h2', 'h3', 'h4']: 
                break
            next_node = next_node.find_next_sibling()
            
        if not target_table:
            continue
            
        print(f"   🔍 檢查項目: {header_text}")
        
        tbody = target_table.find('tbody')
        if not tbody: continue
        
        rows = tbody.find_all('tr')
        if not rows: continue
        
        # 第一列通常是表頭
        data_rows = rows[1:] 
        
        keep_rows = []
        archive_rows = []
        
        count = 0
        for row in data_rows:
            # 規則 B: 紅字絕對保留
            if is_red_row(row):
                keep_rows.append(row)
                print("      🔴 發現紅字，強制保留")
                continue
            
            # 規則 A: 保留前 N 筆
            if count < KEEP_LIMIT:
                keep_rows.append(row)
                count += 1
            else:
                # 規則 C: 其餘歸檔
                archive_rows.append(row)
        
        if archive_rows:
            print(f"      ✂️ 需歸檔 {len(archive_rows)} 筆資料...")
            changed = True
            
            # 3.1 從主表格移除
            for row in archive_rows:
                row.extract()
                
            # 3.2 放入 History
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
    """回存頁面，使用靜默更新"""
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
    print("=== Confluence 專案頁面整理機器人 (Test Mode: Only 1st Link) ===")
    
    # 1. 找週報 (View 格式)
    report = find_latest_report()
    
    # 2. 抓第一個專案連結
    target_info = extract_first_project_link(report['body']['view']['value'])
    
    if not target_info:
        print("結束：沒有找到可處理的專案連結。")
        return

    # 3. 讀取該專案頁面 (Storage 格式)
    if 'id' in target_info:
        page_data = get_page_by_id(target_info['id'])
    else:
        page_data = get_page_by_title(target_info['title'])
        
    if not page_data:
        print(f"❌ 錯誤：無法找到頁面")
        return
        
    print(f"📖 讀取頁面內容: {page_data['title']} (ID: {page_data['id']})")
    
    # 4. 執行清理邏輯
    new_content = clean_project_page_content(page_data['body']['storage']['value'])
    
    # 5. 回存
    if new_content:
        update_page(page_data, new_content)
    else:
        print("👌 頁面無需變更")

if __name__ == "__main__":
    main()
