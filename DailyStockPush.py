import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
import logging
import json
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

# 模型優先順序
MODEL_CANDIDATES = [
    "gemini-2.0-flash-exp", 
    "gemini-1.5-flash",
    "gemini-pro"
]

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

# ==========================================
# 2. 輔助運算工具 (乖離、量能、法人)
# ==========================================
def get_streak_only(sid_clean):
    """獲取外資與投信連買天數"""
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

def check_ma_status(p, ma5, ma20, ma60):
    alerts = []
    THRESHOLD = 0.015 # 1.5% 判定
    if ma5 > 0:
        gap = (p - ma5) / ma5
        if 0 < gap <= THRESHOLD: alerts.append(f"⚡回測5日線(剩{gap:.1%})")
        elif -THRESHOLD <= gap < 0: alerts.append(f"⚠️跌破5日線({gap:.1%})")
    if ma20 > 0:
        gap = (p - ma20) / ma20
        if 0 < gap <= THRESHOLD: alerts.append(f"🛡️回測月線(剩{gap:.1%})")
        elif -THRESHOLD <= gap < 0: alerts.append(f"☠️跌破月線({gap:.1%})")
    if ma60 > 0:
        gap = (p - ma60) / ma60
        if abs(gap) > 0.15: alerts.append("🔥乖離過大" if gap > 0 else "❄️嚴重超跌")
    return " | ".join(alerts) if alerts else ""

# ==========================================
# 3. AI 策略生成器
# ==========================================
def get_gemini_strategy(data, mode="single"):
    """
    mode="single": 單一股票分析
    mode="summary": 投資組合總結
    """
    if not ai_client: return "AI 未啟動"
    
    if mode == "single":
        profit_info = f"損益:{(((data['p'] - data['cost']) / data['cost']) * 100):+.2f}%" if data['is_hold'] else "純觀察"
        prompt = f"""
        你是台股操盤大師。分析 {data['name']} ({data['id']})。
        關鍵均線：MA5:{data['ma5']}, MA20:{data['ma20']}。訊號：{data['ma_alert']}
        數據：價格 {data['p']}, 乖離 {data['bias_str']}, 法人連買:外{data['fs']}/投{data['ss']}, 量能 {data['vol_str']}
        狀態：{profit_info}
        任務：請給出 80 字的操作指令(續抱/加碼/觀望等)，並明確指出防守價位。
        """
    else: # Portfolio Summary
        prompt = f"你是投資長。以下是今日所有標的狀況：{data}。請根據庫存整體損益與盤勢警訊，給出 150 字的全局操作戰略建議。"

    for model_name in MODEL_CANDIDATES:
        try:
            response = ai_client.models.generate_content(model=model_name, contents=prompt)
            return response.text.replace('\n', ' ').strip()
        except: continue
    return "❌ AI 服務忙碌中"

# ==========================================
# 4. 核心邏輯整合
# ==========================================
def generate_auto_analysis(r, is_hold, cost):
    # RSI 風控
    if r['rsi'] >= 80: risk = "🚨極度過熱"
    elif r['rsi'] >= 70: risk = "🚩高檔警戒"
    elif 40 <= r['rsi'] <= 60 and r['d1'] > 0: risk = "✅趨勢穩健"
    elif r['rsi'] <= 30: risk = "🛡️超跌打底"
    else: risk = "正常波動"

    trends = ["🔥主力強攻" if r['vol_r'] > 2.0 and r['d1'] > 0 else "📈有效放量" if r['vol_r'] > 1.2 else "動能平淡"]
    trend_status = " | ".join(trends)

    # 綜合提示 (優先顯示 MA 警示)
    hint = r['ma_alert'] if r['ma_alert'] else ("📦持股觀察" if is_hold else "持續追蹤")
    return risk, trend_status, hint

def fetch_pro_metrics(stock_data):
    sid = stock_data['sid']
    is_hold, cost = stock_data['is_hold'], stock_data['cost']
    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    try:
        df = stock.history(period="8mo")
        if len(df) < 120: return None
        latest = df.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        rsi_series = calculate_rsi(df['Close'])
        ma5, ma20, ma60 = df['Close'].rolling(5).mean().iloc[-1], df['Close'].rolling(20).mean().iloc[-1], df['Close'].rolling(60).mean().iloc[-1]
        
        fs, ss = get_streak_only(sid)
        vol_r = curr_vol / df['Volume'].iloc[-6:-1].mean() if df['Volume'].iloc[-6:-1].mean() > 0 else 0
        bias_60 = ((curr_p - ma60) / ma60) * 100
        
        # 計分
        score = 0
        if curr_p > df['Close'].iloc[0]: score += 3
        if 40 < rsi_series.iloc[-1] < 70: score += 2
        if fs >= 3 or ss >= 2: score += 2
        if vol_r > 1.5: score += 1

        name, ind = STOCK_INFO_MAP.get(str(sid), (sid, "其他"))
        res = {
            "id": f"{sid}{('櫃' if '.TWO' in full_id else '市')}", "name": name, "score": score, 
            "rsi": round(rsi_series.iloc[-1], 1), "industry": ind, "bias_str": f"{bias_60:+.1f}%",
            "vol_str": get_vol_status_str(vol_r), "fs": fs, "ss": ss, "p": round(curr_p, 2),
            "yield": stock.info.get('dividendYield', 0) or 0, "amt_t": round((curr_vol * curr_p)/100_000_000, 1),
            "d1": (curr_p / df['Close'].iloc[-2]) - 1, "d5": (curr_p / df['Close'].iloc[-6]) - 1,
            "m1": (curr_p / df['Close'].iloc[-21]) - 1, "m6": (curr_p / df['Close'].iloc[-121]) - 1,
            "is_hold": is_hold, "cost": cost, "vol_r": vol_r,
            "ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2),
            "ma_alert": check_ma_status(curr_p, ma5, ma20, ma60)
        }
        risk, trend, hint = generate_auto_analysis(res, is_hold, cost)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        res['ai_strategy'] = get_gemini_strategy(res, mode="single")
        return res
    except: return None

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_tw_stock(sid):
    clean_id = str(sid).strip().upper()
    for suffix in [".TW", ".TWO"]:
        t = f"{clean_id}{suffix}"
        try:
            s = yf.Ticker(t)
            if not s.history(period="1d").empty: return s, t
        except: continue
    return None, None

# ==========================================
# 5. 主程序
# ==========================================
def main():
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    watch_data = get_watch_list_from_sheet()
    if not watch_data: return
    
    results, sheet_rows = [], []
    print(f"🚀 開始分析 {len(watch_data)} 檔股票...")

    for stock in watch_data:
        res = fetch_pro_metrics(stock)
        if res:
            results.append(res)
            sheet_rows.append([
                current_time, res['id'], res['name'], ("📦庫存" if res['is_hold'] else "👀觀察"),
                res['score'], res['rsi'], res['industry'], res['bias_str'], res['vol_str'],
                res['fs'], res['ss'], res['p'], res['yield'], res['amt_t'],
                res['d1'], res['d5'], res['m1'], res['m6'],
                res['risk'], res['trend'], res['hint'], res['ai_strategy']
            ])
        time.sleep(15.0)

    # 執行最後的「投資組合綜合評估」
    if results:
        portfolio_brief = "\n".join([f"{r['name']}: {r['ai_strategy'][:30]}..." for r in results])
        final_summary = get_gemini_strategy(portfolio_brief, mode="summary")
        # 寫入最後一列：全組合大師建議
        sheet_rows.append([current_time, "Portfolio", "綜合評估", "ALL", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "綜合診斷", "盤勢建議", "最後總結", final_summary])

    client = get_gspread_client()
    if client:
        sheet = client.open("全能金流診斷報表").get_worksheet(0)
        sheet.append_rows(sheet_rows, value_input_option='USER_ENTERED')
        print(f"✅ 成功同步 {len(sheet_rows)} 筆數據")

if __name__ == "__main__":
    main()
