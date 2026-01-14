import os, yfinance as yf, pandas as pd, requests, datetime, time, sys
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from ta.momentum import RSIIndicator

# ==========================================
# 1. 環境設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
# 若您有 FinMind Token 請設定在 GitHub Secrets，否則使用匿名限制
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNi0wMS0xNCAyMzoxMTo0MSIsInVzZXJfaWQiOiJiYjAwbGlubiIsImVtYWlsIjoiYmIwMGxpbkBnbWFpbC5jb20iLCJpcCI6IjExOC4xNTAuMTIwLjcyIn0.Yp8X-_bkA9j6y3pSJJjHposfxSm0MvtnLkhtlABpQxQ
") 
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"

def get_finmind_data(dataset, stock_id, start_date):
    """最穩定的底層 API 請求方式"""
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
            print(f"⚠️ API 回傳數據為空: {dataset} ({stock_id})")
        return pd.DataFrame(data)
    except Exception as e:
        print(f"❌ API 請求失敗: {e}")
        return pd.DataFrame()

def get_stock_name_map():
    try:
        # 獲取名稱對照表
        df = get_finmind_data("TaiwanStockInfo", "", "")
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
        sheet.append_rows(data_list)
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
# 2. 籌碼邏輯 (強化日期回溯與數據匹配)
# ==========================================
def get_detailed_chips(sid_clean):
    chips = {"fs": 0, "ss": 0, "big": 0.0, "v_ratio": 0.0, "v_status": "未知"}
    try:
        # --- 1. 法人連買 (回溯 40 天) ---
        start_d = (datetime.date.today() - datetime.timedelta(days=40)).strftime('%Y-%m-%d')
        df_i = get_finmind_data("TaiwanStockInstitutionalInvestorsBuySell", sid_clean, start_d)
        
        if not df_i.empty and 'name' in df_i.columns:
            def count_buy_streak(name):
                d = df_i[df_i['name'] == name].sort_values('date', ascending=False)
                c = 0
                for _, r in d.iterrows():
                    net_buy = (int(r.get('buy', 0)) - int(r.get('sell', 0)))
                    if net_buy > 0: c += 1
                    elif net_buy < 0: break
                return c
            chips["fs"], chips["ss"] = count_buy_streak('Foreign_Investor'), count_buy_streak('Investment_Trust')
        
        # --- 2. 大戶持股 (強化回溯 60 天，確保集保數據不漏抓) ---
        start_w = (datetime.date.today() - datetime.timedelta(days=60)).strftime('%Y-%m-%d')
        df_h = get_finmind_data("TaiwanStockHoldingSharesPer", sid_clean, start_w)
        
        if not df_h.empty and 'hold_shares_level' in df_h.columns:
            latest_date = df_h['date'].max()
            df_latest = df_h[df_h['date'] == latest_date].copy()
            # 強制清理所有格式
            df_latest['level_str'] = df_latest['hold_shares_level'].astype(str).str.replace(' ', '')
            
            # 匹配 400 張以上 (涵蓋所有大戶級別)
            mask = df_latest['level_str'].str.contains('400|600|800|1000|以上')
            big_val = df_latest[mask]['percent'].sum()
            
            # 如果還是 0，則抓取該股「持股級別」最後 5 筆直接相加 (因為級別是從小排到大)
            if big_val == 0:
                big_val = df_latest.sort_values('hold_shares_level').tail(5)['percent'].sum()
            
            chips["big"] = round(float(big_val), 1)
            print(f"📊 [{sid_clean}] 抓取日期: {latest_date}, 大戶%: {chips['big']}%")
                
    except Exception as e:
        print(f"❌ 籌碼解析異常 ({sid_clean}): {e}")

    # --- 3. 量能計算 ---
    try:
        ticker = f"{sid_clean}.TW" if int(sid_clean) < 9000 else f"{sid_clean}.TWO"
        h = yf.Ticker(ticker).history(period="10d")
        if len(h) >= 3:
            v_today, v_avg = h['Volume'].iloc[-1], h['Volume'].iloc[-6:-1].mean()
            chips["v_ratio"] = round(v_today / v_avg, 1) if v_avg > 0 else 0
            chips["v_status"] = "🔥爆量" if chips["v_ratio"] > 2.0 else "☁️量平"
    except: pass
    return chips

# ==========================================
# 3. 核心診斷邏輯
# ==========================================
def run_diagnostic(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock_ticker = f"{clean_id}.TW" if int(clean_id) < 9000 else f"{clean_id}.TWO"
        stock = yf.Ticker(stock_ticker)
        df = stock.history(period="1y")
        if df.empty: return None, None
        
        ch_name = STOCK_NAME_MAP.get(clean_id, stock.info.get('shortName', '未知'))
        curr_p = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        info = stock.info
        eps = info.get('trailingEps', 0) or 0
        margin = (info.get('grossMargins', 0) or 0) * 100
        pe = info.get('trailingPE', 0) or "N/A"
        
        c = get_detailed_chips(clean_id)
        
        bias = ((curr_p-ma60)/ma60)*100
        line_msg = (
            f"=== {clean_id} {ch_name} ===\n"
            f"現價：{curr_p:.2f} | RSI：{rsi:.1f}\n"
            f"法人：外{c['fs']}d 投{c['ss']}d | 大戶:{c['big']:.1f}%\n"
            f"量能：{c['v_status']}({c['v_ratio']}x)\n"
            f"趨勢：{'🔥多頭' if curr_p > ma60 else '☁️空頭'}(乖離{bias:+.1f}%)\n"
            f"提示：{'⚠️高檔防回' if bias > 15 else '✅位階安全'}"
        )

        sheet_row = [
            str(datetime.date.today()), clean_id, ch_name, 
            curr_p, round(rsi, 1), eps, pe, round(margin, 1), 
            c['fs'], c['ss'], c['big'], f"{c['v_status']}({c['v_ratio']}x)",
            "🔥多頭" if curr_p > ma60 else "☁️空頭", round(bias, 1), 
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
