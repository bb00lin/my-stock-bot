import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 1. 環境與全域設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"
MIN_AMOUNT_HUNDRED_MILLION = 1.0 

# 全域 Google Sheet 連線物件
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('google_key.json', scope)
    return gspread.authorize(creds)

# 獲取全台股名稱對照表
def get_global_stock_info():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except: return {}

STOCK_INFO_MAP = get_global_stock_info()

# ==========================================
# 2. 讀取 WATCH_LIST (新功能)
# ==========================================
def get_watch_list_from_sheet():
    """從 Google Sheet 'WATCH_LIST' 讀取觀察名單與庫存狀態"""
    try:
        client = get_gspread_client()
        # 嘗試開啟名為 WATCH_LIST 的工作表，若無則開啟第一個
        try:
            sheet = client.open("WATCH_LIST").worksheet("WATCH_LIST")
        except:
            # 相容性：若找不到特定 tab，嘗試找檔名為 WATCH_LIST 的第一個 tab
            sheet = client.open("WATCH_LIST").get_worksheet(0)
            
        records = sheet.get_all_records() # 讀取所有資料為字典列表
        
        watch_data = []
        print(f"📋 正在讀取雲端觀察名單，共 {len(records)} 筆...")
        
        for row in records:
            sid = str(row.get('股票代號', '')).strip()
            if not sid: continue # 跳過空行
            
            is_hold = str(row.get('我的庫存倉位', '')).strip().upper() == 'Y'
            cost = row.get('平均成本', 0)
            if cost == '': cost = 0
            
            watch_data.append({
                'sid': sid,
                'is_hold': is_hold,
                'cost': float(cost)
            })
            
        return watch_data
    except Exception as e:
        print(f"❌ 讀取 WATCH_LIST 失敗: {e}")
        # 備援：如果讀取失敗，回傳一個空的或預設的，避免程式崩潰
        return []

# ==========================================
# 3. 輔助運算工具
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
# 4. 核心診斷引擎 (結合庫存邏輯)
# ==========================================
def generate_auto_analysis(r, is_hold, cost):
    """
    根據數據與庫存狀態生成評語
    r: 技術指標數據
    is_hold: 是否為庫存 (True/False)
    cost: 平均成本
    """
    # 1. 風控評級
    if r['rsi'] > 75: risk = "🚩高檔過熱"
    elif 40 <= r['rsi'] <= 60 and r['d1'] > 0: risk = "✅穩健起漲"
    elif r['rsi'] < 30: risk = "🛡️超跌區"
    else: risk = "正常波動"

    # 2. 動向判斷
    trends = []
    if r['vol_r'] > 1.8 and r['d1'] > 0: trends.append("🔥主力攻擊")
    elif r['vol_r'] < 0.7 and r['d1'] > 0.01: trends.append("⚠️量價背離")
    if r['amt_t'] > 30: trends.append("💰金流集中")
    trend_status = " | ".join(trends) if trends else "橫盤整理"

    # 3. 操作建議 (結合庫存狀態)
    hint = "持續觀察"
    
    # 庫存股的建議邏輯
    if is_hold:
        profit_pct = ((r['p'] - cost) / cost * 100) if cost > 0 else 0
        profit_str = f"(帳面{profit_pct:+.1f}%)" if cost > 0 else ""
        
        if r['rsi'] > 80: hint = f"🚨獲利了結? {profit_str}"
        elif r['d1'] < -0.03 and r['m1'] < 0: hint = f"🛑停損審視 {profit_str}"
        elif r['m6'] > 0.1: hint = f"💎續抱待漲 {profit_str}"
        else: hint = f"📦庫存持股 {profit_str}"
    
    # 非庫存股 (觀察名單) 的建議邏輯
    else:
        if r['score'] >= 9: hint = "⭐⭐優先佈局"
        elif r['yield'] > 0.05 and r['m6'] > 0: hint = "🧧存股首選"
        elif r['rsi'] < 30 and r['d1'] > 0: hint = "💡抄底機會"
        elif r['m1'] > 0.1 and r['d1'] < -0.02: hint = "📉拉回找點"

    return risk, trend_status, hint

def fetch_pro_metrics(stock_data):
    sid = stock_data['sid']
    is_hold = stock_data['is_hold']
    cost = stock_data['cost']

    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    try:
        df_hist = stock.history(period="8mo")
        if len(df_hist) < 120: return None
        
        info = stock.info
        latest = df_hist.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        today_amount = (curr_vol * curr_p) / 100_000_000
        
        # 移除金額過小的過濾，因為這可能是您的庫存，即使量縮也要看
        # if today_amount < MIN_AMOUNT_HUNDRED_MILLION: return None

        # 指標計算
        rsi_series = calculate_rsi(df_hist['Close'])
        clean_rsi = 0.0 if pd.isna(rsi_series.iloc[-1]) else round(rsi_series.iloc[-1], 1)
        
        raw_yield = info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) - 1
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean()

        # 計分
        score = 0
        if (info.get('profitMargins', 0) or 0) > 0: score += 2
        if curr_p > df_hist['Close'].iloc[0]: score += 3
        if 0.03 < raw_yield < 0.15: score += 2
        if 40 < clean_rsi < 70: score += 1
        if today_amount > 10: score += 1
        if vol_ratio > 1.5: score += 1
        
        # 庫存股加分 (讓它排在前面)
        if is_hold: score += 0.5 

        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/ETF"))

        res = {
            "id": f"{sid}{'市' if '.TW' in full_id else '櫃'}", "name": stock_name, 
            "score": score, "rsi": clean_rsi, "industry": industry,
            "vol_r": round(vol_ratio, 1), "p": round(curr_p, 1), 
            "yield": raw_yield, "amt_t": round(today_amount, 1),
            "d1": d1, "d5": d5, "m1": m1, "m6": m6,
            "is_hold": is_hold # 標記是否為庫存
        }

        # 生成 AI 分析 (傳入庫存狀態與成本)
        risk, trend, hint = generate_auto_analysis(res, is_hold, cost)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        return res
    except Exception as e:
        print(f"Error analyzing {sid}: {e}")
        return None

def sync_to_sheets(data_list):
    try:
        client = get_gspread_client()
        sheet = client.open("全能金流診斷報表").get_worksheet(0)
        # 清空舊資料 (保留標題列)
        # sheet.clear() # 若想保留歷史紀錄可註解掉這行，但建議每日更新清空舊的比較乾淨
        # 這裡我們只 Append，若要覆蓋可改用 update
        sheet.append_rows(data_list, value_input_option='USER_ENTERED')
        print(f"✅ 成功同步 {len(data_list)} 筆數據與分析")
    except Exception as e:
        print(f"⚠️ Google Sheets 同步失敗: {e}")

# ==========================================
# 5. 主程序
# ==========================================
def main():
    current_date = datetime.date.today().strftime('%Y-%m-%d')
    results_line, results_sheet = [], []

    # 1. 從 Google Sheet 讀取清單
    watch_data_list = get_watch_list_from_sheet()
    
    if not watch_data_list:
        print("⚠️ 無法讀取觀察名單，請檢查 Google Sheet 設定。")
        return

    # 2. 逐一分析
    for stock_data in watch_data_list:
        res = fetch_pro_metrics(stock_data)
        if res:
            results_line.append(res)
            
            # 庫存標記 (在報表中增加一欄識別)
            hold_mark = "📦庫存" if res['is_hold'] else "👀觀察"
            
            results_sheet.append([
                current_date, res['id'], res['name'], hold_mark, # 新增庫存欄位
                res['score'], res['rsi'], res['industry'], 
                "🟢觀望", res['vol_r'], res['p'], res['yield'], res['amt_t'], 
                res['d1'], res['d5'], res['m1'], res['m6'],
                res['risk'], res['trend'], res['hint']
            ])
        time.sleep(0.5) # 避免 API 速率限制
    
    # 3. LINE 推送
    results_line.sort(key=lambda x: x['score'], reverse=True)
    if results_line:
        msg = f"📊 【{current_date} 庫存與觀察診斷】\n"
        
        # 先推播庫存股
        holdings = [r for r in results_line if r['is_hold']]
        if holdings:
            msg += "--- 📦 我的庫存 ---\n"
            for r in holdings:
                msg += (f"{r['name']}({r['p']}): {r['hint']}\n")
        
        msg += "\n--- 👀 重點觀察 ---\n"
        others = [r for r in results_line if not r['is_hold']][:5] # 取前5名
        for r in others:
            msg += (f"{r['name']}(S:{r['score']}): {r['hint']}\n")

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
        payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

    # 4. 同步回 Sheet
    if results_sheet:
        sync_to_sheets(results_sheet)

if __name__ == "__main__":
    main()
