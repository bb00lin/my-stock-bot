import os, yfinance as yf, pandas as pd, requests, time, datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 1. 配置與對照表初始化
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"
WATCH_LIST = ["6770", "6706", "6684", "6271", "6269", "3105", "2538", "2014", "2010", "2002", "00992A", "00946", "2317", "2347", "2356", "4510", "4540", "9907"]
MIN_AMOUNT_HUNDRED_MILLION = 1.0 

def sync_to_sheets(data_list):
    """將結果寫入 Google Sheets: 全能金流診斷報表"""
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name('google_key.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("全能金流診斷報表").get_worksheet(0)
        sheet.append_rows(data_list, value_input_option='USER_ENTERED') # 使用 USER_ENTERED 確保數值格式正確
        print(f"✅ 成功同步 {len(data_list)} 筆診斷數據至 Google Sheets")
    except Exception as e:
        print(f"⚠️ Google Sheets 同步失敗: {e}")

def get_global_stock_info():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except: return {}

STOCK_INFO_MAP = get_global_stock_info()

# ==========================================
# 2. 輔助運算工具
# ==========================================
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_tw_stock(sid):
    clean_id = str(sid).strip().upper()
    for suffix in [".TW", ".TWO"]:
        target = f"{clean_id}{suffix}"
        stock = yf.Ticker(target)
        if not stock.history(period="1d").empty: return stock, target
    return None, None

# ==========================================
# 3. 核心診斷引擎
# ==========================================
def fetch_pro_metrics(sid):
    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    try:
        df_hist = stock.history(period="7mo")
        if df_hist.empty: return None
        info = stock.info
        latest = df_hist.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        
        today_amount = (curr_vol * curr_p) / 100_000_000
        if today_amount < MIN_AMOUNT_HUNDRED_MILLION: return None

        rsi_series = calculate_rsi(df_hist['Close'])
        curr_rsi = rsi_series.iloc[-1]
        # 處理 RSI 為 NaN 的情況
        display_rsi = 0.0 if pd.isna(curr_rsi) else round(curr_rsi, 1)
        rsi_status = "⚠️過熱" if display_rsi > 75 else ("🟢穩健" if display_rsi < 35 else "中性")

        # --- 殖利率修正邏輯 ---
        # yfinance 的 dividendYield 可能是 0.05 (代表 5%)
        raw_yield = info.get('dividendYield')
        if raw_yield is None:
            dividend_yield = 0.0
        else:
            dividend_yield = float(raw_yield) * 100 # 轉為 5.5 這種格式

        this_q_m = (info.get('profitMargins', 0) or 0) * 100
        inst_own = (info.get('heldPercentInstitutions', 0) or 0) * 100
        
        d1 = ((curr_p / df_hist['Close'].iloc[-2]) - 1) * 100
        chip_status = "🔴加碼" if d1 > 0 and inst_own > 30 else "● 觀望"
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean()

        # 計分邏輯
        score = 0
        if this_q_m > 0: score += 2
        if curr_p > df_hist['Close'].iloc[0]: score += 3
        if 3.0 < dividend_yield < 15.0: score += 2
        if 40 < display_rsi < 70: score += 1
        if today_amount > 10: score += 1
        if vol_ratio > 1.5: score += 1

        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/ETF"))

        return {
            "score": score, "name": stock_name, "industry": industry,
            "id": f"{sid}{'市' if '.TW' in full_id else '櫃'}",
            "rsi": display_rsi, "rsi_s": rsi_status, 
            "yield": round(dividend_yield, 2), # 存數值，不帶 % 符號
            "chip": chip_status, "vol_r": round(vol_ratio, 1),
            "amt_t": round(today_amount, 1), "p": round(curr_p, 1), "d1": f"{d1:+.1f}%"
        }
    except: return None

# ==========================================
# 4. 主程序
# ==========================================
def main():
    current_date = datetime.date.today().strftime('%Y-%m-%d')
    results_line = []
    results_sheet = []

    print(f"🚀 開始診斷清單: {WATCH_LIST}")
    for sid in WATCH_LIST:
        res = fetch_pro_metrics(sid)
        if res:
            results_line.append(res)
            # 準備寫入 Sheet 的資料 (調整順序與格式)
            results_sheet.append([
                current_date, res['id'], res['name'], res['score'], 
                res['rsi'], res['industry'], res['chip'], res['vol_r'], 
                res['p'], res['yield'], res['amt_t'], res['d1']
            ])
        time.sleep(1) # 增加延遲避免被 yfinance 鎖定
    
    # 排序並推送 LINE
    results_line.sort(key=lambda x: x['score'], reverse=True)
    if results_line:
        msg = f"🏆 【{current_date} 金流報表】\n"
        for r in results_line:
            gem = "💎 " if r['score'] >= 8 else ""
            msg += (f"━━━━━━━━━━━━━━\n"
                    f"{gem}Score: {r['score']} | RSI: {r['rsi']}\n"
                    f"標的: {r['id']} {r['name']}\n"
                    f"現價: {r['p']} | 殖利率: {r['yield']}%\n"
                    f"金流: {r['amt_t']}億 | 量比: {r['vol_r']}\n")
        
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
        payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

    if results_sheet:
        sync_to_sheets(results_sheet)

if __name__ == "__main__":
    main()
