import os
import requests
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone, timedelta
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# 載入自訂的環境變數檔案 (本地端測試用)
load_dotenv("jira_config.txt")

# --- 1. 環境變數與金鑰設定 (對齊 GitHub Secrets) ---
raw_url = os.environ.get("CONF_URL", "").strip()
parsed = urlparse(raw_url)
# ✅ 網址清洗器：參考填表機實作，確保主網域純淨
JIRA_URL = f"{parsed.scheme}://{parsed.netloc}"

ADMIN_EMAIL = os.environ.get("CONF_USER")
ADMIN_TOKEN = os.environ.get("CONF_PASS")

if not raw_url or not ADMIN_EMAIL or not ADMIN_TOKEN:
    print("❌ 錯誤：找不到環境變數 CONF_URL, CONF_USER 或 CONF_PASS")
    sys.exit(1)

ADMIN_AUTH = HTTPBasicAuth(ADMIN_EMAIL, ADMIN_TOKEN)
CONFLUENCE_API_URL = f"{JIRA_URL}/wiki/rest/api"

# 目標參數
SPACE_KEY = "teamAIoTHW"
PARENT_PAGE_ID = "151684319" # 您指定的 Project Gantt Chart 頁面 ID

# --- 2. 時區與今天日期計算 (對齊填表機：台灣 UTC+8) ---
tw_tz = timezone(timedelta(hours=8))
now_tpe = datetime.now(tw_tz)
today_start = now_tpe.replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)

def get_page_data(page_id):
    url = f"{CONFLUENCE_API_URL}/content/{page_id}"
    res = requests.get(url, params={"expand": "body.storage,version"}, auth=ADMIN_AUTH)
    if res.status_code == 200:
        return res.json()
    print(f"❌ 無法讀取頁面 {page_id}, 狀態碼: {res.status_code}")
    return None

def get_child_pages(parent_id):
    url = f"{CONFLUENCE_API_URL}/content/{parent_id}/child/page"
    res = requests.get(url, params={"limit": 100}, auth=ADMIN_AUTH)
    if res.status_code == 200:
        return res.json().get('results', [])
    return []

def process_gantt_json(source_str):
    """解碼甘特圖配置，執行 Today 與 NA 推進邏輯"""
    try:
        decoded = urllib.parse.unquote(source_str)
        data = json.loads(decoded)
        
        target_marker_date = today_start.strftime('%Y-%m-%d %H:%M:%S')
        target_marker_title = f"Today {today_start.strftime('%m/%d')}"
        
        # 1. 處理時間軸邊界 (endDate) 延伸
        timeline = data.get("timeline", {})
        if "endDate" in timeline:
            current_end = datetime.strptime(timeline["endDate"], '%Y-%m-%d %H:%M:%S')
            if today_start >= current_end:
                mode = timeline.get("displayOption", "MONTH")
                if mode == "WEEK":
                    new_end = today_start + timedelta(days=14)
                else:
                    # 推到下個月底
                    nm = today_start.month + 1 if today_start.month < 12 else 1
                    ny = today_start.year if today_start.month < 12 else today_start.year + 1
                    nnm = nm + 1 if nm < 12 else 1
                    nny = ny if nm < 12 else ny + 1
                    new_end = datetime(nny, nnm, 1) - timedelta(days=1)
                timeline["endDate"] = new_end.strftime('%Y-%m-%d 12:00:00')
                print(f"    ➡️ [自動延展] 邊界推至 {timeline['endDate']}")

        # 2. 更新 Today 紅色標籤 (Markers)
        if "markers" in data:
            found_today = False
            for m in data["markers"]:
                if str(m.get("title", "")).startswith("Today"):
                    m["markerDate"] = target_marker_date
                    m["title"] = target_marker_title
                    found_today = True
            if not found_today:
                data["markers"].append({"markerDate": target_marker_date, "title": target_marker_title})

        # 3. 推進 [NA] 任務 (Bars)
        if "lanes" in data:
            for lane in data["lanes"]:
                for bar in lane.get("bars", []):
                    if str(bar.get("title", "")).startswith("[NA]"):
                        bar["startDate"] = today_start.strftime('%Y-%m-%d 12:00:00')

        return urllib.parse.quote(json.dumps(data, separators=(',', ':')))
    except Exception as e:
        print(f"    ⚠️ 解析甘特圖資料失敗: {e}")
        return source_str

def main():
    print(f"=== 啟動 GitHub Actions 甘特圖 Today 推進引擎 ===")
    print(f"📍 台灣時間：{now_tpe.strftime('%Y/%m/%d %H:%M')}")
    
    # 取得子頁面清單
    children = get_child_pages(PARENT_PAGE_ID)
    print(f"🔍 找到 {len(children)} 個子頁面...")

    for child in children:
        c_id, c_title = child['id'], child['title']
        if not c_title.startswith("[Gantt]"): continue
        
        print(f"\n📄 處理頁面: {c_title}")
        page_data = get_page_data(c_id)
        if not page_data: continue

        soup = BeautifulSoup(page_data['body']['storage']['value'], 'html.parser')
        macros = soup.find_all("ac:structured-macro", attrs={"ac:name": "roadmap"})
        
        is_changed = False
        for macro in macros:
            source_param = macro.find("ac:parameter", attrs={"ac:name": "source"})
            if source_param and source_param.string:
                old_val = source_param.string
                new_val = process_gantt_json(old_val)
                if old_val != new_val:
                    source_param.string = new_val
                    is_changed = True

        if is_changed:
            payload = {
                "version": {"number": page_data['version']['number'] + 1, "minorEdit": True},
                "title": c_title, "type": "page",
                "body": {"storage": {"value": str(soup), "representation": "storage"}}
            }
            res = requests.put(f"{CONFLUENCE_API_URL}/content/{c_id}", json=payload, auth=ADMIN_AUTH, headers={"Content-Type": "application/json"})
            if res.status_code == 200:
                print(f"  ✅ 成功更新甘特圖日期")
            else:
                print(f"  ❌ 更新失敗: {res.text}")
        else:
            print(f"  ⏭️ 內容無須推進")

if __name__ == "__main__":
    main()
