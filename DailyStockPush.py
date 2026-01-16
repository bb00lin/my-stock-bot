import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
import logging
from google import genai
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 0. 靜音設定
# ==========================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

# ==========================================
# 1. 環境與全域設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 初始化 Gemini Client
ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Gemini Client 初始化失敗: {e}")

# 模型清單 (優先順序)
MODEL_CANDIDATES = [
    "gemini-2.0-flash-exp", 
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-pro"
]

def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('google_key.json', scope)
    return gspread.authorize(creds)

def get_global_stock_info():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except: return {}

STOCK_INFO_MAP = get_global_stock_info()

# ==========================================
# 2. 輔助數據獲取 (新增籌碼與計算)
# ==========================================
def get_streak_only(sid_clean):
    """獲取外資與投信連買天數"""
    try:
        dl = DataLoader()
        start = (datetime.date.today() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        # 修正: 確保 sid 是純數字
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
    """量能狀態文字化"""
    if ratio > 1.8: return f"🔥爆量({ratio:.1f}x)"
    elif ratio > 1.2: return f"📈溫和({ratio:.1f}x)"
    elif ratio < 0.7: return f"⚠️縮量({ratio:.1f}x)"
    else: return f"☁️量平({ratio:.1f}x)"

# ==========================================
# 3. AI 策略生成器 (深度綜合評估)
# ==========================================
def get_gemini_strategy(data):
    if not ai_client: return "AI 未啟動 (Init Fail)"
    
    # 計算盈虧狀態
    profit_info = "目前無庫存，純觀察"
    if data['is_hold']:
        roi = ((data['p'] - data['cost']) / data['cost']) * 100
        profit_info = f"🔴庫存持有中 (成本:{data['cost']} | 現價:{data['p']} | 損益:{roi:+.2f}%)"

    prompt = f"""
    角色：頂尖台股操盤手。
    任務：針對個股 {data['name']} ({data['id']}) 進行全方位診斷，並給出下一步具體操作建議。
    
    【核心籌碼與技術數據】
    - 價格：{data['p']} (日漲跌 {data['d1']:.2%}) | 乖離率：{data['bias_str']}
    - 籌碼：外資連買 {data['fs']} 天 | 投信連買 {data['ss']} 天
    - 量能：{data['vol_str']}
    - 指標：RSI {data['rsi']} | 評分 {data['score']}分
    - 系統訊號：{data['risk']} | {data['hint']}
    
    【使用者資產狀態】
    - {profit_info}

    【請依照上述數據，給出約 80 字的綜合操作建議】
    1. 針對「庫存」或「觀察」身分，直接給出：續抱、加碼、減碼、止損 或 觀望、切入。
    2. 結合「損益%」與「乖離/量能」，例如：「獲利已達10%且爆量乖離過大，建議分批獲利」或「投信連買3天，拉回五日線可佈局」。
    3. 給出一個關鍵防守價位或目標價。
    """

    last_error = ""
    for model_name in MODEL_CANDIDATES:
        try:
            response = ai_client.models.generate_content(
                model=model_name, 
                contents=prompt
            )
            return response.text.replace('\n', ' ').strip()
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg:
                print(f"   ⏳ {model_name} 額度已滿 (429)，停止嘗試。")
                return "❌ 今日額度用盡 (429)"
            elif "404" in error_msg:
                last_error = f"404 ({model_name})"
                continue
            else:
                last_error = f"Err: {error_msg[:10]}"
                continue
    return f"❌ AI 失敗: {last_error}"

# ==========================================
# 4. 讀取 WATCH_LIST
# ==========================================
def get_watch_list_from_sheet():
    try:
        client = get_gspread_client()
        try:
            sheet = client.open("WATCH_LIST").worksheet("WATCH_LIST")
        except:
            print("⚠️ 找不到 'WATCH_LIST' 分頁，自動切換讀取『第一個分頁』...")
            sheet = client.open("WATCH_LIST").get_worksheet(0)
            
        records = sheet.get_all_records()
        watch_data = []
        print(f"📋 正在讀取雲端觀察名單，共 {len(records)} 筆...")
        
        for row in records:
            raw_sid = str(row.get('股票代號', '')).strip()
            if not raw_sid: continue
            
            if raw_sid.isdigit():
                if len(raw_sid) == 3: sid = "00" + raw_sid
                elif len(raw_sid) < 4: sid = raw_sid.zfill(4)
                else: sid = raw_sid
            else:
                sid = raw_sid
            
            is_hold = str(row.get('我的庫存倉位', '')).strip().upper() == 'Y'
            cost = row.get('平均成本', 0)
            if cost == '': cost = 0
            
            watch_data.append({'sid': sid, 'is_hold': is_hold, 'cost': float(cost)})
        return watch_data
    except Exception as e:
        print(f"❌ 讀取 WATCH_LIST 失敗: {e}")
        return []

# ==========================================
# 5. 技術指標運算
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
    if clean_id.startswith(('3', '4', '5', '6', '8')):
        suffixes = [".TWO", ".TW"]
    else:
        suffixes = [".TW", ".TWO"]
        
    for suffix in suffixes:
        target = f"{clean_id}{suffix}"
        try:
            stock = yf.Ticker(target)
            if not stock.history(period="1d").empty: return stock, target
        except: continue
    return None, None

# ==========================================
# 6. 核心數據抓取與計算
# ==========================================
def generate_auto_analysis(r, is_hold, cost):
    # 邏輯判斷保持不變，直接沿用
    if r['rsi'] >= 80: risk = "🚨極度過熱"
    elif r['rsi'] >= 70: risk = "🚩高檔警戒"
    elif 40 <= r['rsi'] <= 60 and r['d1'] > 0: risk = "✅趨勢穩健"
    elif r['rsi'] <= 30: risk = "🛡️超跌打底"
    else: risk = "正常波動"

    trends = []
    if r['vol_r'] > 2.0 and r['d1'] > 0: trends.append("🔥主力強攻")
    elif r['vol_r'] > 1.2: trends.append("📈有效放量")
    elif r['vol_r'] < 0.7: trends.append("⚠️縮量")
    trend_status = " | ".join(trends) if trends else "動能平淡"
    
    hint = ""
    profit_pct = ((r['p'] - cost) / cost * 100) if (is_hold and cost > 0) else 0
    profit_str = f"({profit_pct:+.1f}%)" if (is_hold and cost > 0) else ""

    if is_hold:
        if r['rsi'] >= 80: hint = f"❗分批止盈 {profit_str}"
        elif r['d1'] <= -0.04: hint = f"📢急跌守5日線 {profit_str}"
        elif r['rsi'] < 45 and r['d5'] < -0.05: hint = f"🛑停損審視 {profit_str}"
        elif r['m6'] > 0.1 and r['d1'] > -0.02: hint = f"💎波段續抱 {profit_str}"
        else: hint = f"📦持股觀察 {profit_str}"
    else:
        if r['score'] >= 9: hint = "⭐⭐優先佈局"
        elif r['score'] >= 8 and r['vol_r'] > 1.5: hint = "🚀放量轉強"
        elif r['rsi'] <= 30 and r['d1'] > 0: hint = "💡跌深反彈"
        elif r['rsi'] >= 75: hint = "🚫高位禁追"
        elif r['m1'] > 0.1 and r['d1'] < -0.02: hint = "📉拉回找撐"
        else: hint = "持續追蹤"

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
        
        rsi_series = calculate_rsi(df_hist['Close'])
        clean_rsi = 0.0 if pd.isna(rsi_series.iloc[-1]) else round(rsi_series.iloc[-1], 1)
        
        # 均線與乖離率計算
        ma5 = df_hist['Close'].rolling(5).mean().iloc[-1]
        ma20 = df_hist['Close'].rolling(20).mean().iloc[-1]
        ma60 = df_hist['Close'].rolling(60).mean().iloc[-1]
        
        # 乖離率 (以季線為基準，可代表波段位階)
        bias_60 = ((curr_p - ma60) / ma60) * 100
        
        raw_yield = info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) - 1
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean() if df_hist['Volume'].iloc[-6:-1].mean() > 0 else 0

        # 新增籌碼與量能狀態
        pure_id = ''.join(filter(str.isdigit, sid))
        fs, ss = get_streak_only(pure_id) # 外資/投信連買
        vol_str = get_vol_status_str(vol_ratio)

        score = 0
        if (info.get('profitMargins', 0) or 0) > 0: score += 2
        if curr_p > df_hist['Close'].iloc[0]: score += 3
        if 0.03 < raw_yield < 0.15: score += 2
        if 40 < clean_rsi < 70: score += 1
        if today_amount > 10: score += 1
        if vol_ratio > 1.5: score += 1
        if fs >= 3 or ss >= 2: score += 1.5 # 籌碼加分
        if is_hold: score += 0.5 

        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/ETF"))
        market_label = '櫃' if '.TWO' in full_id else '市'

        res = {
            "id": f"{sid}{market_label}", "name": stock_name, 
            "score": score, "rsi": clean_rsi, "industry": industry,
            "vol_r": round(vol_ratio, 1), "p": round(curr_p, 2), 
            "yield": raw_yield, "amt_t": round(today_amount, 1),
            "d1": d1, "d5": d5, "m1": m1, "m6": m6,
            "is_hold": is_hold, "cost": cost,
            # 新增欄位數據
            "bias_str": f"{bias_60:+.1f}%",
            "vol_str": vol_str,
            "fs": fs, "ss": ss,
            # 輔助AI用
            "ma5": round(ma5, 2), "ma10": round(ma20, 2), "ma20": round(ma20, 2)
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

def main():
    # [更新] 時間格式顯示到分鐘
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    results_line, results_sheet = [], []

    watch_data_list = get_watch_list_from_sheet()
    
    if not watch_data_list:
        print("❌ 中止：觀察名單讀取失敗。")
        return

    print(f"🚀 開始分析 {len(watch_data_list)} 檔股票 (每檔間隔 15 秒)...")

    for stock_data in watch_data_list:
        res = fetch_pro_metrics(stock_data)
        if res:
            results_line.append(res)
            
            hold_mark = "📦庫存" if res['is_hold'] else "👀觀察"
            
            # [更新] Sheet 欄位順序調整 (移除籌碼，加入乖離/量能/法人)
            # 對應: 時間, 代號, 名稱, 庫存, 評分, RSI, 產業, 乖離, 量能, 外資, 投信, 現價...
            results_sheet.append([
                current_time, res['id'], res['name'], hold_mark, 
                res['score'], res['rsi'], res['industry'], 
                res['bias_str'], res['vol_str'], res['fs'], res['ss'], # 新增的4個欄位
                res['p'], res['yield'], res['amt_t'], 
                res['d1'], res['d5'], res['m1'], res['m6'],
                res['risk'], res['trend'], res['hint'],
                res['ai_strategy']
            ])
            
        time.sleep(15.0) 
    
    results_line.sort(key=lambda x: x['score'], reverse=True)
    if results_line:
        msg = f"📊 【{current_time} 庫存與 AI 診斷】\n"
        
        holdings = [r for r in results_line if r['is_hold']]
        if holdings:
            msg += "--- 📦 我的庫存 ---\n"
            for r in holdings:
                msg += (f"{r['name']} ({r['p']}): {r['hint']}\n")
        
        msg += "\n--- 🚀 重點關注 ---\n"
        others = [r for r in results_line if not r['is_hold']][:5]
        for r in others:
            short_ai = r['ai_strategy'].split("。")[0]
            msg += (f"{r['name']}: {short_ai[:25]}...\n")

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
        payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

    if results_sheet:
        sync_to_sheets(results_sheet)

if __name__ == "__main__":
    main()
