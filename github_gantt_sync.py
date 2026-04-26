import sys
import os
import requests
import json
import re
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from requests.auth import HTTPBasicAuth
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# --- 1. 環境變數與金鑰設定 (對齊 GitHub Secrets) ---
load_dotenv("jira_config.txt")

# 這裡共用 JIRA 與 Confluence 的網域與驗證 (通常 Atlassian Cloud 是共用的)
JIRA_URL = os.environ.get("CONF_URL", os.environ.get("JIRA_URL", "")).rstrip('/')
ADMIN_EMAIL = os.environ.get("CONF_USER", os.environ.get("JIRA_EMAIL", ""))
ADMIN_TOKEN = os.environ.get("CONF_PASS", os.environ.get("JIRA_TOKEN", ""))

if not JIRA_URL or not ADMIN_EMAIL or not ADMIN_TOKEN:
    print("❌ 錯誤：找不到環境變數 CONF_URL/JIRA_URL, CONF_USER 或 CONF_PASS")
    sys.exit(1)

ADMIN_AUTH = HTTPBasicAuth(ADMIN_EMAIL, ADMIN_TOKEN)
CONFLUENCE_API_URL = f"{JIRA_URL}/wiki/rest/api"

# JIRA 自訂欄位 ID (與您原本的設定一致)
JIRA_START_DATE_FIELD = "customfield_10015" 
JIRA_DUE_DATE_FIELD = "duedate"

# --- 2. 設定檔載入引擎 ---
class SettingsManager:
    def __init__(self, filepath="settings_gantt.json"):
        self.filepath = filepath
        self.config = {
            "confluence_space_key": "team_AIoTHW",      # 目標空間 KEY
            "confluence_page_title": "Project Gantt Chart", # 目標頁面標題
            "target_projects": ["ALL"],                 # 要抓取的 JIRA 專案 (填 "ALL" 抓全部，或填 ["prj_a", "prj_b"])
            "view_mode": "MONTH",                       # WEEK 或 MONTH
            "show_duration": True,
            "show_dates": True,
            "show_assignee": True,
            "show_status": True,
            "show_task_id": True,
            "show_weekends": False,
            "show_nodate": True,
            "status_BLOCKED": True,
            "status_CANDIDATE": True,
            "status_RESUME": True,
            "status_進行中": True,
            "status_WAITING": True,
            "status_ABORT": True,
            "status_完成": False
        }
        self.load_settings()

    def load_settings(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
                print(f"⚙️ 成功讀取 {self.filepath}")
            except Exception as e:
                print(f"⚠️ 讀取設定檔失敗，將使用預設值 ({e})")
        else:
            print(f"⚠️ 找不到 {self.filepath}，將使用預設值")

    def get(self, key, default=None):
        return self.config.get(key, default)

SETTINGS = SettingsManager()

# --- 3. 核心工具與轉換器 ---
def get_canonical_status(raw_status):
    if not raw_status: return "進行中"
    s = str(raw_status).strip().upper()
    if s in ["DONE", "CLOSED", "RESOLVED", "完成", "FINISH"]: return "完成"
    if s in ["WAITING", "PENDING", "HOLD"]: return "WAITING"
    if s in ["BLOCKED"]: return "BLOCKED"
    if s in ["CANDIDATE"]: return "CANDIDATE"
    if s in ["RESUME"]: return "RESUME"
    if s in ["ABORT", "CANCELLED", "CANCELED"]: return "ABORT"
    return "進行中"

def get_short_name(raw_name):
    if not raw_name: return "Unknown"
    name = str(raw_name).strip()
    if name in ["未指派", "Unassigned", ""]: return "Unknown"
    parts = name.replace('.', ' ').split()
    if not parts: return "Unknown"
    first_part = parts[0]
    return first_part.upper() if len(first_part) <= 2 else first_part.capitalize()

def format_workdays(sd, ed):
    import pandas as pd
    workdays = len(pd.bdate_range(start=sd, end=ed))
    weeks, days = workdays // 5, workdays % 5
    parts = []
    if weeks > 0: parts.append(f"{weeks}W")
    if days > 0: parts.append(f"{days}D")
    return f"\"{ ''.join(parts) if parts else '1D' }\""

def parse_jira_date(date_str):
    if not date_str: return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except:
        return None

# --- 4. API 串接引擎 ---
def get_target_projects():
    target_list = SETTINGS.get("target_projects", ["ALL"])
    if "ALL" not in [t.upper() for t in target_list]:
        return target_list
        
    print("🔍 正在獲取 JIRA 中所有專案清單...")
    res = requests.get(f"{JIRA_URL}/rest/api/3/project", auth=ADMIN_AUTH, timeout=10)
    if res.status_code == 200:
        return [p['key'] for p in res.json()]
    else:
        print(f"❌ 獲取專案失敗: {res.text}")
        return []

def fetch_issues_for_project(project_key):
    jql = f'project = "{project_key}" ORDER BY updated DESC'
    fields = ["summary", "issuetype", "status", "parent", "assignee", JIRA_DUE_DATE_FIELD, JIRA_START_DATE_FIELD]
    issues = []
    start_at = 0
    while True:
        payload = {"jql": jql, "maxResults": 100, "startAt": start_at, "fields": fields}
        res = requests.post(f"{JIRA_URL}/rest/api/3/search/jql", json=payload, auth=ADMIN_AUTH, timeout=15)
        if res.status_code != 200:
            print(f"⚠️ 讀取專案 {project_key} 失敗: {res.text}")
            break
        data = res.json()
        batch = data.get('issues', [])
        issues.extend(batch)
        if len(issues) >= data.get('total', 0) or not batch:
            break
        start_at += 100
    return issues

def ensure_schedule_boundary(soup):
    start_tag = soup.find(string=re.compile(r'#Project Schedule'))
    end_tag = soup.find(string=re.compile(r'#Project End'))
    if not start_tag or not end_tag:
        boundary_html = BeautifulSoup('<hr/><p>#Project Schedule</p><p>#Project End</p><hr/>', 'html.parser')
        soup.append(boundary_html)
        return soup.find(string=re.compile(r'#Project Schedule')), soup.find(string=re.compile(r'#Project End'))
    return start_tag, end_tag

# --- 5. 主程式 ---
def main():
    print(f"=== 啟動 JIRA to Confluence 無頭甘特圖同步引擎 ===")
    projects = get_target_projects()
    if not projects:
        return print("⚠️ 沒有找到任何專案需要同步。")

    space_key = SETTINGS.get("confluence_space_key")
    page_title = SETTINGS.get("confluence_page_title")
    print(f"🎯 目標 Confluence 頁面: [{space_key}] {page_title}")

    # 1. 獲取 Confluence 頁面
    res = requests.get(f"{CONFLUENCE_API_URL}/content", params={"spaceKey": space_key, "title": page_title, "expand": "body.storage,version"}, auth=ADMIN_AUTH)
    pages = res.json().get('results', [])
    if not pages:
        return print(f"❌ 找不到目標頁面，請確認 Space Key 與標題是否正確。")
    
    page_data = pages[0]
    page_id = page_data['id']
    soup = BeautifulSoup(page_data['body']['storage']['value'], 'html.parser')
    start_node, end_node = ensure_schedule_boundary(soup)

    # 2. 清除舊有甘特圖 (包含標籤與分隔線)
    curr = start_node.find_parent().next_sibling
    while curr and curr != end_node.find_parent():
        temp = curr
        curr = curr.next_sibling
        temp.extract()

    # 3. 準備調色盤與全域設定
    lane_colors = [{"lane": "#00875a", "bar": "#00875a"}, {"lane": "#0052cc", "bar": "#0052cc"}, {"lane": "#5243aa", "bar": "#5243aa"}, {"lane": "#ff991f", "bar": "#ff991f"}, {"lane": "#00b8d9", "bar": "#00b8d9"}, {"lane": "#42526e", "bar": "#42526e"}]
    view_mode = SETTINGS.get("view_mode", "MONTH")
    mode = view_mode.split(" ")[0]
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    current_time_str = datetime.now().strftime('%Y/%m/%d %H:%M')

    # 4. 依序處理每個專案
    all_gantt_html = ""
    is_first_chart = True

    for proj_key in projects:
        print(f"\n📦 正在處理專案: {proj_key}")
        issues = fetch_issues_for_project(proj_key)
        if not issues: continue

        valid_tasks, no_date_tasks = [], []
        current_epic = proj_key # 預設 Epic 為專案名稱

        # 解析與過濾 JIRA 資料
        for issue in issues:
            f = issue['fields']
            issuetype = f.get('issuetype', {}).get('name', '').lower()
            raw_name = f.get('summary', '')
            raw_status = f.get('status', {}).get('name', '')
            raw_owner = f.get('assignee', {}).get('displayName', '') if f.get('assignee') else ''
            task_id_str = issue['key']

            if 'epic' in issuetype:
                current_epic = raw_name
                continue

            c_status = get_canonical_status(raw_status)
            if not SETTINGS.get(f"status_{c_status}", True):
                continue # 狀態被過濾

            short_name = get_short_name(raw_owner)
            sd = parse_jira_date(f.get(JIRA_START_DATE_FIELD))
            ed = parse_jira_date(f.get(JIRA_DUE_DATE_FIELD))

            if sd and ed:
                valid_tasks.append({'epic': current_epic, 'raw_name': raw_name, 'sd': sd, 'ed': ed, 'short_name': short_name, 'status': c_status, 'owner': raw_owner, 'task_id': task_id_str})
            else:
                no_date_tasks.append({'epic': current_epic, 'raw_name': raw_name, 'sd': None, 'ed': None, 'short_name': short_name, 'status': c_status, 'owner': raw_owner, 'task_id': task_id_str})

        if not valid_tasks and not no_date_tasks:
            print(f"  └ ⚠️ 無符合條件的任務")
            continue

        # 處理有日期的任務
        valid_tasks_processed = []
        for t in valid_tasks:
            final_name = t['raw_name']
            if SETTINGS.get("show_duration"):
                final_name = f"{format_workdays(t['sd'], t['ed'])} {final_name}"
            if SETTINGS.get("show_dates"):
                final_name = f"{final_name} {t['sd'].month}/{t['sd'].day}~{t['ed'].month}/{t['ed'].day}"
            if SETTINGS.get("show_assignee") and t['short_name']:
                final_name += f" -{t['short_name']}"
            if SETTINGS.get("show_status"):
                final_name += f" -{t['status']}"
            if SETTINGS.get("show_task_id") and t['task_id']:
                final_name += f" {t['task_id']}"
            t['name'] = final_name
            valid_tasks_processed.append(t)

        # 處理無日期的任務
        no_date_tasks_processed = []
        if SETTINGS.get("show_nodate") and no_date_tasks:
            for t in no_date_tasks:
                if SETTINGS.get("show_assignee") and t['short_name']:
                    final_name = f"[NA]-{t['short_name']} {t['raw_name']}"
                else:
                    final_name = f"[NA] {t['raw_name']}"
                if SETTINGS.get("show_status"):
                    final_name += f" -{t['status']}"
                if SETTINGS.get("show_task_id") and t['task_id']:
                    final_name += f" {t['task_id']}"
                
                char_units = sum(2 if ord(c) > 127 else 1 for c in final_name)
                week_days = max(3, int(char_units * 0.6))
                
                t['sd'] = today_start
                t['ed'] = today_start + timedelta(days=week_days if mode == "WEEK" else 30)
                t['name'] = final_name
                no_date_tasks_processed.append(t)
            
            no_date_tasks_processed.sort(key=lambda x: (x.get('short_name', ''), x.get('raw_name', '')))

        all_tasks = valid_tasks_processed + no_date_tasks_processed
        if not all_tasks: continue

        epics = list(dict.fromkeys([t['epic'] for t in all_tasks]))
        min_d = min(t['sd'] for t in all_tasks)
        max_d = max(t['ed'] for t in all_tasks)

        if mode == "WEEK":
            timeline_start = min_d - timedelta(days=min_d.weekday()) - timedelta(days=7)
            timeline_end = max_d + timedelta(days=6 - max_d.weekday()) + timedelta(days=7)
            duration_unit = 7.0
        else:
            timeline_start = datetime(min_d.year - 1 if min_d.month == 1 else min_d.year, 12 if min_d.month == 1 else min_d.month - 1, 1)
            next_m = max_d.month + 1 if max_d.month < 12 else 1
            next_y = max_d.year if max_d.month < 12 else max_d.year + 1
            nn_m = next_m + 1 if next_m < 12 else 1
            nn_y = next_y if next_m < 12 else next_y + 1
            timeline_end = (datetime(nn_y, nn_m, 1) - timedelta(days=1))
            duration_unit = 30.416

        markers_json = []
        if timeline_start <= today_start <= timeline_end:
            markers_json.append({"markerDate": today_start.strftime('%Y-%m-%d %H:%M:%S'), "title": f"Today {today_start.strftime('%m/%d')}"})
        
        if SETTINGS.get("show_weekends"):
            curr_d = timeline_start
            while curr_d <= timeline_end:
                if curr_d.weekday() == 5: markers_json.append({"markerDate": curr_d.strftime('%Y-%m-%d 00:00:00'), "title": "<"})
                elif curr_d.weekday() == 6: markers_json.append({"markerDate": curr_d.strftime('%Y-%m-%d 23:59:59'), "title": ">"})
                curr_d += timedelta(days=1)

        lanes_json = []
        for i, epic in enumerate(epics):
            bars = []
            tasks = [t for t in all_tasks if t['epic'] == epic]
            for r_idx, t in enumerate(tasks):
                dur = max(0.1, round(((t['ed'] - t['sd']).days + 1) / duration_unit, 3))
                bars.append({"title": t['name'], "description": t['owner'], "startDate": t['sd'].strftime('%Y-%m-%d 12:00:00'), "duration": dur, "rowIndex": r_idx})
            lanes_json.append({"title": epic, "color": {**lane_colors[i % len(lane_colors)], "text": "#ffffff", "count": 1}, "bars": bars})

        source_dict = {
            "title": f"{proj_key} Gantt", "timeline": {"startDate": timeline_start.strftime('%Y-%m-%d 12:00:00'), "endDate": timeline_end.strftime('%Y-%m-%d 12:00:00'), "displayOption": mode}, 
            "lanes": lanes_json, "markers": markers_json
        }
        encoded_source = urllib.parse.quote(json.dumps(source_dict, separators=(',', ':')))

        anchor_html = '<ac:structured-macro ac:name="anchor" ac:schema-version="1"><ac:parameter ac:name="">gantt_chart_area</ac:parameter></ac:structured-macro>' if is_first_chart else ""
        is_first_chart = False

        all_gantt_html += f'''
        <ac:layout>
          <ac:layout-section ac:type="full-width">
            <ac:layout-cell>
              <div style="width: 100%; min-width: 1000px; margin-top: 15px;">
                <p>{anchor_html}<strong><span style="color: rgb(0,82,204);">#[{proj_key}] Schedule ({mode}) </span><span style="color: #95a5a6;">- {current_time_str}</span></strong></p>
                <ac:structured-macro ac:name="roadmap" ac:schema-version="1">
                  <ac:parameter ac:name="width">100%</ac:parameter><ac:parameter ac:name="view">{mode}</ac:parameter>
                  <ac:parameter ac:name="display-option">{mode}</ac:parameter><ac:parameter ac:name="timeline">true</ac:parameter>
                  <ac:parameter ac:name="source">{encoded_source}</ac:parameter>
                </ac:structured-macro>
              </div>
            </ac:layout-cell>
          </ac:layout-section>
        </ac:layout>
        '''
        print(f"  └ ✅ 生成甘特圖區塊成功 (共 {len(all_tasks)} 筆任務)")

    if not all_gantt_html:
        return print("📭 沒有產生任何甘特圖資料。")

    # 5. 將所有產生的 HTML 寫入 Confluence
    print(f"\n💾 正在將所有甘特圖寫入 Confluence 頁面...")
    new_soup = BeautifulSoup(all_gantt_html, 'html.parser')
    end_node.find_parent().insert_before(new_soup)

    payload = {
        "version": {"number": page_data['version']['number'] + 1, "minorEdit": True},
        "title": page_data['title'], "type": "page",
        "body": {"storage": {"value": str(soup), "representation": "storage"}}
    }
    update_res = requests.put(f"{CONFLUENCE_API_URL}/content/{page_id}", json=payload, auth=ADMIN_AUTH, headers={"Content-Type": "application/json"})
    
    if update_res.status_code == 200:
        print(f"🎉 大功告成！所有專案已成功同步至: {JIRA_URL}/wiki/spaces/{space_key}/pages/{page_id}#gantt_chart_area")
    else:
        print(f"❌ 更新失敗: {update_res.text}")

if __name__ == "__main__":
    main()
