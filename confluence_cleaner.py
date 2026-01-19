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
    """找到最新的週報，用來抓取 Project 列表"""
    print("正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    url = f"{API_ENDPOINT}/search"
    params = {'cql': cql, 'limit': 1, 'expand': 'body.storage'}
    
    response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
    response.raise_for_status()
    results = response.json().get('results', [])
    if not results:
        print("⚠️ 找不到週報")
        sys.exit(1)
    return results[0]

def extract_first_project_link(report_body):
    """從週報 HTML 中抓取 Project 欄位的第一個連結"""
    soup = BeautifulSoup(report_body, 'html.parser')
    
    # 假設 Project 列表在第一個表格中
    # 這裡我們尋找包含 "Project" 表頭的表格
    target_link = None
    
    tables = soup.find_all('table')
    for table in tables:
        headers = [th.get_text().strip() for th in table.find_all('th')]
        if "Project" in headers:
            # 找到 Project 欄位是第幾個 (index)
            proj_idx = headers.index("Project")
            
            # 找第一列有資料的 row
            rows = table.find_all('tr')
            for row in rows[1:]: # 跳過表頭
                cols = row.find_all('td')
                if len(cols) > proj_idx:
                    link_tag = cols[proj_idx].find('a')
                    if link_tag:
                        # 抓取 pageId (通常連結是 /wiki/pages/viewpage.action?pageId=xxxx)
                        # 或者 storage format 是 <ac:link><ri:page ri:content-title="WeeklyStatus_BUSGW" /></ac:link>
                        # BeautifulSoup 解析 Storage Format 的 ri:page
                        ri_page = link_tag.find('ri:page')
                        if ri_page and ri_page.get('ri:content-title'):
                            target_title = ri_page.get('ri:content-title')
                            print(f"🎯 鎖定目標專案頁面: {target_title}")
                            return target_title
                        
                        # 備用：如果是傳統 href
                        href = link_tag.get('href')
                        if href and "pageId=" in href:
                            # 這種情況比較少見於 Storage Format，但預防萬一
                            pass
                            
    print("⚠️ 在週報中找不到任何 Project 連結")
    return None

def get_page_by_title(title):
    """透過標題取得頁面資訊"""
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
    # Confluence 紅字通常是 color: rgb(255, 0, 0); 或 #ff0000
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
    history_container = None
    
    if not history_header:
        print("   ℹ️ 找不到 History 區塊，正在建立...")
        history_header = soup.new_tag('h1')
        history_header.string = "History"
        soup.append(history_header)
        # History 之後的內容都算 History 區
    
    # 2. 尋找所有主要項目 (Item) 的區塊
    # 邏輯：通常是 <h4>標題</h4> 接著一個 <table>
    # 我們只處理 History 之前的表格
    
    # 為了避免抓到 History 裡面的表格，我們需要一個停止點
    # 簡單作法：遍歷所有 h4，如果該 h4 在 history_header 之後，就忽略
    
    all_headers = soup.find_all(['h3', 'h4']) # 假設項目標題是 h3 或 h4
    
    changed = False
    
    for header in all_headers:
        # 檢查這個標題是否在 History 之後 (如果是，則不處理，因為那是歸檔區)
        if history_header and header.sourceline > history_header.sourceline:
            continue
            
        header_text = header.get_text().strip()
        # 排除一些非項目的標題
        if header_text.lower() in ['history', 'work item table']:
            continue
            
        # 找這個標題緊接著的表格
        next_node = header.find_next_sibling()
        target_table = None
        while next_node:
            if next_node.name == 'table':
                target_table = next_node
                break
            if next_node.name in ['h1', 'h2', 'h3', 'h4']: # 遇到下一個標題就停
                break
            next_node = next_node.find_next_sibling()
            
        if not target_table:
            continue
            
        print(f"   🔍 檢查項目: {header_text}")
        
        # 3. 處理表格行
        tbody = target_table.find('tbody')
        if not tbody: continue
        
        rows = tbody.find_all('tr')
        if not rows: continue
        
        # 第一列通常是表頭 (Item, Update)，跳過
        data_rows = rows[1:] 
        
        keep_rows = []
        archive_rows = []
        
        count = 0
        for row in data_rows:
            # 規則 B: 紅字絕對保留
            if is_red_row(row):
                keep_rows.append(row)
                # 紅字不佔用計數名額 (根據您的需求：紅字是例外)
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
            
            # 3.1 從主表格移除這些行
            for row in archive_rows:
                row.extract() # 從 HTML 樹中拔除
                
            # 3.2 放入 History
            # 找 History 區塊下是否已經有這個標題的表格
            # 這比較難定位，我們採用簡單策略：
            # 在 History Header 之後找同名的 h3/h4
            
            hist_item_header = None
            # 搜尋 history_header 之後的所有兄弟節點
            curr = history_header.next_sibling
            while curr:
                if curr.name in ['h3', 'h4'] and curr.get_text().strip() == header_text:
                    hist_item_header = curr
                    break
                curr = curr.next_sibling
            
            hist_table = None
            if hist_item_header:
                # 找到了，找它下面的表格
                curr = hist_item_header.next_sibling
                while curr:
                    if curr.name == 'table':
                        hist_table = curr
                        break
                    if curr.name in ['h1', 'h2', 'h3', 'h4']: break
                    curr = curr.next_sibling
            else:
                # 沒找到，新建標題和表格
                print(f"      🆕 History 中無 [{header_text}]，正在新建...")
                new_h4 = soup.new_tag(header.name) # 使用跟原本一樣的層級 (h3/h4)
                new_h4.string = header_text
                soup.append(new_h4)
                
                hist_table = soup.new_tag('table')
                # 複製原表格的表頭
                orig_thead = rows[0] # 原本的第一列
                # 注意：這裡要深拷貝表頭，不然會被拔走
                import copy
                new_thead = copy.copy(orig_thead) 
                hist_table.append(new_thead)
                soup.append(hist_table)
            
            # 3.3 貼上資料
            # 確保 hist_table 有 tbody (BeautifulSoup 有時不會自動建)
            if not hist_table.find('tbody'):
                hist_table.append(soup.new_tag('tbody'))
                
            # 如果是新建的表格，第一行要是表頭
            # 這裡簡單處理：直接 append row
            for row in archive_rows:
                hist_table.append(row)
                
    return str(soup) if changed else None

def update_page(page_data, new_content):
    """回存頁面，使用靜默更新"""
    print(f"💾 正在儲存頁面: {page_data['title']} (靜默模式)...")
    
    url = f"{API_ENDPOINT}/{page_data['id']}"
    
    payload = {
        "version": {"number": page_data['version']['number'] + 1, "minorEdit": True}, # minorEdit = 不通知
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
    
    # 1. 找週報
    report = find_latest_report()
    
    # 2. 抓第一個專案連結
    target_title = extract_first_project_link(report['body']['storage']['value'])
    
    if not target_title:
        print("結束：沒有找到可處理的專案連結。")
        return

    # 3. 讀取該專案頁面
    page_data = get_page_by_title(target_title)
    if not page_data:
        print(f"❌ 錯誤：無法透過標題 '{target_title}' 找到頁面 ID")
        return
        
    print(f"📖 讀取頁面內容: {target_title} (ID: {page_data['id']})")
    
    # 4. 執行清理邏輯
    new_content = clean_project_page_content(page_data['body']['storage']['value'])
    
    # 5. 回存 (如果有變更)
    if new_content:
        update_page(page_data, new_content)
    else:
        print("👌 頁面無需變更 (沒有超過限制的舊資料，或全部都是紅字)")

if __name__ == "__main__":
    main()
