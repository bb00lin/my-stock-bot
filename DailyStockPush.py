import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
import logging
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 0. 靜音設定與全域變數
# ==========================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Email 設定
MAIL_RECEIVERS = ['bb00lin@gmail.com']
MAIL_USER = os.environ.get('MAIL_USERNAME')
MAIL_PASS = os.environ.get('MAIL_PASSWORD')

# 優先使用高額度正式版模型
MODEL_CANDIDATES = [
    "gemini-2.0-flash",      # 🚀 首選
    "gemini-1.5-flash",      
    "gemini-1.5-pro",
]

# 全域變數
HAS_GENAI = False
AI_CLIENT = None

# 全局 Token 統計記帳本
GLOBAL_TOKEN_BILLING = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "api_calls": 0
}

# ==========================================
# [啟動檢查] AI 自我診斷
# ==========================================
def check_ai_health():
    global HAS_GENAI, AI_CLIENT
    print("🤖 正在進行 AI 模型連線測試 (使用 google-genai SDK)...")
    if not GEMINI_API_KEY:
        print("⚠️ 警告: 未設定 GEMINI_API_KEY，將跳過 AI 功能。")
        HAS_GENAI = False
        return

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        for model_name in MODEL_CANDIDATES:
            try:
                response = client.models.generate_content(model=model_name, contents="Hi")
                if response and response.text:
                    print(f"✅ AI 測試成功！將使用模型: {model_name}")
                    HAS_GENAI = True
                    AI_CLIENT = client
                    return
            except: continue
        print("❌ 失敗: 所有候選模型皆無法連線。將以「無 AI 模式」繼續執行。")
        HAS_GENAI = False
    except Exception as e:
        print(f"❌ AI 初始化錯誤: {e}")
        HAS_GENAI = False

check_ai_health()

# ==========================================
# ✨ 本次新增：LINE 官方帳號免費發送額度查詢
# ==========================================
def get_line_quota_report():
    """透過 LINE API 自動查詢本月已發送則數與剩餘免費額度"""
    if not LINE_ACCESS_TOKEN:
        return "⚠️ 未設定 LINE Token，無法查詢額度"
        
    headers = {"Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    
    try:
        # 1. 查詢當月限制總量 (免費方案預設通常為 200 或因官方調整而異)
        quota_url = "https://api.line.me/v2/bot/message/quota"
        quota_res = requests.get(quota_url, headers=headers).json()
        value_type = quota_res.get("type", "none")
        
        # 如果是無限制方案，直接回傳
        if value_type == "none":
            return "♾️ 目前 LINE 方案為無限制則數"
            
        total_limit = quota_res.get("value", 0)
        
        # 2. 查詢當月透過 API 已發送的累積訊息量
        consumption_url = "https://api.line.me/v2/bot/message/quota/consumption"
        consumption_res = requests.get(consumption_url, headers=headers).json()
        total_consumed = consumption_res.get("totalUsage", 0)
        
        # 3. 計算剩餘額度
        remaining_quota = total_limit - total_consumed
        
        # 4. 根據剩餘容量給予警示符號
        alert_tag = "🟢 安全" if remaining_quota > 50 else ("🟡 偏低" if remaining_quota > 15 else "🚨 嚴重不足")
        
        report_str = (
            f"📊 ── LINE 本月額度診斷 ──\n"
            f"🔹 當月免費總量：{total_limit} 則\n"
            f"🔹 本月已發送量：{total_consumed} 則\n"
            f"🔹 目前剩餘額度：{remaining_quota} 則 [{alert_tag}]"
        )
        return report_str
    except Exception as e:
        return f"⚠️ LINE 額度查詢失敗: {e}"

# ==========================================
# Token 費用換算邏輯
# ==========================================
def calculate_twd_cost():
    USD_PER_M_INPUT = 0.075   
    USD_PER_M_OUTPUT = 0.30   
    FX_USD_TO_TWD = 32.5      

    p_tokens = GLOBAL_TOKEN_BILLING["prompt_tokens"]
    c_tokens = GLOBAL_TOKEN_BILLING["completion_tokens"]

    usd_cost = ((p_tokens / 1_000_000) * USD_PER_M_INPUT) + ((c_tokens / 1_000_000) * USD_PER_M_OUTPUT)
    twd_cost = usd_cost * FX_USD_TO_TWD
    return round(twd_cost, 4)

def record_token_usage(response):
    try:
        if response and hasattr(response, 'usage_metadata') and response.usage_metadata:
            meta = response.usage_metadata
            GLOBAL_TOKEN_BILLING["prompt_tokens"] += meta.prompt_token_count
            GLOBAL_TOKEN_BILLING["completion_tokens"] += meta.candidates_token_count
            GLOBAL_TOKEN_BILLING["total_tokens"] += meta.total_token_count
            GLOBAL_TOKEN_BILLING["api_calls"] += 1
    except: pass

# ==========================================
# Google Sheets 連線與資料獲取 (維持原邏輯)
# ==========================================
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    if not json_key_str: return None
    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except: return None

def get_global_stock_info():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except: return {}

STOCK_INFO_MAP = get_global_stock_info()

def get_watch_list_from_sheet():
    try:
        client = get_gspread_client()
        if not client: return []
        try: sheet = client.open("WATCH_LIST").worksheet("WATCH_LIST")
        except: sheet = client.open("WATCH_LIST").get_worksheet(0)
        records = sheet.get_all_records()
        watch_data = []
        for row in records:
            raw_sid = str(row.get('股票代號', '')).strip()
            if not raw_sid: continue
            if raw_sid.isdigit():
                sid = "00" + raw_sid if len(raw_sid) == 3 else (raw_sid.zfill(4) if len(raw_sid) < 4 else raw_sid)
            else: sid = raw_sid
            is_hold = str(row.get('我的庫存倉位', '')).strip().upper() == 'Y'
            cost = row.get('平均成本', 0)
            if cost == '': cost = 0
            watch_data.append({'sid': sid, 'is_hold': is_hold, 'cost': float(cost)})
        return watch_data
    except: return []

# ==========================================
# 輔助數據運算 (與先前版本一致)
# ==========================================
def get_streak_only(sid_clean):
    try:
        dl = DataLoader()
        start = (datetime.date.today() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        clean_id = ''.join(filter(str.isdigit, str(sid_clean)))
        df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start)
        if df is None or df.empty: return 0, 0
        def count_s(name):
            d = df[df['name'] == name].sort_values('date', ascending=False)
            c = 0
            for _, r in d.iterrows():
                if (r['buy'] - r['sell']) > 0: c += 1
                else: break
            return c
        return count_s('Foreign_Investor'), count_s('Investment_Trust')
    except: return 0, 0

def get_vol_status_str(ratio):
    if ratio > 1.8: return f"🔥爆量({ratio:.1f}x)"
    elif ratio > 1.2: return f"📈溫和({ratio:.1f}x)"
    elif ratio < 0.7: return f"⚠️縮量({ratio:.1f}x)"
    else: return f"☁️量平({ratio:.1f}x)"

def check_ma_status(p, ma5, ma10, ma20, ma60):
    alerts = []
    THRESHOLD = 0.015 
    if ma5 > 0:
        gap_ma5 = (p - ma5) / ma5
        if 0 < gap_ma5 <= THRESHOLD: alerts.append(f"⚡回測5日線(剩{gap_ma5:.1%})")
        elif -THRESHOLD <= gap_ma5 < 0: alerts.append(f"⚠️跌破5日線({gap_ma5:.1%})")
    if ma20 > 0:
        gap_ma20 = (p - ma20) / ma20
        if 0 < gap_ma20 <= THRESHOLD: alerts.append(f"🛡️回測月線(剩{gap_ma20:.1%})")
        elif -THRESHOLD <= gap_ma20 < 0: alerts.append(f"☠️跌破月線({gap_ma20:.1%})")
    if ma60 > 0:
        gap_ma60 = (p - ma60) / ma60
        if abs(gap_ma60) > 0.15: 
            alerts.append("🔥乖離過大" if gap_ma60 > 0 else "❄️嚴重超跌")
    return " | ".join(alerts) if alerts else ""

def check_golden_entry(df_hist):
    try:
        if len(df_hist) < 65: return False, ""
        latest, prev = df_hist.iloc[-1], df_hist.iloc[-2]
        close = latest['Close']
        ma20 = df_hist['Close'].rolling(20).mean().iloc[-1]
        ma60 = df_hist['Close'].rolling(60).mean().iloc[-1]
        if not (close > ma20 and ma20 > ma60): return False, "非多頭趨勢"
        past_4_days = df_hist.iloc[-5:-1]
        drop_days = 0
        for i in range(len(past_4_days)):
            if past_4_days.iloc[i]['Close'] < past_4_days.iloc[i]['Open'] or past_4_days.iloc[i]['Close'] < past_4_days.iloc[i-1]['Close']: drop_days += 1
        if drop_days < 2: return False, "無明顯回檔"
        if not (close > latest['Open'] and close > prev['Close']): return False, "今日未轉強"
        vol_ma5 = df_hist['Volume'].iloc[-6:-1].mean()
        if not (prev['Volume'] < vol_ma5) and latest['Volume'] < prev['Volume']: return False, "攻擊量不足"
        return True, "🔥黃金買點:量縮回後買上漲"
    except: return False, ""

def get_limit_up_potential(r):
    score = 0
    reasons = []
    if r['p'] > r['ma5'] and r['ma5'] > r['ma10'] and r['ma10'] > r['ma20']:
        score += 30; reasons.append("🔥均線多頭發散")
    if r['ss'] > 0: score += 30; reasons.append("🏦投信點火")
    elif r['fs'] >= 3: score += 20; reasons.append("💰外資連買")
    if r['vol_r'] >= 1.8: score += 20; reasons.append("📈出量攻擊")
    if r['d1'] > 0.03: score += 20; reasons.append("🚀長紅棒")
    return score, " | ".join(reasons)

# ==========================================
# AI 策略生成器 (維持原邏輯)
# ==========================================
def get_gemini_strategy(data):
    if not HAS_GENAI or not AI_CLIENT: return "AI 服務暫停"
    profit_info = "目前無庫存，純觀察"
    if data['is_hold']:
        roi = ((data['p'] - data['cost']) / data['cost']) * 100
        profit_info = f"🔴庫存持有中 (成本:{data['cost']} | 現價:{data['p']} | 損益:{roi:+.2f}%)"

    prompt = f"針對個股 {data['name']} ({data['id']}) 進行短線診斷。現價：{data['p']}，5日線: {data['ma5']}，20日線: {data['ma20']}。{profit_info}。請給出約 80 字操作建議與明確防守價。"

    for model_name in MODEL_CANDIDATES:
        try:
            response = AI_CLIENT.models.generate_content(model=model_name, contents=prompt)
            record_token_usage(response)  
            return response.text.replace('\n', ' ').strip()
        except:
            time.sleep(1)
            continue
    return "AI 連線忙碌中"

def generate_and_save_summary(data_list, report_time_str):
    if not HAS_GENAI or not AI_CLIENT: return "本次報告未包含 AI 總結"
    inventory_txt = "".join([f"- {r['name']}({r['id']}) | 現價:{r['p']}\n" for r in data_list if r['is_hold']])
    watchlist_txt = "".join([f"- {r['name']}({r['id']}) | 現價:{r['p']}\n" for r in data_list if not r['is_hold']])
    prompt = f"請擔任台股投資總監，根據以下持股與觀察名單，撰寫專業的五章節戰略日報。庫存：\n{inventory_txt}\n觀察：\n{watchlist_txt}"

    for model_name in MODEL_CANDIDATES:
        try:
            response = AI_CLIENT.models.generate_content(model=model_name, contents=prompt)
            record_token_usage(response)  
            return response.text
        except:
            time.sleep(2)
            continue
    return "AI 生成總結報告失敗"

def sync_to_sheets(data_list):
    try:
        client = get_gspread_client()
        if not client: return
        client.open("全能金流診斷報表").get_worksheet(0).append_rows(data_list, value_input_option='USER_ENTERED')
    except Exception as e: print(f"⚠️ Sheets 失敗: {e}")

def send_email(subject, body):
    if not MAIL_USER or not MAIL_PASS: return
    msg = MIMEMultipart(); msg['From'] = MAIL_USER; msg['To'] = ", ".join(MAIL_RECEIVERS); msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587); server.starttls(); server.login(MAIL_USER, MAIL_PASS)
        server.send_message(msg); server.quit()
        print("✅ 郵件發送成功")
    except Exception as e: print(f"❌ 郵件失敗: {e}")

# ==========================================
# 8. 主程式執行區塊 (雙費用監控核心版)
# ==========================================
def main():
    current_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')
    watch_data_list = get_watch_list_from_sheet()
    if not watch_data_list: return

    results_line, results_sheet = [], []
    for idx, stock_data in enumerate(watch_data_list):
        res = fetch_pro_metrics(stock_data)
        if res:
            results_line.append(res)
            results_sheet.append([current_time, res['id'], res['name'], "📦庫存" if res['is_hold'] else "👀觀察", res['score'], res['rsi'], res['industry'], res['bias_str'], res['vol_str'], res['fs'], res['ss'], res['p'], res['yield'], res['amt_t'], res['d1'], res['d5'], res['m1'], res['m6'], res['risk'], res['trend'], res['hint'], res['ai_strategy']])
        if idx < len(watch_data_list) - 1: time.sleep(2.0)
    
    if results_line:
        time.sleep(10) 
        summary_text = generate_and_save_summary(results_line, current_time)
        sync_to_sheets(results_sheet)

        # 🚀 1. 計算 Gemini AI 最終開銷
        twd_cost = calculate_twd_cost()
        
        # 🚀 2. ✨動態撈取當前 LINE 官方帳號免費額度狀態
        line_quota_report = get_line_quota_report()

        # HTML 版成本報告 (用於 Email 最下方)
        cost_report_html = f"""
        <div style='background-color:#fff9db; padding:15px; border-left:5px solid #fcc419; margin-top:20px; font-family:sans-serif;'>
            <h3 style='margin-top:0; color:#e67e22;'>💰 今日運作成本診斷報告</h3>
            <p><b>【Gemini API 帳單】</b><br>
            - 消耗總 Tokens：<span style='color:#d9480f;'>{GLOBAL_TOKEN_BILLING['total_tokens']:,}</span><br>
            - 預估台幣費用：<span style='color:#c92a2a;'><b>NT$ {twd_cost} 元</b></span></p>
            <p style='margin-bottom:0;'><b>【LINE Bot 免費額度】</b><br>
            {line_quota_report.replace('\n', '<br>')}</p>
        </div>
        """

        # 發送 Email 完整戰報 (內含雙成本診斷)
        email_body = f"<html><body><h2>📊 {current_time} 全能金流診斷</h2><pre style='font-family:sans-serif; white-space:pre-wrap;'>{summary_text}</pre><hr>{cost_report_html}</body></html>"
        send_email(f"[{current_time}] 台股 AI 戰報 (附成本與 LINE 額度診斷)", email_body)

        # 發送精簡 LINE 通知 (完美結合兩大監控指標)
        if LINE_ACCESS_TOKEN:
            line_msg = (
                f"📊 【{current_time} 戰略報告已更新】\n\n"
                f"今日自選股診斷已執行完畢，詳細戰報請查收 Email。\n\n"
                f"── 💸 今日 AI 帳單明細 ──\n"
                f"🔹 總消耗 Tokens：{GLOBAL_TOKEN_BILLING['total_tokens']:,}\n"
                f"💰 今日預估費用：NT$ {twd_cost} 元\n\n"
                f"{line_quota_report}"  # ✨ 自動黏貼本月 LINE 剩餘額度
            )
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
            payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": line_msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)
            print("✅ 整合 LINE 本月發送額度查詢之推播大獲成功！")

if __name__ == "__main__":
    main()
