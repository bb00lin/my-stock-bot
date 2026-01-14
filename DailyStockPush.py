import os, yfinance as yf, pandas as pd, requests, time, datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 1. 配置與對照表初始化
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"
WATCH_LIST = ["6770", "6706", "6684", "6271", "6269", "3105", "2538", "2014", "2010", "2002", "00992A", "00946", "2317", "2347", "2356", "4510", "4540", "9907"]
MIN_AMOUNT_HUNDRED_MILLION = 1.0 

def sync_to_sheets(data_list):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name('google_key.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open("全能金流診斷報表").get_worksheet(0)
        sheet.append_rows(data_list, value_input_option='USER_ENTERED')
        print(f"✅ 成功同步 {len(data_list)} 筆數據與 AI 分析評語")
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
    if loss.empty or loss.iloc[-1] == 0: return pd.Series([100.0] * len(series))
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
# 3. 核心診斷引擎 (包含自動分析邏輯)
# ==========================================
def generate_auto_analysis(r):
    """仿照參考圖邏輯：生成風控評級、動向判斷、綜合提示"""
    # 1. 風控評級 (基於 RSI 與 漲跌)
    if r['rsi'] > 75: risk = "🚩 偏高 (留意止漲)"
    elif 40 <= r['rsi'] <= 60 and r['d1'] > 0: risk = "✅ 穩健 (蓄勢起漲)"
    elif r['rsi'] < 35: risk = "🛡️ 安全 (超跌反彈中)"
    else: risk = "正常"

    # 2. 動向判斷 (基於量比與金流)
    trends = []
    if r['vol_r'] > 1.8 and r['d1'] > 0: trends.append("🔥 主力介入")
    elif r['vol_r'] < 0.8 and r['d1'] > 0.01: trends.append("⚠️ 量價背離")
    if r['amt_t'] > 30: trends.append("💰 籌碼集中")
    trend_status = " | ".join(trends) if trends else "橫盤整理"

    # 3. 綜合提示 (決策建議)
    if r['score'] >= 9: hint = "⭐⭐⭐ 優先佈局：指標極強"
    elif r['yield'] > 0.05: hint = "🧧 殖利率高：防守型配置"
    elif r['m1'] > 0.1 and r['d1'] < -0.02: hint = "💡 多頭回檔：尋找支撐"
    else: hint = "持續觀察"

    return risk, trend_status, hint

def fetch_pro_metrics(sid):
    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    try:
        df_hist = stock.history(period="8mo")
        if len(df_hist) < 120: return None
        
        info = stock.info
        latest = df_hist.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        today_amount = (curr_vol * curr_p) / 100_000_000
        if today_amount < MIN_AMOUNT_HUNDRED_MILLION: return None

        # 計算指標
        rsi_series = calculate_rsi(df_hist['Close'])
        curr_rsi = rsi_series.iloc[-1]
        clean_rsi = 0.0 if pd.isna(curr_rsi) else round(curr_rsi, 1)
        
        raw_yield = info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) - 1
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean()

        # 計分邏輯
        score = 0
        if (info.get('profitMargins', 0) or 0) > 0: score += 2
        if curr_p > df_hist['Close'].iloc[0]: score += 3
        if 0.03 < raw_yield < 0.15: score += 2
        if 40 < clean_rsi < 70: score += 1
        if today_amount > 10: score += 1
        if vol_ratio > 1.5: score += 1

        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/ETF"))

        # 基礎數據包
        res = {
            "id": f"{sid}{'市' if '.TW' in full_id else '櫃'}", "name": stock_name, 
            "score": score, "rsi": clean_rsi, "industry": industry,
            "vol_r": round(vol_ratio, 1), "p": round(curr_p, 1), 
            "yield": raw_yield, "amt_t": round(today_amount, 1),
            "d1": d1, "d5": d5, "m1": m1, "m6": m6
        }

        # 生成分析並注入
        risk, trend, hint = generate_auto_analysis(res)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        return res
    except: return None

# ==========================================
# 4. 主程序
# ==========================================
def main():
    current_date = datetime.date.today().strftime('%Y-%m-%d')
    results_line, results_sheet = [], []

    for sid in WATCH_LIST:
        res = fetch_pro_metrics(sid)
        if res:
            results_line.append(res)
            # 寫入 Sheet: 包含數據與診斷 (P, Q, R 欄)
            results_sheet.append([
                current_date, res['id'], res['name'], res['score'], 
                res['rsi'], res['industry'], "🟢觀望", res['vol_r'], 
                res['p'], res['yield'], res['amt_t'], 
                res['d1'], res['d5'], res['m1'], res['m6'],
                res['risk'], res['trend'], res['hint']
            ])
        time.sleep(0.5) 
    
    # LINE 推送
    results_line.sort(key=lambda x: x['score'], reverse=True)
    if results_line:
        msg = f"📊 【{current_date} 深度診斷報告】\n"
        for r in results_line[:8]: # 僅推播分數前 8 名避免訊息太長
            msg += (f"━━━━━━━━━━━━━━\n"
                    f"● {r['id']} {r['name']} (S: {r['score']})\n"
                    f"現價: {r['p']} | 漲幅: {r['d1']*100:+.1f}%\n"
                    f"評級: {r['risk']}\n"
                    f"判斷: {r['trend']}\n"
                    f"建議: {r['hint']}\n")
        
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
        payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

    if results_sheet:
        sync_to_sheets(results_sheet)

if __name__ == "__main__":
    main()
