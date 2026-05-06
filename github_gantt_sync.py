import os
import json
import urllib.parse
import requests
from datetime import datetime, timezone, timedelta
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup

# --- 1. 環境變數與金鑰設定 (對齊你的 GitHub Secrets) ---
# [終極淨化器] 確保 URL 沒有多餘的空白、斜線，並強制移除可能重複的 /wiki
raw_url = os.environ.get("CONF_URL", "").strip().rstrip('/')
if raw_url.endswith("/wiki"):
    raw_url = raw_url[:-5]
JIRA_URL = raw_url

ADMIN_EMAIL = os.environ.get("CONF_USER", "").strip()
ADMIN_TOKEN = os.environ.get("CONF_PASS", "").strip()

if not JIRA_URL or not ADMIN_EMAIL or not ADMIN_TOKEN:
    print("❌ 錯誤：找不到環境變數 CONF_URL, CONF_USER 或 CONF_PASS")
    exit(1)

ADMIN_AUTH = HTTPBasicAuth(ADMIN_EMAIL, ADMIN_TOKEN)
CONFLUENCE_API_URL = f"{JIRA_URL}/wiki/rest/api"

# 目標空間與母頁面 ID (從您提供的網址提取，最為精準)
SPACE_KEY = "teamAIoTHW"
PARENT_PAGE_ID = "151684319"

# --- 2. 時區與今天日期計算 (台灣 UTC+8) ---
tw_tz = timezone(timedelta(hours=8))
# 取得台灣當下時間的凌晨 00:00:00
today_start = datetime.now(tw_tz).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)

def get_parent_page_by_id(page_id):
    """直接透過精確的 Page ID 抓取母頁面，避免名稱比對錯誤"""
    url = f"{CONFLUENCE_API_URL}/content/{page_id}"
    print(f"🔗 正在請求 API: {url}") 
    res = requests.get(url, params={"expand": "body.storage,version"}, auth=ADMIN_AUTH)
    
    if res.status_code != 200:
        print(f"❌ 取得母頁面失敗，HTTP 狀態碼: {res.status_code}")
        print(f"❌ 伺服器回傳內容: {res.text[:500]}")
        return None
    return res.json()

def get_child_pages(parent_id):
    url = f"{CONFLUENCE_API_URL}/content/{parent_id}/child/page"
    res = requests.get(url, params={"expand": "body.storage,version", "limit": 100}, auth=ADMIN_AUTH)
    
    if res.status_code != 200:
        print(f"❌ 取得子頁面失敗，HTTP 狀態碼: {res.status_code}")
        return []
    return res.json().get('results', [])

def update_page(page_id, new_html, current_version, title):
    payload = {
        "version": {"number": current_version + 1, "minorEdit": True},
        "title": title,
        "type": "page",
        "body": {"storage": {"value": new_html, "representation": "storage"}}
    }
    res = requests.put(f"{CONFLUENCE_API_URL}/content/{page_id}", json=payload, auth=ADMIN_AUTH, headers={"Content-Type": "application/json"})
    return res.status_code == 200

def process_gantt_source(source_str):
    """解碼甘特圖的 source，更新 Today, NA任務, 以及 endDate 邊界"""
    decoded = urllib.parse.unquote(source_str)
    data = json.loads(decoded)
    
    new_marker_date = today_start.strftime('%Y-%m-%d %H:%M:%S')
    new_marker_title = f"Today {today_start.strftime('%m/%d')}"
    
    # 1. 自動延伸甘特圖的 endDate 邊界
    timeline = data.get("timeline", {})
    if timeline and "endDate" in timeline:
        end_date_str = timeline["endDate"]
        try:
            current_end_date = datetime.strptime(end_date_str, '%Y-%m-%d %H:%M:%S')
            if today_start >= current_end_date:
                mode = timeline.get("displayOption", "MONTH")
                if mode == "WEEK":
                    new_end_date = today_start + timedelta(days=14)
                else:
                    next_m = today_start.month + 1 if today_start.month < 12 else 1
                    next_y = today_start.year if today_start.month < 12 else today_start.year + 1
                    nn_m = next_m + 1 if next_m < 12 else 1
                    nn_y = next_y if next_m < 12 else next_y + 1
                    new_end_date = datetime(nn_y, nn_m, 1) - timedelta(days=1)
                
                timeline["endDate"] = new_end_date.strftime('%Y-%m-%d 12:00:00')
                print(f"    ➡️ [邊界延伸] {mode} 模式，endDate 已展延至 {timeline['endDate']}")
        except Exception as e:
            print(f"    ⚠️ 邊界延伸計算失敗: {e}")

    # 2. 更新 Today 標籤 (Marker)
    has_today = False
    if "markers" in data:
        for marker in data["markers"]:
            if str(marker.get("title", "")).startswith("Today"):
                marker["markerDate"] = new_marker_date
                marker["title"] = new_marker_title
                has_today = True
                break
        
        if not has_today:
            data["markers"].append({"markerDate": new_marker_date, "title": new_marker_title})

    # 3. 將無日期任務 ([NA] 前綴) 跟著今天往前移動
    if "lanes" in data:
        for lane in data["lanes"]:
            if "bars" in lane:
                for bar in lane["bars"]:
                    if str(bar.get("title", "")).startswith("[NA]"):
                        bar["startDate"] = today_start.strftime('%Y-%m-%d 12:00:00')

    return urllib.parse.quote(json.dumps(data, separators=(',', ':')))

def main():
    print(f"=== 啟動 GitHub Actions 甘特圖 Today 每日推進任務 ===")
    print(f"📍 系統判定今日 (台灣時間): {today_start.strftime('%Y-%m-%d')}")
    print(f"🎯 目標母頁面 ID: {PARENT_PAGE_ID}")

    # 直接透過 ID 獲取，跳過名稱搜尋可能造成的失誤
    parent_page = get_parent_page_by_id(PARENT_PAGE_ID)
    if not parent_page:
        print(f"❌ 找不到母頁面！請檢查憑證權限是否正確。")
        return

    child_pages = get_child_pages(PARENT_PAGE_ID)
    print(f"🔍 找到 {len(child_pages)} 個子頁面，開始逐一檢查甘特圖...")

    for page in child_pages:
        title = page['title']
        if not title.startswith("[Gantt]"):
            continue

        html_content = page['body']['storage']['value']
        soup = BeautifulSoup(html_content, 'html.parser')
        
        macros = soup.find_all("ac:structured-macro", attrs={"ac:name": "roadmap"})
        
        updated = False
        for macro in macros:
            source_param = macro.find("ac:parameter", attrs={"ac:name": "source"})
            if source_param and source_param.string:
                old_source = source_param.string
                new_source = process_gantt_source(old_source)
                
                if old_source != new_source:
                    source_param.string = new_source
                    updated = True

        if updated:
            success = update_page(page['id'], str(soup), page['version']['number'], title)
            if success:
                print(f"  ✅ 成功推進：{title}")
            else:
                print(f"  ❌ 推進失敗：{title}")
        else:
            print(f"  ⏭️ 無需變更：{title}")

    print("🎉 執行完畢！")

if __name__ == "__main__":
    main()
