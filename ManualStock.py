import os, yfinance as yf, pandas as pd, requests, datetime, time, sys
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from ta.momentum import RSIIndicator

# ==========================================
# 1. 環境設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN")

def get_finmind_data(dataset, stock_id, start_date):
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "token": FINMIND_TOKEN,
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        res_json = res.json()
        data = res_json.get("data", [])
        return pd.DataFrame(data), res_json.get("msg", "")
    except Exception as e:
        return pd.DataFrame(), str(e)

def sync_to_sheets(data_list):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name('google_key.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("個股深度診斷").get_worksheet(0)
        sheet.append_rows(data_list, value_input_option='USER_ENTERED')
        print(f"✅ 成功同步 {len(data_list)} 筆診斷結果至雲端")
    except Exception as e:
        print(f"⚠️ Google Sheets 同步失敗: {e}")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

# ==========================================
# 2. 籌碼邏輯 (自動處理 N/A 問題)
# ==========================================
def get_detailed_chips(sid_clean):
    chips = {"fs": 0, "ss": 0, "chip_val": "N/A", "v_ratio": 0.0, "v_status": "未知"}
    try:
        start_d = (datetime.date.today() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        
        # 1. 法人連買 (免費權限可用)
        df_i, _ = get_finmind_data("TaiwanStockInstitutionalInvestorsBuySell", sid_clean, start_d)
        if not df_i.empty:
            def streak(name):
                d = df_i[df_i['name'] == name].sort_values('date', ascending=False)
                c = 0
                for _, r in d.iterrows():
                    if (int(r.get('buy', 0)) - int(r.get('sell', 0))) > 0: c += 1
                    else: break
                return c
            chips["fs"], chips["ss"] = streak('Foreign_Investor'), streak('Investment_Trust')

        # 2. 嘗試抓取大戶，若失敗則抓融資
        df_h, msg = get_finmind_data("TaiwanStockHoldingSharesPer", sid_clean, start_d)
        
        if not df_h.empty and "Please update your user level" not in msg:
            latest_date = df_h['date'].max()
            df_latest = df_h[df_h['date'] == latest_date].copy()
            df_latest['lvl'] = df_latest['hold_shares_level'].astype(str)
            mask = df_latest['lvl'].str.contains('400|600|800|1000|以上|11|12|13|14|15')
            chips["chip_val"] = f"{round(float(df_latest[mask]['percent'].sum()), 1)}%"
        else:
            # 備援指標：融資增減 (免費權限可用)
            df_m, _ = get_finmind_data("TaiwanStockMarginPurchaseEvid", sid_clean, start_d)
            if not df_m.empty:
                df_m = df_m.sort_values('date')
                m_diff = int(df_m.iloc[-1]['MarginPurchaseBuy']) - int(df_m.iloc[-1]['MarginPurchaseSell'])
                chips["chip_val"] = f"{'+' if m_diff > 0 else ''}{m_diff}張(融資)"

    except: pass

    # 3. 量能計算
    try:
        ticker = f"{sid_clean}.TW" if int(sid_clean) < 9000 else f"{sid_clean}.TWO"
        h = yf.Ticker(ticker).history(period="10d")
        v_today, v_avg = h['Volume'].iloc[-1], h['Volume'].iloc[-6:-1].mean()
        chips["v_ratio"] = round(v_today / v_avg, 1) if v_avg > 0 else 0
        chips["v_status"] = "🔥爆量" if chips["v_ratio"] > 1.8 else "☁️量平"
    except: pass
    return chips

def run_diagnostic(sid):
    try:
        clean_id = str(sid).strip()
        tk_str = f"{clean_id}.TW" if int(clean_id) < 9000 else f"{clean_id}.TWO"
        stock = yf.Ticker(tk_str)
        df = stock.history(period="1y")
        if df.empty: return None, None
        
        curr_p = round(df.iloc[-1]['Close'], 2)
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        rsi = round(RSIIndicator(df['Close']).rsi().iloc[-1], 1)
        
        c = get_detailed_chips(clean_id)
        bias = round(((curr_p-ma60)/ma60)*100, 1)
        
        line_msg = (
            f"=== {clean_id} ===\n"
            f"現價：{curr_p} | RSI：{rsi}\n"
            f"法人：外{c['fs']} 投{c['ss']}\n"
            f"籌碼：{c['chip_val']}\n"
            f"量能：{c['v_status']}({c['v_ratio']}x)\n"
            f"趨勢：{'🔥多頭' if curr_p > ma60 else '☁️空頭'}"
        )

        sheet_row = [
            str(datetime.date.today()), clean_id, "", 
            curr_p, rsi, "", "", "", 
            c['fs'], c['ss'], c['chip_val'], f"{c['v_status']}({c['v_ratio']}x)",
            "🔥多頭" if curr_p > ma60 else "☁️空頭", bias, 
            "⚠️高檔防回" if bias > 15 else "✅位階安全"
        ]
        return line_msg, sheet_row
    except Exception as e:
        print(f"❌ 診斷出錯 ({sid}): {e}")
        return None, None

if __name__ == "__main__":
    targets = sys.argv[1].replace(',', ' ').split()
    results = []
    for t in targets:
        l_msg, s_row = run_diagnostic(t.strip())
        if l_msg:
            send_line_message(l_msg)
            results.append(s_row)
        time.sleep(1)
    if results:
        sync_to_sheets(results)
