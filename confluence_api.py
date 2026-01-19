import os
import requests
import json
import re
import sys
from datetime import datetime, timedelta
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse

# --- 設定區 ---
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("錯誤：缺少環境變數")
    sys.exit(1)

parsed = urlparse(RAW_URL)
BASE_URL = f"{parsed.scheme}://{parsed.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def find_latest_report():
    print("正在搜尋最新週報...")
    cql = 'type=page AND title ~ "WeeklyReport*" ORDER BY created DESC'
    url = f"{API_ENDPOINT}/search"
    params = {'cql': cql, 'limit': 1, 'expand': 'body.storage,ancestors,space'}
    
    try:
        response = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        response.raise_for_status()
        results = response.json().get('results', [])
        if not results:
            print("⚠️ 找不到任何基準週報。")
            sys.exit(1)
        latest = results[0]
        print(f"✅ 找到基準週報: {latest['title']} (ID: {latest['id']})")
        return latest
    except Exception as e:
        print(f"❌ 搜尋失敗: {e}")
        sys.exit(1)

def calculate_next_date(latest_title):
    """
    邏輯：
    1. 從標題解析出日期 (例如 20260130)。
    2. 下一期檔名 = 該日期 + 7天。
    3. JQL 開始日 (Monday) = 下一期檔名日期 - 4天 (因為檔名通常是週五)。
    4. JQL 結束日 (Sunday) = 下一期檔名日期 + 2天。
    
    例如檔名是 1/30 (週五):
    - 週一 = 1/26
    - 週日 = 2/1
    """
    match = re.search(r"(\d{8})", latest_title)
    if match:
        last_date_str = match.group(1)
        try:
            last_date_obj = datetime.strptime(last_date_str, "%Y%m%d").date()
            
            # 下一期週報的日期 (週五)
            next_report_date = last_date_obj + timedelta(days=7)
            
            # 計算該週的區間 (假設週報檔名是週五)
            # Monday is 4 days before Friday
            monday = next_report_date - timedelta(days=4)
            # Sunday is 2 days after Friday
            sunday = next_report_date + timedelta(days=2)
            
            return {
                "filename": next_report_date.strftime("%Y%m%d"),
                "monday_str": monday.strftime("%Y-%m-%d"),
                "sunday_str": sunday.strftime("%Y-%m-%d")
            }
        except ValueError: pass
            
    print("⚠️ 無法解析日期，使用本週五為基準。")
    today = datetime.now().date()
    # 假設今天是執行日，算出本週五
    friday = today + timedelta(days=(4 - today.weekday()))
    monday = friday - timedelta(days=4)
    sunday = friday + timedelta(days=2)
    
    return {
        "filename": friday.strftime("%Y%m%d"),
        "monday_str": monday.strftime("%Y-%m-%d"),
        "sunday_str": sunday.strftime("%Y-%m-%d")
    }

def update_jql_dates(content, new_monday, new_sunday):
    """
    強大的 JQL 日期替換函數
    目標：找到內容中所有的 updated >= "YYYY-MM-DD" 和 updated <= "YYYY-MM-DD"
    並將其替換為新的週一和週日。
    """
    print(f"正在將 JQL 日期更新為: {new_monday} ~ {new_sunday}")
    
    # 正則表達式解釋：
    # 尋找類似 updated >= "2026-01-26" 這樣的模式
    # 使用捕獲組 () 來保留前面的語法，只替換日期部分
    
    # 替換起始日 (>= "YYYY-MM-DD")
    # 這裡匹配： updated >= " 或 updated >= ' 或 updated>= 
    # 為了簡單，我們直接匹配日期格式並假設成對出現
    
    # 方法 A: 簡單暴力替換所有日期
    # 但這可能會誤傷內文中單純的文字日期。
    
    # 方法 B: 針對 JQL 結構替換 (更安全)
    # 我們假設 JQL 結構是 updated >= "舊日期" ... updated <= "舊日期"
    # 但舊日期可能每一行都不一樣（如果有人手動改錯過）
    # 所以最好的方法是：
    # 1. 找出所有 >= "YYYY-MM-DD" -> 換成 >= "新週一"
    # 2. 找出所有 <= "YYYY-MM-DD" -> 換成 <= "新週日"
    
    # 替換 >= (Start Date)
    # pattern_start 尋找： (updated\s*>=\s*["'])(\d{4}-\d{2}-\d{2})(["'])
    # \s* 代表可能有的空白
    pattern_start = re.compile(r'(updated\s*>=\s*["\\]*)(\d{4}-\d{1,2}-\d{1,2})(["\\]*)', re.IGNORECASE)
    content = pattern_start.sub(f'\\g<1>{new_monday}\\g<3>', content)
    
    # 替換 <= (End Date)
    pattern_end = re.compile(r'(updated\s*<=\s*["\\]*)(\d{4}-\d{1,2}-\d{1,2})(["\\]*)', re.IGNORECASE)
    content = pattern_end.sub(f'\\g<1>{new_sunday}\\g<3>', content)
    
    # 額外保險：有時候 JQL 可能是 created >= ...
    # 如果您的 JQL 只有 updated，上面的就夠了。
    
    return content

def create_new_report(latest_page):
    # 1. 計算日期
    next_dates = calculate_next_date(latest_page['title'])
    new_title = f"WeeklyReport_{next_dates['filename']}"
    print(f"準備建立: {new_title}")
    print(f"新週期: {next_dates['monday_str']} (一) ~ {next_dates['sunday_str']} (日)")
    
    # 2. 檢查重複
    check_url = f"{API_ENDPOINT}/search"
    check_params = {'cql': f'title = "{new_title}"'}
    check_resp = requests.get(check_url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=check_params)
    if check_resp.json().get('results'):
        print(f"⚠️ 跳過：頁面 '{new_title}' 已經存在！")
        return

    # 3. 處理內容與日期替換
    original_body = latest_page['body']['storage']['value']
    
    # 呼叫我們新寫的函數，處理所有 Jira Macro
    new_body = update_jql_dates(original_body, next_dates['monday_str'], next_dates['sunday_str'])
    
    # 4. 建立頁面
    ancestors = []
    if latest_page.get('ancestors'):
        ancestors.append({'id': latest_page['ancestors'][-1]['id']})
    
    payload = {
        "title": new_title,
        "type": "page",
        "space": {"key": latest_page['space']['key']},
        "ancestors": ancestors,
        "body": {
            "storage": {
                "value": new_body,
                "representation": "storage"
            }
        }
    }
    
    try:
        response = requests.post(
            API_ENDPOINT, 
            auth=HTTPBasicAuth(USERNAME, API_TOKEN),
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload)
        )
        response.raise_for_status()
        data = response.json()
        webui = data['_links']['webui']
        link = f"{BASE_URL}/wiki{webui}" if not webui.startswith('/wiki') else f"{BASE_URL}{webui}"
        
        print(f"🎉 成功建立！所有 Jira 表格日期已更新。")
        print(f"連結: {link}")
        
    except requests.exceptions.HTTPError as e:
        print(f"❌ 建立失敗: {e}")
        print(response.text)

def main():
    print(f"=== Confluence API 自動週報 (v6.0 全面自動化版) ===")
    latest_page = find_latest_report()
    create_new_report(latest_page)

if __name__ == "__main__":
    main()
