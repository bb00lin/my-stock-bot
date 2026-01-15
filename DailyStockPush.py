import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 1. 環境與全域設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 初始化 Gemini AI
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        ai_model = genai.GenerativeModel('gemini-1.5-flash')
    except Exception as e:
        print(f"Gemini 初始化警告: {e}")

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
# 2. AI 策略生成器 (錯誤處理優化)
# ==========================================
def get_gemini_strategy(data):
    """
    根據股票數據，生成具體的操作策略
    """
    if not GEMINI_API_KEY: return "AI 未啟動 (缺 Key)"
    
    hold_status = f"持有 (成本 {data['cost']})" if data['is_hold'] else "觀望中"
    
    prompt = f"""
    角色：專業台股操盤手。
    任務：分析個股 {data['name']} ({data['id']}) 並給出約 60 字的操作策略。
    數據：現價{data['p']} | 漲跌{data['d1']:.2%} | RSI {data['rsi']} | 量比 {data['vol_r']}x
    均線：5MA {data['ma5']} | 10MA {data['ma10']} | 20MA {data['ma20']}
    狀態：{data['risk']} | {hold_status}
    
    請給出：
    1. 關鍵價位 (如回測5MA或停損價)。
    2. 明日看盤重點。
    3. 簡評。
    """
    try:
        # 增加 timeout 設定避免卡死
        response = ai_model.generate_content(prompt, request_options={"timeout": 30})
        return response.text.replace('\n', ' ').strip()
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            return "AI 額度滿載 (429)"
        return f"AI 分析失敗: {error_msg[:20]}..."

# ==========================================
# 3. 讀取 WATCH_LIST (新增補零邏輯)
# ==========================================
def get_watch_list_from_sheet():
    try:
        client = get_gspread_client()
        try:
            sheet = client.open("WATCH_LIST").worksheet("WATCH_LIST")
        except:
            sheet = client.open("WATCH_LIST").get_worksheet(0)
            
        records = sheet.get_all_records()
        watch_data = []
        print(f"📋 正在讀取雲端觀察名單，共 {len(records)} 筆...")
        
        for row in records:
            raw_sid = str(row.get('股票代號', '')).strip()
            if not raw_sid: continue
            
            # --- 修正：處理 ETF 代碼 (如 946 -> 00946) ---
            # 如果是數字且長度為 3 (如 946)，自動補兩個 0
            if raw_sid.isdigit() and len(raw_sid) == 3:
                sid = "00" + raw_sid
            # 如果是數字且長度為 4 (如 940 -> 00940? 不太可能，通常是 00940 被轉成 940)
            # 這裡假設標準長度至少 4 碼，若不足 4 碼且是數字，則補 0
            elif raw_sid.isdigit() and len(raw_sid) < 4:
                sid = raw_sid.zfill(4) 
            else:
                sid = raw_sid
            # ---------------------------------------------
            
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
        return []

# ==========================================
# 4. 輔助運算工具
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
        # 嘗試抓取，若無數據則換下一個後綴
        try:
            hist = stock.history(period="1d")
            if not hist.empty: return stock, target
        except: pass
    return None, None

# ==========================================
# 5. 核心診斷引擎
# ==========================================
def generate_auto_analysis(r, is_hold, cost):
    # 風控評級
    if r['rsi'] >= 80: risk = "🚨極度過熱"
    elif r['rsi'] >= 70: risk = "🚩高檔警戒"
    elif 40 <= r['rsi'] <= 60 and r['d1'] > 0: risk = "✅趨勢穩健"
    elif r['rsi'] <= 30: risk = "🛡️超跌打底"
    else: risk = "正常波動"

    # 動向判斷
    trends = []
    if r['vol_r'] > 2.0 and r['d1'] > 0: trends.append("🔥主力強攻")
    elif r['vol_r'] > 1.2 and r['d1'] > 0: trends.append("📈有效放量")
    elif r['vol_r'] < 0.7 and r['d1'] > 0.01: trends.append("⚠️縮量背離")
    if r['amt_t'] > 30: trends.append("💰熱錢中心")
    trend_status = " | ".join(trends) if trends else "動能平淡"

    # 綜合提示
    hint = ""
    profit_pct = ((r['p'] - cost) / cost * 100) if (is_hold and cost > 0) else 0
    profit_str = f"({profit_pct:+.1f}%)" if (is_hold and cost > 0) else ""

    if is_hold:
        if r['rsi'] >= 80: hint = f"❗分批止盈 {profit_str}"
        elif r['d1'] <= -0.04: hint = f"📢急跌守5日線 {profit_str}"
        elif r['rsi'] < 45 and r['d5'] < -0.05: hint = f"🛑停損審視 {profit_str}"
        else: hint = f"📦持股觀察 {profit_str}"
    else:
        if r['score'] >= 9: hint = "⭐⭐優先佈局"
        elif r['score'] >= 8 and r['vol_r'] > 1.5: hint = "🚀放量轉強"
        elif r['rsi'] <= 30: hint = "💡跌深反彈"
        else: hint = "持續追蹤"

    return risk, trend_status, hint

def fetch_pro_metrics(stock_data):
    sid = stock_data['sid']
    is_hold = stock_data['is_hold']
    cost = stock_data['cost']

    stock, full_id = get_tw_stock(sid)
    if not stock: 
        print(f"⚠️ 找不到股票數據: {sid}")
        return None
    try:
        df_hist = stock.history(period="6mo")
        if len(df_hist) < 60: return None
        
        info = stock.info
        latest = df_hist.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        today_amount = (curr_vol * curr_p) / 100_000_000
        
        rsi_series = calculate_rsi(df_hist['Close'])
        clean_rsi = 0.0 if pd.isna(rsi_series.iloc[-1]) else round(rsi_series.iloc[-1], 1)
        
        ma5 = df_hist['Close'].rolling(5).mean().iloc[-1]
        ma10 = df_hist['Close'].rolling(10).mean().iloc[-1]
        ma20 = df_hist['Close'].rolling(20).mean().iloc[-1]
        
        raw_yield = info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) if len(df_hist) >= 121 else 0
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean()

        score = 0
        if (info.get('profitMargins', 0) or 0) > 0: score += 2
        if curr_p > df_hist['Close'].iloc[0]: score += 3
        if 0.03 < raw_yield < 0.15: score += 2
        if 40 < clean_rsi < 70: score += 1
        if today_amount > 10: score += 1
        if vol_ratio > 1.5: score += 1
        if is_hold: score += 0.5 

        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/ETF"))

        res = {
            "id": f"{sid}{'市' if '.TW' in full_id else '櫃'}", "name": stock_name, 
            "score": score, "rsi": clean_rsi, "industry": industry,
            "vol_r": round(vol_ratio, 1), "p": round(curr_p, 2), 
            "yield": raw_yield, "amt_t": round(today_amount, 1),
            "d1": d1, "d5": d5, "m1": m1, "m6": m6,
            "is_hold": is_hold, "cost": cost,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2)
        }

        risk, trend, hint = generate_auto_analysis(res, is_hold, cost)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        
        # 呼叫 AI
        ai_strategy = get_gemini_strategy(res)
        res['ai_strategy'] = ai_strategy
        
        return res
    except Exception as e:
        print(f"Error analyzing {sid}: {e}")
        return None

def sync_to_sheets(data_list):
    try:
        client = get_gspread_client()
        sheet = client.open("全能金流診斷報表").get_worksheet(0)
        sheet.append_rows(data_list, value_input_option='USER_ENTERED')
        print(f"✅ 成功同步 {len(data_list)} 筆數據與 AI 分析")
    except Exception as e:
        print(f"⚠️ Google Sheets 同步失敗: {e}")

# ==========================================
# 6. 主程序
# ==========================================
def main():
    current_date = datetime.date.today().strftime('%Y-%m-%d')
    results_line, results_sheet = [], []

    watch_data_list = get_watch_list_from_sheet()
    
    if not watch_data_list:
        print("⚠️ 無法讀取觀察名單，請檢查 Google Sheet 設定。")
        return

    print(f"🚀 開始分析 {len(watch_data_list)} 檔股票 (每檔間隔 5 秒以保護 AI 額度)...")

    for stock_data in watch_data_list:
        res = fetch_pro_metrics(stock_data)
        if res:
            results_line.append(res)
            
            hold_mark = "📦庫存" if res['is_hold'] else "👀觀察"
            
            results_sheet.append([
                current_date, res['id'], res['name'], hold_mark, 
                res['score'], res['rsi'], res['industry'], 
                "🟢觀望", res['vol_r'], res['p'], res['yield'], res['amt_t'], 
                res['d1'], res['d5'], res['m1'], res['m6'],
                res['risk'], res['trend'], res['hint'],
                res['ai_strategy']
            ])
            
        # [重要] 延長等待時間至 5 秒，確保符合 Gemini Free Tier (15 RPM)
        # 43 檔股票 x 5 秒 = 約 3.5 分鐘跑完，這是最安全的做法
        time.sleep(5.0) 
    
    # LINE 推送
    results_line.sort(key=lambda x: x['score'], reverse=True)
    if results_line:
        msg = f"📊 【{current_date} 庫存與 AI 診斷】\n"
        
        holdings = [r for r in results_line if r['is_hold']]
        if holdings:
            msg += "--- 📦 我的庫存 ---\n"
            for r in holdings:
                msg += (f"{r['name']} ({r['p']}): {r['hint']}\n")
        
        msg += "\n--- 🚀 重點關注 ---\n"
        others = [r for r in results_line if not r['is_hold']][:5]
        for r in others:
            ai_short = r['ai_strategy'].replace("AI 額度滿載", "AI忙線").split("。")[0]
            msg += (f"{r['name']} (S:{r['score']}): {ai_short[:20]}...\n")

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
        payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

    # 同步回 Sheet
    if results_sheet:
        sync_to_sheets(results_sheet)

if __name__ == "__main__":
    main()
