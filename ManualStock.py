import os, yfinance as yf, pandas as pd, requests, datetime, time, sys
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

# ==========================================
# 1. 環境設定與名稱對照初始化
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"

def get_stock_name_map():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): row['stock_name'] for _, row in df.iterrows()}
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
# 2. 籌碼與量能邏輯 (強制修復大戶 % 顯示問題)
# ==========================================
def get_detailed_chips(sid_clean):
    chips = {"fs": 0, "ss": 0, "big": 0.0, "v_ratio": 0.0, "v_status": "未知"}
    try:
        dl = DataLoader()
        # --- 法人連買 ---
        start_d = (datetime.date.today() - datetime.timedelta(days=40)).strftime('%Y-%m-%d')
        df_i = dl.taiwan_stock_institutional_investors(stock_id=sid_clean, start_date=start_d)
        if df_i is not None and not df_i.empty:
            def count_buy_streak(name):
                d = df_i[df_i['name'] == name].sort_values('date', ascending=False)
                c = 0
                for _, r in d.iterrows():
                    net_buy = r['buy'] - r['sell']
                    if net_buy > 0: c += 1
                    elif net_buy < 0: break
                return c
            chips["fs"], chips["ss"] = count_buy_streak('Foreign_Investor'), count_buy_streak('Investment_Trust')
        
        # --- 大戶持股 (強化版：模糊匹配與多層級偵測) ---
        start_w = (datetime.date.today() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        df_h = dl.taiwan_stock_holding_shares_per(stock_id=sid_clean, start_date=start_w)
        
        if df_h is not None and not df_h.empty:
            # 1. 取得最近一次有數據的日期
            latest_date = df_h['date'].max()
            df_latest = df_h[df_h['date'] == latest_date].copy()
            
            # 2. 清理級別字串 (去除空格)
            df_latest['hold_shares_level'] = df_latest['hold_shares_level'].str.replace(' ', '')
            
            # 3. 嘗試多種匹配方式
            # 方式 A: 匹配 400 張以上的所有層級
            targets = ['400-600', '600-800', '800-1000', '1000以上', '400-600股', '600-800股', '800-1000股', '1000股以上']
            big_total = df_latest[df_latest['hold_shares_level'].isin(targets)]['percent'].sum()
            
            # 方式 B: 萬一方式 A 還是 0 (有些 API 回傳格式不同)，使用大範圍關鍵字匹配
            if big_total == 0:
                big_total = df_latest[df_latest['hold_shares_level'].str.contains('400|600|800|1000|以上', na=False)]['percent'].sum()
            
            # 方式 C: 極端情況 (防止重複計算)，若超過 100 則修正
            chips["big"] = min(big_total, 100.0)
            print(f"DEBUG [{sid_clean}]: 日期 {latest_date}, 偵測到大戶% {chips['big']}%")
            
    except Exception as e:
        print(f"❌ 籌碼分析錯誤 ({sid_clean}): {e}")

    try:
        ticker = f"{sid_clean}.TW" if int(sid_clean) < 9000 else f"{sid_clean}.TWO"
        h = yf.Ticker(ticker).history(period="10d")
        if len(h) >= 3:
            v_today, v_avg = h['Volume'].iloc[-1], h['Volume'].iloc[-6:-1].mean()
            chips["v_ratio"] = v_today / v_avg if v_avg > 0 else 0
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
        trend = "🔥多頭" if curr_p > ma60 else "☁️空頭"
        bias = ((curr_p-ma60)/ma60)*100
        tip = "⚠️高檔防回" if bias > 15 else "✅位階安全"

        line_msg = (
            f"=== {clean_id} {ch_name} ===\n"
            f"現價：{curr_p:.2f} | RSI：{rsi:.1f}\n"
            f"法人：外{c['fs']}d 投{c['ss']}d | 大戶:{c['big']:.1f}%\n"
            f"量能：{c['v_status']}({c['v_ratio']:.1f}x)\n"
            f"趨勢：{trend}(乖離{bias:+.1f}%)\n"
            f"提示：{tip}"
        )

        sheet_row = [
            str(datetime.date.today()), clean_id, ch_name, 
            curr_p, round(rsi, 1), eps, pe, round(margin, 1), 
            c['fs'], c['ss'], round(c['big'], 1), f"{c['v_status']}({c['v_ratio']:.1f}x)",
            trend, round(bias, 1), tip
        ]
        return line_msg, sheet_row
    except Exception as e:
        print(f"❌ 診斷失敗 ({sid}): {e}")
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
