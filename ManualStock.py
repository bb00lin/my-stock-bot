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
        if not data:
            # 偵錯用：如果沒資料，印出 API 給出的訊息
            msg = res_json.get("msg", "No message")
            print(f"ℹ️ [{stock_id}] {dataset} 無數據. 原因: {msg}")
        return pd.DataFrame(data)
    except Exception as e:
        print(f"❌ API 請求失敗: {e}")
        return pd.DataFrame()

def get_stock_name_map():
    try:
        df = get_finmind_data("TaiwanStockInfo", "", "2025-01-01")
        if not df.empty and 'stock_id' in df.columns:
            return {str(row['stock_id']): row['stock_name'] for _, row in df.iterrows()}
        return {}
    except: return {}

STOCK_NAME_MAP = get_stock_name_map()

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
    try: requests.post(url, headers=headers, json=payload)
    except: pass

# ==========================================
# 2. 籌碼邏輯 (強化版：解決大戶空值問題)
# ==========================================
def get_detailed_chips(sid_clean):
    chips = {"fs": 0, "ss": 0, "big": 0.0, "v_ratio": 0.0, "v_status": "未知"}
    try:
        # --- 1. 法人買賣超 ---
        start_d = (datetime.date.today() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        df_i = get_finmind_data("TaiwanStockInstitutionalInvestorsBuySell", sid_clean, start_d)
        if not df_i.empty:
            def streak(name):
                d = df_i[df_i['name'] == name].sort_values('date', ascending=False)
                c = 0
                for _, r in d.iterrows():
                    if (int(r.get('buy', 0)) - int(r.get('sell', 0))) > 0: c += 1
                    else: break
                return c
            chips["fs"], chips["ss"] = streak('Foreign_Investor'), streak('Investment_Trust')

        # --- 2. 大戶持股 (核心修正：不限制起點日期) ---
        # 改為回溯 90 天，確保至少能抓到最新的週資料
        start_w = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%Y-%m-%d')
        df_h = get_finmind_data("TaiwanStockHoldingSharesPer", sid_clean, start_w)
        
        # 備援：如果 90 天沒資料，嘗試抓取「該資料集」最後一筆資料
        if df_h.empty:
            df_h = get_finmind_data("TaiwanStockHoldingSharesPer", sid_clean, "2024-01-01")

        if not df_h.empty:
            latest_date = df_h['date'].max()
            df_latest = df_h[df_h['date'] == latest_date].copy()
            df_latest['lvl'] = df_latest['hold_shares_level'].astype(str).str.replace(' ', '')
            
            # 匹配大戶級別 (11級=400張以上, 15級=1000張以上)
            mask = df_latest['lvl'].str.contains('400|600|800|1000|以上|11|12|13|14|15')
            big_val = df_latest[mask]['percent'].sum()
            
            if big_val == 0: # 另一種 API 格式可能出現在尾端
                big_val = df_latest.sort_values('hold_shares_level').tail(5)['percent'].sum()
            
            chips["big"] = round(float(big_val), 1)
            print(f"📊 [{sid_clean}] 成功獲取大戶數據: {chips['big']}% ({latest_date})")
                
    except Exception as e:
        print(f"❌ 籌碼解析異常 ({sid_clean}): {e}")

    # --- 3. 量能計算 ---
    try:
        ticker = f"{sid_clean}.TW" if int(sid_clean) < 9000 else f"{sid_clean}.TWO"
        h = yf.Ticker(ticker).history(period="10d")
        if len(h) >= 3:
            v_today, v_avg = h['Volume'].iloc[-1], h['Volume'].iloc[-6:-1].mean()
            chips["v_ratio"] = round(v_today / v_avg, 1) if v_avg > 0 else 0
            chips["v_status"] = "🔥爆量" if chips["v_ratio"] > 1.8 else "☁️量平"
    except: pass
    return chips

def run_diagnostic(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        tk_str = f"{clean_id}.TW" if int(clean_id) < 9000 else f"{clean_id}.TWO"
        stock = yf.Ticker(tk_str)
        df = stock.history(period="1y")
        if df.empty: return None, None
        
        ch_name = STOCK_NAME_MAP.get(clean_id, stock.info.get('shortName', '未知'))
        curr_p = round(df.iloc[-1]['Close'], 2)
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        rsi = round(RSIIndicator(df['Close']).rsi().iloc[-1], 1)
        
        info = stock.info
        eps = info.get('trailingEps', 0) or 0
        margin = round((info.get('grossMargins', 0) or 0) * 100, 1)
        pe = info.get('trailingPE', 0) or "N/A"
        
        c = get_detailed_chips(clean_id)
        bias = round(((curr_p-ma60)/ma60)*100, 1)
        
        line_msg = (
            f"=== {clean_id} {ch_name} ===\n"
            f"現價：{curr_p} | RSI：{rsi}\n"
            f"法人：外{c['fs']}d 投{c['ss']}d | 大戶:{c['big']}%\n"
            f"量能：{c['v_status']}({c['v_ratio']}x)\n"
            f"趨勢：{'🔥多頭' if curr_p > ma60 else '☁️空頭'}(乖離{bias:+.1f}%)\n"
            f"提示：{'⚠️高檔防回' if bias > 15 else '✅位階安全'}"
        )

        sheet_row = [
            str(datetime.date.today()), clean_id, ch_name, 
            curr_p, rsi, eps, pe, margin, 
            c['fs'], c['ss'], c['big'], f"{c['v_status']}({c['v_ratio']}x)",
            "🔥多頭" if curr_p > ma60 else "☁️空頭", bias, 
            "⚠️高檔防回" if bias > 15 else "✅位階安全"
        ]
        return line_msg, sheet_row
    except Exception as e:
        print(f"❌ 診斷出錯 ({sid}): {e}")
        return None, None

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2330"
    targets = input_str.replace(',', ' ').split()
    results_sheet = []
    
    for t in targets:
        l_msg, s_row = run_diagnostic(t.strip())
        if l_msg:
            send_line_message(l_msg)
            results_sheet.append(s_row)
        time.sleep(1)
    
    if results_sheet:
        sync_to_sheets(results_sheet)
