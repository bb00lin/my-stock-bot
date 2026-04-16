這個要求真的把排版的質感推向了另一個層次！加入「邊框收納」與「數字編號」後，週報看起來會更像是一份結構嚴謹的專業報表，而不再只是散落的文字。

針對你的三個需求，我為你打造了 **V49.12 精品框線與編號版**：

1. **集中在同一個框框**：我為每一組專案（以及下方的待辦任務區）加入了一個帶有圓角的淡灰色邊框 `border: 1px solid #dfe1e6`，這正是 Jira/Confluence 官方設計系統最喜歡用的高質感邊框顏色。
2. **圖案換成數字編號**：移除了原本的 `🔹` 和 `🔸`，改為自動遞增的紅色粗體數字 `1.`、`2.`、`3.`。且**每個專案框框都會自動從 1 開始重新計數**！
3. **寬度再縮小一點點**：由於我們上一版把頁面強制設定為「全寬 (Full-width)」，導致在大螢幕上文字會被拉得非常長。這次我在最外層的容器加上了 `max-width: 1000px;`。這樣一來，不管你的螢幕有多寬，日誌區塊都會保持在一個「最舒適的閱讀寬度」，不會被無限拉長，同時也不會被擠壓到換行。

請直接**全選並覆蓋**你 GitHub 上的 `daily_worklog_to_confluence.py` 代碼：

```python
import os
import requests
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse
from bs4 import BeautifulSoup, Tag, NavigableString
from dotenv import load_dotenv

# 載入自訂的環境變數檔案 (本地端測試用，GitHub Actions 會自動忽略)
load_dotenv("jira_config.txt")

# --- 1. 環境變數與金鑰設定 (對齊 GitHub Secrets) ---
raw_url = os.environ.get("CONF_URL", "")
parsed = urlparse(raw_url)
# ✅ 網址清洗器：強制只保留主網域
JIRA_URL = f"{parsed.scheme}://{parsed.netloc}"

ADMIN_EMAIL = os.environ.get("CONF_USER")
ADMIN_TOKEN = os.environ.get("CONF_PASS")

if not raw_url or not ADMIN_EMAIL or not ADMIN_TOKEN:
    print("❌ 錯誤：找不到環境變數 CONF_URL, CONF_USER 或 CONF_PASS")
    sys.exit(1)

ADMIN_AUTH = HTTPBasicAuth(ADMIN_EMAIL, ADMIN_TOKEN)

# 這裡的 Email 就算在 Github Secrets 沒設定，程式也會自動切換成人名搜尋，非常安全
ACCOUNT_DICT = {
    "Bob Lin": os.environ.get("CONF_USER"),
    "shannonchang": os.environ.get("SHANNON_EMAIL"),
    "sam.chang": os.environ.get("SAM_EMAIL"),
    "Vic Wu": os.environ.get("VIC_EMAIL"),
    "SF Hsieh": os.environ.get("SF_EMAIL")
}

# --- 2. 設定檔載入引擎 (取代舊有 GUI 變數) ---
class SettingsManager:
    def __init__(self, filepath="settings_daily.json"):
        self.filepath = filepath
        # 預設值
        self.config = {
            "filter_comment": True, "filter_started": True, "day_yesterday": False,
            "exclude_keywords": "DailyMeeting", "auto_clear_first": False,
            "day_auto": True, "day_mon": True, "day_tue": True, "day_wed": True,
            "day_thu": True, "day_fri": True, "show_label": True, "show_parent": True,
            "show_status": True, "show_comment": True, "minor_edit": True,
            "show_pending_inprogress": True, "show_pending_waiting": True,
            "show_pending_todo": False, "show_pending_candidate": False,
            "show_pending_has_due": False, "compact_layout": False, "use_jira_macro": True,
            "group_by_project": False, "hide_duplicate_transition": False,
            "show_confluence_links": True, "show_total_time": True, "show_write_time": True,
            "show_duedate": True, "show_due_tbd": False
        }
        self.load_settings()

    def load_settings(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.config.update(json.load(f))
                print(f"⚙️ 成功讀取 {self.filepath} 設定檔")
            except Exception as e:
                print(f"⚠️ 讀取設定檔失敗，將使用預設值 ({e})")
        else:
            print(f"⚠️ 找不到 {self.filepath}，將使用預設值")

    def get(self, key, default=None):
        return self.config.get(key, default)

SETTINGS = SettingsManager()

# --- 3. 核心輔助函式 ---
EMOJI_MAP = {
    "TO DO": "📋", "待辦事項": "📋", "IN PROGRESS": "🔄", "進行中": "🔄",
    "DONE": "✅", "完成": "✅", "BLOCKED": "🛑", "CANDIDATE": "🎯",
    "RESUME": "▶️", "WAITING": "⏳", "等待中": "⏳", "ABORT": "❌"
}

def get_emoji(status_str):
    if not status_str: return "🔸"
    return EMOJI_MAP.get(status_str.strip().upper(), "🔸") 

def translate_status(status_str):
    if not status_str: return "NA"
    s = status_str.upper()
    if s == "IN PROGRESS": return "進行中"
    if s == "DONE": return "完成"
    if s == "TO DO": return "待辦事項"
    if s == "WAITING": return "Waiting"
    return status_str

def parse_jira_date_to_tz8(jira_time_str):
    if not jira_time_str: return ""
    try:
        dt = datetime.strptime(jira_time_str, "%Y-%m-%dT%H:%M:%S.%f%z")
        dt_tz8 = dt.astimezone(timezone(timedelta(hours=8)))
        return dt_tz8.strftime("%Y-%m-%d")
    except Exception:
        return jira_time_str[:10]

def parse_duration_to_minutes(dur_str):
    if not dur_str or dur_str == "-": return 0
    mins = 0
    parts = re.findall(r'(\d+)([wdhm])', dur_str.lower())
    for val, unit in parts:
        v = int(val)
        if unit == 'w': mins += v * 5 * 8 * 60
        elif unit == 'd': mins += v * 8 * 60
        elif unit == 'h': mins += v * 60
        elif unit == 'm': mins += v
    return mins

def format_duration(total_mins):
    if total_mins == 0: return "0m"
    h = total_mins // 60
    m = total_mins % 60
    if h > 0 and m > 0: return f"{h}h{m}m"
    elif h > 0: return f"{h}h"
    else: return f"{m}m"

def adjust_duration_for_lunch(start_dt, duration_mins):
    if duration_mins <= 0: return 0
    end_dt = start_dt + timedelta(minutes=duration_mins)
    lunch_start = start_dt.replace(hour=12, minute=0, second=0, microsecond=0)
    lunch_end = start_dt.replace(hour=13, minute=0, second=0, microsecond=0)
    overlap_start = max(start_dt, lunch_start)
    overlap_end = min(end_dt, lunch_end)
    if overlap_start < overlap_end:
        overlap_mins = (overlap_end - overlap_start).total_seconds() / 60
        return int(duration_mins - overlap_mins)
    return int(duration_mins)

def format_due_date(date_str):
    if not date_str: return '"Due TBD"', None
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return f'"Due {dt.strftime("%y")}\' {dt.month}/{dt.day}"', dt
    except: return '"Due TBD"', None

def calculate_working_days(start_date, end_date):
    start = start_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end = end_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    if start == end: return 0
    step = timedelta(days=1) if start < end else timedelta(days=-1)
    current = start + step
    working_days = 0
    while True:
        if current.weekday() < 5: working_days += 1
        if current == end: break
        current += step
    return working_days if start < end else -working_days

# --- 4. 核心邏輯引擎 ---
def get_selected_dates():
    now_tpe = datetime.now(timezone(timedelta(hours=8)))
    
    if SETTINGS.get("day_auto") and not SETTINGS.get("day_yesterday"):
        weekday = now_tpe.weekday()
        SETTINGS.config.update({
            "day_mon": weekday >= 0, "day_tue": weekday >= 1,
            "day_wed": weekday >= 2, "day_thu": weekday >= 3,
            "day_fri": weekday >= 4
        })

    if SETTINGS.get("day_yesterday"): 
        if now_tpe.weekday() == 0: return [now_tpe - timedelta(days=3)]
        else: return [now_tpe - timedelta(days=1)]
            
    weekday = now_tpe.weekday()
    monday = now_tpe - timedelta(days=weekday)
    dates = []
    if SETTINGS.get("day_mon"): dates.append(monday)
    if SETTINGS.get("day_tue"): dates.append(monday + timedelta(days=1))
    if SETTINGS.get("day_wed"): dates.append(monday + timedelta(days=2))
    if SETTINGS.get("day_thu"): dates.append(monday + timedelta(days=3))
    if SETTINGS.get("day_fri"): dates.append(monday + timedelta(days=4))
    return dates

def get_target_report_title(sample_date):
    days_ahead = 4 - sample_date.weekday()
    target_date = sample_date + timedelta(days=days_ahead)
    return f"WeeklyReport_{target_date.strftime('%Y%m%d')}"

_account_id_cache = {}
def get_account_id(email, name):
    if name in _account_id_cache: return _account_id_cache[name]
    if email:
        try:
            res = requests.get(f"{JIRA_URL}/rest/api/3/user/search?query={email}", auth=ADMIN_AUTH, timeout=5)
            if res.status_code == 200 and len(res.json()) > 0:
                _account_id_cache[name] = res.json()[0]['accountId']
                return _account_id_cache[name]
        except: pass
    try:
        clean_name = name.split(" (")[0].strip()
        res = requests.get(f"{JIRA_URL}/rest/api/3/user/search?query={clean_name}", auth=ADMIN_AUTH, timeout=5)
        if res.status_code == 200 and len(res.json()) > 0:
            _account_id_cache[name] = res.json()[0]['accountId']
            return _account_id_cache[name]
    except: pass
    return None

def fetch_all_recent_issues(min_date):
    min_date_str = min_date.strftime("%Y-%m-%d")
    jql = f'updated >= "{min_date_str}" ORDER BY updated DESC'
    all_issues = []
    
    next_page_token = None 
    
    while True:
        payload = {
            "jql": jql, 
            "maxResults": 100, 
            "fields": ["summary", "status", "project", "parent", "labels", "worklog", "assignee", "duedate"],
            "expand": "changelog" 
        }
        
        if next_page_token:
            payload["nextPageToken"] = next_page_token
            
        try:
            res = requests.post(f"{JIRA_URL}/rest/api/3/search/jql", json=payload, auth=ADMIN_AUTH, timeout=20)
            res.raise_for_status()
            data = res.json()
            
            issues = data.get('issues', [])
            all_issues.extend(issues)
            
            next_page_token = data.get('nextPageToken')
            
            if not next_page_token or not issues:
                break
                
        except Exception as e:
            print(f"    ❌ 全域掃描 API 請求失敗: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"    📄 錯誤詳情: {e.response.text}")
            break
            
    return all_issues

_remote_links_cache = {}
def get_remote_links(key):
    if key in _remote_links_cache: return _remote_links_cache[key]
    links = []
    try:
        res = requests.get(f"{JIRA_URL}/rest/api/3/issue/{key}/remotelink", auth=ADMIN_AUTH, timeout=5)
        if res.status_code == 200:
            for rlink in res.json():
                app_name = rlink.get('application', {}).get('name', '').lower()
                obj = rlink.get('object', {})
                url = obj.get('url', '')
                title = obj.get('title', 'Confluence Page')
                if 'confluence' in app_name or '/wiki/' in url:
                    if 'weeklyreport' not in title.lower() and 'weeklyreport' not in url.lower():
                        links.append({'url': url, 'title': title})
    except: pass
    _remote_links_cache[key] = links
    return links

def fetch_pending_tasks(account_id, updated_keys):
    if not account_id: return [], [], [], []
    jql = f'assignee = "{account_id}" AND resolution = Unresolved'
    payload = {"jql": jql, "maxResults": 100, "fields": ["summary", "status", "project", "duedate"]}
    
    pending_in_progress, pending_waiting, pending_todo, pending_candidate = [], [], [], []
    try:
        res = requests.post(f"{JIRA_URL}/rest/api/3/search/jql", json=payload, auth=ADMIN_AUTH, timeout=10)
        if res.status_code == 200:
            issues = res.json().get('issues', [])
            
            exclude_kws = [kw.strip().lower() for kw in SETTINGS.get("exclude_keywords", "").split(',') if kw.strip()]
            
            for issue in issues:
                key = issue['key']
                summary_text = issue['fields'].get('summary', 'NA')
                project_text = issue['fields'].get('project', {}).get('name', 'NA')
                
                if key not in updated_keys:
                    is_excluded = any(kw in summary_text.lower() or kw in project_text.lower() for kw in exclude_kws)
                    if is_excluded:
                        continue
                        
                    status_str = issue['fields'].get('status', {}).get('name', 'NA')
                    duedate_str, duedate_dt = format_due_date(issue['fields'].get('duedate'))
                    confluence_links = get_remote_links(key) if SETTINGS.get("show_confluence_links") else []

                    task_data = {
                        "key": key, "summary": summary_text, "status": status_str,
                        "project": project_text, "duedate": duedate_str, "duedate_dt": duedate_dt,
                        "confluence_links": confluence_links
                    }
                    
                    status_upper = status_str.upper()
                    if status_upper in ["WAITING", "等待中"] or "WAIT" in status_upper: pending_waiting.append(task_data)
                    elif status_upper in ["CANDIDATE"]: pending_candidate.append(task_data)
                    elif status_upper in ["TO DO", "待辦事項", "OPEN", "NEW"]: pending_todo.append(task_data)
                    elif status_upper in ["IN PROGRESS", "進行中"] or "PROGRESS" in status_upper: pending_in_progress.append(task_data)
                        
            for task_list in [pending_in_progress, pending_waiting, pending_todo, pending_candidate]:
                task_list.sort(key=lambda x: (1 if x.get('project', '').upper() == 'MEETING' else 0, x.get('project', ''), x.get('key', '')))
            return pending_in_progress, pending_waiting, pending_todo, pending_candidate
    except: pass
    return [], [], [], []

def get_user_status_transition_for_day(key, account_id, email, target_date_str, issue_data):
    day_status_changes = []
    histories = []

    if 'changelog' in issue_data:
        histories = issue_data['changelog'].get('histories', [])
        if issue_data['changelog'].get('total', len(histories)) > len(histories): histories = []

    if not histories:
        try:
            res = requests.get(f"{JIRA_URL}/rest/api/3/issue/{key}/changelog", auth=ADMIN_AUTH, timeout=5)
            if res.status_code == 200:
                data = res.json()
                total = data.get('total', 0)
                if total > 100:
                    data = requests.get(f"{JIRA_URL}/rest/api/3/issue/{key}/changelog?startAt={total-100}&maxResults=100", auth=ADMIN_AUTH, timeout=5).json()
                histories = data.get('values', [])
        except: pass

    for history in histories:
        created_date_tz8 = parse_jira_date_to_tz8(history.get('created', ''))
        author_id = history.get('author', {}).get('accountId')
        author_email = history.get('author', {}).get('emailAddress', '').lower()
        is_author = (account_id and author_id == account_id) or (email and author_email == email.lower())

        if created_date_tz8 == target_date_str and is_author:
            for item in history.get('items', []):
                if item.get('field', '').lower() == 'status':
                    day_status_changes.append((item.get('fromString', ''), item.get('toString', '')))

    if day_status_changes:
        first_trans = translate_status(day_status_changes[0][0])
        last_trans = translate_status(day_status_changes[-1][1])
        if SETTINGS.get("hide_duplicate_transition") and first_trans == last_trans: return ""
        return f"{get_emoji(day_status_changes[0][0])}[{first_trans}] --> {get_emoji(day_status_changes[-1][1])}[{last_trans}]"
    return ""

def extract_logs_from_issues(name, email, account_id, target_date, all_issues):
    target_date_str = target_date.strftime("%Y-%m-%d")
    target_short = target_date.strftime("%m/%d").lstrip("0").replace("/0", "/")
    collected_logs = []
    
    exclude_kws = [kw.strip().lower() for kw in SETTINGS.get("exclude_keywords", "").split(',') if kw.strip()]
    
    for issue in all_issues:
        key = issue['key']
        fields = issue['fields']
        
        summary = fields.get('summary', 'NA')
        project_name = fields.get('project', {}).get('name', 'NA')
        
        is_excluded = any(kw in summary.lower() or kw in project_name.lower() for kw in exclude_kws)
        if is_excluded:
            continue
            
        parent = fields.get('parent', {}).get('fields', {}).get('summary', 'NA')
        label_str = fields.get('labels', ['NA'])[0] if fields.get('labels') else "NA"
        current_status = fields.get('status', {}).get('name', 'NA')
        duedate_str, duedate_dt = format_due_date(fields.get('duedate'))

        status_transition_str = get_user_status_transition_for_day(key, account_id, email, target_date_str, issue)
        user_changed_status = bool(status_transition_str)

        user_worklogs = []
        worklogs_data = fields.get('worklog', {}).get('worklogs', [])
        
        if fields.get('worklog', {}).get('total', 0) > len(worklogs_data):
            try: worklogs_data = requests.get(f"{JIRA_URL}/rest/api/2/issue/{key}/worklog", auth=ADMIN_AUTH).json().get('worklogs', [])
            except: pass

        for wl in worklogs_data:
            author_id = wl.get('author', {}).get('accountId')
            author_email = wl.get('author', {}).get('emailAddress', '').lower()
            if not ((account_id and author_id == account_id) or (email and author_email == email.lower())): continue

            comment_raw = wl.get('comment', 'NA')
            if isinstance(comment_raw, dict): 
                comment_texts = []
                def ex(n):
                    if n.get('type') == 'text': comment_texts.append(n.get('text', ''))
                    for child in n.get('content', []): ex(child)
                ex(comment_raw)
                comment_text = "".join(comment_texts).strip()
            else: comment_text = str(comment_raw).strip() if comment_raw else "NA"
            if not comment_text: comment_text = "NA"

            raw_started = wl.get('started', '')
            try:
                dt = datetime.strptime(raw_started, "%Y-%m-%dT%H:%M:%S.%f%z")
                start_dt_tz8 = dt.astimezone(timezone(timedelta(hours=8)))
                started_date_tz8 = start_dt_tz8.strftime("%Y-%m-%d")
            except:
                start_dt_tz8 = None
                started_date_tz8 = raw_started[:10]

            is_target_day = (started_date_tz8 == target_date_str)
            
            date_match = re.match(r'^(\d{1,2}/\d{1,2})', comment_text)
            explicit_date_str = date_match.group(1) if date_match else None

            is_valid = False
            
            if SETTINGS.get("filter_comment"):
                if explicit_date_str:
                    clean_explicit = explicit_date_str.lstrip("0").replace("/0", "/")
                    if clean_explicit == target_short:
                        is_valid = True
                else:
                    if SETTINGS.get("filter_started") and is_target_day:
                        is_valid = True
            elif SETTINGS.get("filter_started"):
                if is_target_day:
                    is_valid = True

            if is_valid:
                raw_mins = parse_duration_to_minutes(wl.get('timeSpent', '0m'))
                adjusted_mins = adjust_duration_for_lunch(start_dt_tz8, raw_mins) if start_dt_tz8 else raw_mins
                user_worklogs.append({"comment": comment_text, "duration": format_duration(adjusted_mins), "duration_mins": adjusted_mins})

        assignee_data = fields.get('assignee')
        is_assignee = assignee_data and ((assignee_data.get('accountId') == account_id) or (assignee_data.get('emailAddress', '').lower() == email.lower()))

        if user_worklogs or (user_changed_status and is_assignee):
            if not user_worklogs: user_worklogs.append({"comment": "更新任務狀態 (無填寫工時)", "duration": "-", "duration_mins": 0})
            confluence_links = get_remote_links(key) if SETTINGS.get("show_confluence_links") else []
            for uwl in user_worklogs:
                collected_logs.append({
                    "key": key, "summary": summary, "status": current_status, "transition": status_transition_str,
                    "project": project_name, "parent": parent, "label": label_str, "comment": uwl['comment'],
                    "duration": uwl['duration'], "duration_mins": uwl.get('duration_mins', 0),
                    "duedate": duedate_str, "duedate_dt": duedate_dt, "confluence_links": confluence_links
                })
    
    collected_logs.sort(key=lambda x: (1 if x.get('project', '').upper() == 'MEETING' else 0, x.get('project', ''), 0 if x.get('status', '').upper() in ['DONE', '完成'] else 1, x.get('key', '')))
    return collected_logs

def generate_style_2_html(soup, target_date, logs, pending_in_progress=None, pending_waiting=None, pending_todo=None, pending_candidate=None, total_mins=0):
    date_str_tag = target_date.strftime("[%Y/%m/%d]")
    weekday_en = target_date.strftime("%A")
    safe_date_class = target_date.strftime("%Y%m%d")
    
    # ✅ 調整 3: 將最大寬度限制在 1000px，保持舒適的閱讀寬度
    container = soup.new_tag("div", **{
        "class": f"daily-worklog-{safe_date_class}", 
        "style": "max-width: 1000px; margin-bottom: 25px;"
    })
    
    p_date = soup.new_tag("p")
    strong_date = soup.new_tag("strong")
    
    time_parts = []
    if SETTINGS.get("show_write_time"): time_parts.append(datetime.now(timezone(timedelta(hours=8))).strftime('"%Y/%m/%d %H:%M"'))
    if SETTINGS.get("show_total_time"): time_parts.append(f"({format_duration(total_mins)})")

    if time_parts:
        strong_date.string = f"{date_str_tag} {weekday_en} - "
        p_date.append(strong_date)
        span_time = soup.new_tag("span", style="color: gray; font-size: 50%;")
        span_time.string = " - ".join(time_parts)
        p_date.append(span_time)
    else:
        strong_date.string = f"{date_str_tag} {weekday_en}"
        p_date.append(strong_date)
        
    container.append(p_date)
    
    current_project = None
    project_box = None
    log_counter = 1

    # 如果沒有啟動群組，且有日誌，則建立單一的大框框
    if logs and not SETTINGS.get("group_by_project"):
        project_box = soup.new_tag("div", style="border: 1px solid #dfe1e6; padding: 12px 16px; border-radius: 8px; margin-bottom: 15px; background-color: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.02);")
        container.append(project_box)

    for log in logs:
        # ✅ 調整 1: 當專案變更時，產生新的邊框框框
        if SETTINGS.get("group_by_project") and log['project'] != current_project:
            if current_project is not None:
                p_empty = soup.new_tag("p")
                p_empty.append(soup.new_tag("br"))
                container.append(p_empty)
            current_project = log['project']
            p_proj = soup.new_tag("p", style="margin-top: 5px; margin-bottom: 8px; font-weight: bold; color: #2980b9;")
            p_proj.string = f"---- 專案: {current_project} ----"
            container.append(p_proj)
            
            project_box = soup.new_tag("div", style="border: 1px solid #dfe1e6; padding: 12px 16px; border-radius: 8px; margin-bottom: 15px; background-color: #ffffff; box-shadow: 0 1px 3px rgba(0,0,0,0.02);")
            container.append(project_box)
            
            # ✅ 調整 2: 重置計數器
            log_counter = 1

        p1 = soup.new_tag("p", style="margin-top: 5px; margin-bottom: 2px;")
        
        # ✅ 調整 2: 將藍色菱形 🔹 換成紅色數字編號 (例: 1. , 2. )
        num_span = soup.new_tag("span", style="color: #e74c3c; font-weight: bold; margin-right: 5px;")
        num_span.string = f"{log_counter}."
        p1.append(num_span)
        
        if SETTINGS.get("use_jira_macro"):
            macro = soup.new_tag("ac:structured-macro", **{"ac:name": "jira", "ac:schema-version": "1"})
            
            param_server = soup.new_tag("ac:parameter", **{"ac:name": "server"})
            param_server.string = "System JIRA"
            macro.append(param_server)
            
            param_key = soup.new_tag("ac:parameter", **{"ac:name": "key"})
            param_key.string = log['key']
            macro.append(param_key)
            p1.append(macro)
            
            if SETTINGS.get("show_status"):
                status_text = f" {log['transition']}" if log.get('transition') else f" {get_emoji(log['status'])}[{translate_status(log['status'])}]"
                p1.append(soup.new_string(status_text))
        else:
            a_key = soup.new_tag("a", href=f"{JIRA_URL}/browse/{log['key']}")
            a_key.string = log['key']
            p1.append(a_key)
            if SETTINGS.get("show_status"):
                status_text = f" {log['transition']}" if log.get('transition') else f" {get_emoji(log['status'])}[{translate_status(log['status'])}]"
            else: status_text = ""
            p1.append(soup.new_string(f" : {log['summary']}{status_text}"))
        
        is_tbd = (log.get('duedate') == '"Due TBD"')
        if SETTINGS.get("show_duedate") and log.get('duedate'):
            if not (is_tbd and not SETTINGS.get("show_due_tbd")):
                span_due = soup.new_tag("span", style="color: gray; font-size: 50%;")
                span_due.string = f" {log['duedate']}"
                p1.append(span_due)
                if not is_tbd and log.get('duedate_dt'):
                    diff_days = calculate_working_days(target_date, log['duedate_dt'])
                    color = "#2ecc71" if diff_days >= 0 else "#e74c3c"
                    sign = "+" if diff_days >= 0 else ""
                    warning_icon = "❗" if diff_days <= 2 else ""
                    span_diff = soup.new_tag("span", style=f"color: {color}; font-size: 50%; margin-left: 4px;")
                    span_diff.string = f" {warning_icon}({sign}{diff_days})"
                    p1.append(span_diff)
        
        if log.get('confluence_links'):
            for cl in log['confluence_links']:
                p1.append(soup.new_string(" "))
                a_conf = soup.new_tag("a", href=cl['url'], **{"data-card-appearance": "inline"})
                a_conf.string = cl['url']
                p1.append(a_conf)

        parts = []
        if SETTINGS.get("show_label"): parts.append(f"標籤: {log['label']}")
        if not SETTINGS.get("group_by_project"): parts.append(f"專案: {log['project']}")
        if SETTINGS.get("show_parent"): parts.append(f"父系: {log['parent']}")
        parts_str = " | ".join(parts)
        
        if SETTINGS.get("compact_layout"):
            span_parts = soup.new_tag("span", style="margin-left: 10px; color: #7f8c8d;")
            span_parts.string = f" {parts_str}"
            p1.append(span_parts)
            project_box.append(p1) # 改為寫入 box 中
        else:
            project_box.append(p1) # 改為寫入 box 中
            p2 = soup.new_tag("p", style="margin-left: 20px; margin-top: 0px; margin-bottom: 2px; color: #555555;")
            p2.string = f"　 └ " + parts_str
            project_box.append(p2) # 改為寫入 box 中
        
        p3 = soup.new_tag("p", style="margin-left: 20px; margin-top: 0px; margin-bottom: 10px; color: #555555;")
        if SETTINGS.get("show_comment"):
            dur_text = f"({log['duration']}) " if log['duration'] != "-" and log['duration'] != "0m" else ""
            p3.string = f"　 └ 📝 {dur_text}{log['comment']}"
        else:
            if log['duration'] != "-": p3.string = f"　 └ ⏱️ 耗時: {log['duration']}"
            
        project_box.append(p3) # 改為寫入 box 中
        log_counter += 1

    has_any_pending = bool((SETTINGS.get("show_pending_inprogress") and pending_in_progress) or
                           (SETTINGS.get("show_pending_waiting") and pending_waiting) or
                           (SETTINGS.get("show_pending_todo") and pending_todo) or
                           (SETTINGS.get("show_pending_candidate") and pending_candidate))

    if logs and has_any_pending:
        p_spacer_before_pending = soup.new_tag("p")
        p_spacer_before_pending.append(soup.new_tag("br"))
        container.append(p_spacer_before_pending)

    rendered_pending_sections = [0]

    def append_pending_tasks(task_list, title_text, title_color):
        if not task_list: return
        
        if rendered_pending_sections[0] > 0:
            container.append(soup.new_tag("br"))

        p_divider = soup.new_tag("p", style=f"margin-top: 10px; margin-bottom: 8px; font-weight: bold; color: {title_color};")
        p_divider.string = title_text
        container.append(p_divider)
        
        # ✅ 待辦區塊同樣使用邊框包裝，保持視覺一致性
        pending_box = soup.new_tag("div", style="border: 1px solid #dfe1e6; padding: 12px 16px; border-radius: 8px; margin-bottom: 15px; background-color: #fafbfc;")
        container.append(pending_box)

        task_counter = 1
        for pl in task_list:
            p_pend = soup.new_tag("p", style="margin-top: 4px; margin-bottom: 4px; color: #7f8c8d;")
            
            # ✅ 待辦任務也加入紅色數字編號
            num_span = soup.new_tag("span", style="color: #e74c3c; font-weight: bold; margin-right: 5px;")
            num_span.string = f"{task_counter}."
            p_pend.append(num_span)
            
            p_pend.append(soup.new_string(f"[{pl['project']}] "))
            
            if SETTINGS.get("use_jira_macro"):
                macro = soup.new_tag("ac:structured-macro", **{"ac:name": "jira", "ac:schema-version": "1"})
                param_server = soup.new_tag("ac:parameter", **{"ac:name": "server"})
                param_server.string = "System JIRA"
                macro.append(param_server)
                param_key = soup.new_tag("ac:parameter", **{"ac:name": "key"})
                param_key.string = pl['key']
                macro.append(param_key)
                
                p_pend.append(macro)
            else:
                a_key = soup.new_tag("a", href=f"{JIRA_URL}/browse/{pl['key']}")
                a_key.string = pl['key']
                p_pend.append(a_key)
                p_pend.append(soup.new_string(f" : {pl['summary']} {get_emoji(pl['status'])}[{translate_status(pl['status'])}]"))

            is_tbd = (pl.get('duedate') == '"Due TBD"')
            if SETTINGS.get("show_duedate") and pl.get('duedate'):
                if not (is_tbd and not SETTINGS.get("show_due_tbd")):
                    span_due = soup.new_tag("span", style="color: gray; font-size: 50%;")
                    span_due.string = f" {pl['duedate']}"
                    p_pend.append(span_due)
                    if not is_tbd and pl.get('duedate_dt'):
                        diff_days = calculate_working_days(target_date, pl['duedate_dt'])
                        color = "#2ecc71" if diff_days >= 0 else "#e74c3c"
                        sign = "+" if diff_days >= 0 else ""
                        warning_icon = "❗" if diff_days <= 2 else ""
                        span_diff = soup.new_tag("span", style=f"color: {color}; font-size: 50%; margin-left: 4px;")
                        span_diff.string = f" {warning_icon}({sign}{diff_days})"
                        p_pend.append(span_diff)

            if pl.get('confluence_links'):
                for cl in pl['confluence_links']:
                    p_pend.append(soup.new_string(" "))
                    a_conf = soup.new_tag("a", href=cl['url'], **{"data-card-appearance": "inline"})
                    a_conf.string = cl['url']
                    p_pend.append(a_conf)
                
            pending_box.append(p_pend) # 改為寫入 pending_box 中
            task_counter += 1
            
        rendered_pending_sections[0] += 1

    if SETTINGS.get("show_pending_inprogress"): append_pending_tasks(pending_in_progress, "🔄 進行中且尚未更新進度的任務：", "#d35400")
    if SETTINGS.get("show_pending_waiting"): append_pending_tasks(pending_waiting, "⏳ Waiting 狀態的任務：", "#8e44ad")
    if SETTINGS.get("show_pending_todo"): append_pending_tasks(pending_todo, "📋 待辦事項 (To Do) 任務：", "#2980b9")
    if SETTINGS.get("show_pending_candidate"): append_pending_tasks(pending_candidate, "🎯 Candidate 狀態任務：", "#16a085")

    p_spacer = soup.new_tag("p")
    p_spacer.append(soup.new_tag("br"))
    container.append(p_spacer)
            
    return container

def run_clear_logic():
    try:
        api_endpoint = f"{JIRA_URL}/wiki/rest/api/content"
        selected_dates = get_selected_dates()
        if not selected_dates: return print("⚠️ 未選擇任何要更新的日期(無法判斷目標頁面)。")

        target_title = get_target_report_title(selected_dates[0])
        print(f"\n=========================================\n🎯 目標週報頁面: {target_title} (清除模式)\n=========================================\n")
        print(f"🔍 正在 Confluence 搜尋頁面...")
        res = requests.get(api_endpoint, params={"title": target_title, "expand": "body.storage,version"}, auth=ADMIN_AUTH)
        pages = res.json().get('results', [])
        if not pages: return print(f"❌ 找不到目標頁面 {target_title}！")
            
        page_data = pages[0]
        page_id = page_data['id']
        html_content = page_data['body']['storage']['value']
        soup = BeautifulSoup(html_content, 'html.parser')
        
        page_needs_update = False
        cleaned_count = 0

        for element in soup.find_all(lambda tag: tag.has_attr('class') and any(c.startswith('daily-worklog-') for c in tag['class'])):
            element.extract()
            page_needs_update = True
            cleaned_count += 1
        
        for name, email in ACCOUNT_DICT.items():
            acc_id = get_account_id(email, name)
            target_mention = None
            if acc_id:
                ri_user = soup.find('ri:user', attrs={'ri:account-id': acc_id})
                if ri_user: target_mention = ri_user.find_parent('ac:link')
            if not target_mention:
                all_links = soup.find_all('ac:link')
                for link in all_links:
                    if name.lower() in str(link).lower() or (email and email.split('@')[0].lower() in str(link).lower()):
                        target_mention = link; break
            if not target_mention:
                target_mention = soup.find(string=re.compile(f"@{name}", re.I))
                if not target_mention and email:
                    target_mention = soup.find(string=re.compile(f"@{email.split('@')[0]}", re.I))
            if not target_mention: continue

            mention_container = target_mention.find_parent(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div'])
            if not mention_container: mention_container = target_mention

            if isinstance(mention_container, Tag) and mention_container.name in ['p', 'div', 'li', 'h2', 'h3', 'h4', 'h5', 'h6']:
                mention_container.name = 'h1'
                page_needs_update = True

            user_nodes = []
            current_node = mention_container.next_sibling
            while current_node:
                if isinstance(current_node, NavigableString) and str(current_node).strip().startswith('@'): break 
                if isinstance(current_node, Tag):
                    if current_node.name == 'ac:link' or current_node.find('ac:link'): break 
                    if current_node.name in ['h1', 'h2', 'hr']: break 
                    if str(current_node.get_text()).strip().startswith('@'): break
                user_nodes.append(current_node)
                current_node = current_node.next_sibling

            nodes_to_remove = []
            i = 0
            while i < len(user_nodes):
                node = user_nodes[i]
                text = node.get_text(strip=True) if isinstance(node, Tag) else str(node).strip()
                if re.match(r'^\[\d{4}/\d{2}/\d{2}\]', text):
                    nodes_to_remove.append(node)
                    i += 1
                    while i < len(user_nodes):
                        next_node = user_nodes[i]
                        next_text = next_node.get_text(strip=True) if isinstance(next_node, Tag) else str(next_node).strip()
                        if re.match(r'^\[\d{4}/\d{2}/\d{2}\]', next_text): break
                        nodes_to_remove.append(next_node)
                        i += 1
                else:
                    i += 1

            for node in nodes_to_remove:
                node.extract()
                page_needs_update = True
                cleaned_count += 1
        
        if page_needs_update:
            print(f"🧹 發現 {cleaned_count} 個日誌區塊/殘留，正在更新至 Confluence...")
            url = f"{api_endpoint}/{page_id}"
            
            payload = {
                "version": {"number": page_data['version']['number'] + 1, "minorEdit": SETTINGS.get("minor_edit")},
                "title": page_data['title'],
                "type": "page",
                "body": {"storage": {"value": str(soup), "representation": "storage"}},
                "metadata": {
                    "properties": {
                        "content-appearance-published": {"value": "full-width"},
                        "content-appearance-draft": {"value": "full-width"}
                    }
                }
            }
            update_res = requests.put(url, json=payload, auth=ADMIN_AUTH, headers={"Content-Type": "application/json"})
            if update_res.status_code == 200:
                print("🎉 大功告成！已成功清除本頁所有舊日誌。")
            else: print(f"❌ 儲存至 Confluence 失敗: {update_res.text}")
        else:
            print("📭 頁面乾淨無殘留，無需清除。")

    except Exception as e: print(f"\n❌ 程式發生意外錯誤: {e}")

def run_sync_logic():
    start_time = time.time()
    try:
        if SETTINGS.get("auto_clear_first"):
            print("=========================================")
            print("🧹 [排程任務] 執行批次寫入前的前置自動清除作業")
            print("=========================================")
            run_clear_logic()
            print("\n✅ 前置清除作業完成，準備開始批次寫入...\n")

        if not SETTINGS.get("filter_comment") and not SETTINGS.get("filter_started"):
            return print("❌ 錯誤：請至少在 settings_daily.json 啟用一種「智慧過濾機制」。")

        api_endpoint = f"{JIRA_URL}/wiki/rest/api/content"
        selected_dates = get_selected_dates()
        if not selected_dates: return print("⚠️ 未選擇任何要更新的日期。")

        target_title = get_target_report_title(selected_dates[0])
        print(f"\n=========================================\n🎯 目標週報頁面: {target_title}\n=========================================\n")
        
        print(f"🔍 正在 Confluence 搜尋頁面...")
        res = requests.get(api_endpoint, params={"title": target_title, "expand": "body.storage,version"}, auth=ADMIN_AUTH)
        
        if res.status_code != 200:
            print(f"❌ API 請求被 Confluence 拒絕！(狀態碼: {res.status_code})")
            print(f"💡 錯誤診斷提示：")
            if res.status_code == 401:
                print("   👉 [401 未授權]: 請檢查 GitHub Secrets 的 CONF_USER 與 CONF_PASS。注意：CONF_PASS 必須是 Atlassian API Token，不能是登入密碼！")
            elif res.status_code == 403:
                print("   👉 [403 權限不足]: 你的帳號沒有權限讀取此頁面，或 API Token 權限不足。")
            elif res.status_code == 404:
                print(f"   👉 [404 找不到]: 請檢查 CONF_URL ({JIRA_URL}) 是否正確。")
            print(f"📄 伺服器原始回傳內容: {res.text[:300]}")
            return

        pages = res.json().get('results', [])
        if not pages: return print(f"❌ 找不到目標頁面 {target_title}，請先確保週報已建立！")
            
        page_data = pages[0]
        page_id = page_data['id']
        html_content = page_data['body']['storage']['value']
        soup = BeautifulSoup(html_content, 'html.parser')
        
        page_needs_update = False
        total_logs_written = 0

        sample_date = selected_dates[0]
        monday = sample_date - timedelta(days=sample_date.weekday())
        sunday = monday + timedelta(days=6)
        min_date_str = monday.strftime("%Y%m%d")
        max_date_str = sunday.strftime("%Y%m%d")
        
        print("\n🧹 執行清潔防呆：正在檢查頁面中是否殘留非本週的歷史紀錄...")
        cleaned_count = 0
        for element in soup.find_all(lambda tag: tag.has_attr('class') and any(c.startswith('daily-worklog-') for c in tag['class'])):
            for c in element['class']:
                if c.startswith('daily-worklog-'):
                    date_str = c.replace('daily-worklog-', '')
                    if len(date_str) == 8 and date_str.isdigit():
                        if not (min_date_str <= date_str <= max_date_str):
                            element.extract()
                            page_needs_update = True
                            cleaned_count += 1
                            print(f"  └ 🗑️ 自動刪除複製頁面殘留的舊紀錄: {date_str}")
                    break
                    
        if cleaned_count > 0: print(f"  └ ✅ 共清理了 {cleaned_count} 筆非本週的歷史區塊。")
        else: print("  └ ✨ 頁面乾淨無殘留，無需清理。")

        min_date = min(selected_dates)
        min_date_tag = min_date.strftime("[%Y/%m/%d]")
        print(f"\n📡 啟動全域雷達：一次性掃描 Jira 自 {min_date_tag} 起的所有變更紀錄...")
        all_issues_pool = fetch_all_recent_issues(min_date)
        print(f"  └ 掃描完畢，大池子共找到 {len(all_issues_pool)} 筆曾被觸碰過的任務。")

        most_recent_date = max(selected_dates)
        target_date_tags = [d.strftime("[%Y/%m/%d]") for d in selected_dates]

        print("\n=========================================")
        print("🚀 開始針對個別成員過濾並寫入資料...")

        for name, email in ACCOUNT_DICT.items():
            acc_id = get_account_id(email, name)
            
            target_mention = None
            if acc_id:
                ri_user = soup.find('ri:user', attrs={'ri:account-id': acc_id})
                if ri_user: target_mention = ri_user.find_parent('ac:link')
                    
            if not target_mention:
                all_links = soup.find_all('ac:link')
                for link in all_links:
                    if name.lower() in str(link).lower() or (email and email.split('@')[0].lower() in str(link).lower()):
                        target_mention = link; break
            
            if not target_mention:
                target_mention = soup.find(string=re.compile(f"@{name}", re.I))
                if not target_mention and email:
                    target_mention = soup.find(string=re.compile(f"@{email.split('@')[0]}", re.I))
            if not target_mention: continue

            mention_container = target_mention.find_parent(['p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'div'])
            if not mention_container: mention_container = target_mention

            if isinstance(mention_container, Tag) and mention_container.name in ['p', 'div', 'li', 'h2', 'h3', 'h4', 'h5', 'h6']:
                mention_container.name = 'h1'
                page_needs_update = True

            user_nodes = []
            current_node = mention_container.next_sibling
            while current_node:
                if isinstance(current_node, NavigableString) and str(current_node).strip().startswith('@'): break 
                if isinstance(current_node, Tag):
                    if current_node.name == 'ac:link' or current_node.find('ac:link'): break 
                    if current_node.name in ['h1', 'h2', 'hr']: break 
                    if str(current_node.get_text()).strip().startswith('@'): break
                user_nodes.append(current_node)
                current_node = current_node.next_sibling

            existing_blocks = {}
            nodes_to_remove = []

            for node in user_nodes:
                if isinstance(node, Tag):
                    classes = node.get('class', [])
                    if classes and any(c.startswith('daily-worklog-') for c in classes):
                        for c in classes:
                            if c.startswith('daily-worklog-'):
                                d_str = c.replace('daily-worklog-', '')
                                existing_blocks[d_str] = node
                                nodes_to_remove.append(node)
                                break

            i = 0
            while i < len(user_nodes):
                node = user_nodes[i]
                if node in nodes_to_remove: 
                    i += 1; continue
                
                text = node.get_text(strip=True) if isinstance(node, Tag) else str(node).strip()
                matched_target = next((tdt for tdt in target_date_tags if text.startswith(tdt)), None)
                        
                if matched_target:
                    nodes_to_remove.append(node); i += 1
                    while i < len(user_nodes):
                        next_node = user_nodes[i]
                        if next_node in nodes_to_remove: break
                        next_text = next_node.get_text(strip=True) if isinstance(next_node, Tag) else str(next_node).strip()
                        if re.match(r'^\[\d{4}/\d{2}/\d{2}\]', next_text): break
                        nodes_to_remove.append(next_node); i += 1
                else: i += 1

            for node in nodes_to_remove:
                node.extract()
                page_needs_update = True

            for target_date in sorted(selected_dates, reverse=True):
                date_str_tag = target_date.strftime("[%Y/%m/%d]")
                safe_date_class = target_date.strftime("%Y%m%d")
                
                logs = extract_logs_from_issues(name, email, acc_id, target_date, all_issues_pool)
                total_mins = sum(log.get('duration_mins', 0) for log in logs)

                pending_in_progress, pending_waiting, pending_todo, pending_candidate = [], [], [], []
                if target_date == most_recent_date:
                    updated_keys = {log['key'] for log in logs}
                    pending_in_progress, pending_waiting, pending_todo, pending_candidate = fetch_pending_tasks(acc_id, updated_keys)
                
                if SETTINGS.get("show_pending_has_due"):
                    pending_in_progress = [p for p in pending_in_progress if p.get('duedate_dt') is not None]
                    pending_waiting = [p for p in pending_waiting if p.get('duedate_dt') is not None]
                    pending_todo = [p for p in pending_todo if p.get('duedate_dt') is not None]
                    pending_candidate = [p for p in pending_candidate if p.get('duedate_dt') is not None]

                has_pending_p = bool(SETTINGS.get("show_pending_inprogress") and pending_in_progress)
                has_pending_w = bool(SETTINGS.get("show_pending_waiting") and pending_waiting)
                has_pending_t = bool(SETTINGS.get("show_pending_todo") and pending_todo)
                has_pending_c = bool(SETTINGS.get("show_pending_candidate") and pending_candidate)
                
                if logs or has_pending_p or has_pending_w or has_pending_t or has_pending_c:
                    new_html_block = generate_style_2_html(soup, target_date, logs, pending_in_progress, pending_waiting, pending_todo, pending_candidate, total_mins)
                    existing_blocks[safe_date_class] = new_html_block
                    total_logs_written += (len(logs) + len(pending_in_progress) + len(pending_waiting) + len(pending_todo) + len(pending_candidate))
                    page_needs_update = True
                    
                    print(f"  ☑️ 成功處理 {name} ({date_str_tag} 有更新: {len(logs)} 筆, 進行: {len(pending_in_progress)} 筆, Waiting: {len(pending_waiting)} 筆, To Do: {len(pending_todo)} 筆, Candidate: {len(pending_candidate)} 筆):")
                    for log in logs:
                        trans = log.get('transition', '')
                        trans_text = f" {trans}" if trans else f" [{translate_status(log['status'])}]"
                        print(f"     └ [有更新] [{log['key']}] {log['summary'][:20]}.. {trans_text}")
                else:
                    if safe_date_class in existing_blocks:
                        del existing_blocks[safe_date_class]
                        page_needs_update = True

            if existing_blocks:
                insert_cursor = mention_container
                sorted_dates = sorted(existing_blocks.keys(), reverse=True)
                for d_str in sorted_dates:
                    block = existing_blocks[d_str]
                    insert_cursor.insert_after(block)
                    insert_cursor = block

        if page_needs_update:
            print(f"\n💾 發現頁面有變動，正在將最終結果儲存至 Confluence...")
            url = f"{api_endpoint}/{page_id}"
            
            payload = {
                "version": {"number": page_data['version']['number'] + 1, "minorEdit": SETTINGS.get("minor_edit")},
                "title": page_data['title'],
                "type": "page",
                "body": {"storage": {"value": str(soup), "representation": "storage"}},
                "metadata": {
                    "properties": {
                        "content-appearance-published": {"value": "full-width"},
                        "content-appearance-draft": {"value": "full-width"}
                    }
                }
            }
            update_res = requests.put(url, json=payload, auth=ADMIN_AUTH, headers={"Content-Type": "application/json"})
            if update_res.status_code == 200:
                notice_text = "🔇已啟動靜默更新" if SETTINGS.get("minor_edit") else "🔊已發送公開通知"
                print(f"🎉 大功告成！已成功更新 {total_logs_written} 筆任務紀錄 ({notice_text})！")
            else: print(f"❌ 儲存至 Confluence 失敗: {update_res.text}")
        else:
            print(f"\n📭 根據你選擇的日期，沒有找到任何需要變動的紀錄。")

    except Exception as e: print(f"\n❌ 程式發生意外錯誤: {e}")
    finally:
        elapsed = int(time.time() - start_time)
        mins, secs = divmod(elapsed, 60)
        time_str = f"{mins}m{secs}s" if mins > 0 else f"{secs}s"
        print(f"\n🏁 任務結束。 (總耗時: {time_str})")

if __name__ == "__main__":
    print("=== Confluence 自動填表機 (GitHub Actions Headless 版) ===")
    run_sync_logic()
```
