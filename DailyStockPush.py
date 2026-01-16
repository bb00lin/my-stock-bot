import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
import logging
# [核心升級] 使用 Google GenAI 新版 SDK
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
        # 使用新版 SDK 初始化
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Gemini Client 初始化失敗: {e}")

# 定義模型優先順序 (包含各種變體名稱以防 404)
# 程式會依序嘗試，直到成功為止
MODEL_CANDIDATES = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro",
    "gemini-1.0-pro",
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
# 2. AI 策略生成器 (智慧重試邏輯)
# ==========================================
def get_gemini_strategy(data):
    """
    逐一嘗試模型，遇到 429 則等待重試
    """
    if not ai_client: return "AI 未啟動 (Init Fail)"
    
    hold_txt = f"目前持有 (成本 {data['cost']})" if data['is_hold'] else "目前空手觀望"
    
    prompt = f"""
    角色：專業台股操盤手。
    任務：分析個股 {data['name']} ({data['id']}) 並給出約 80 字的操作建議。
    
    【技術數據】
    - 收盤：{data['p']} (漲跌幅 {data['d1']:.2%})
    - 均線支撐：5日線 {data['ma5']} | 10日線 {data['ma10']} | 20日線 {data['ma20']}
    - 指標：RSI {data['rsi']} | 量比 {data['vol_r']}x
    - 狀態：{data['risk']} | {hold_txt}

    【請模仿以下語氣撰寫】
    1. "如果明日開盤維持在 {data['p']} 以上..."
    2. "監控量能：若持續出量則..."
    3. "最佳買點：等待回測 5日線({data['ma5']}) 縮量佈局。"
    """

    # --- 核心邏輯：遍歷模型清單 ---
    for model_name in MODEL_CANDIDATES:
        try:
            # 嘗試生成
            response = ai_client.models.generate_content(
                model=model_name, 
                contents=prompt
            )
            # 成功則直接回傳
            return response.text.replace('\n', ' ').strip()

        except Exception as e:
            error_msg = str(e)
            
            # 情境 A: 忙線中 (429) -> 這是好消息，代表模型存在！
            # 策略：原地休息 20 秒，然後用同一個模型再試一次
            if "429" in error_msg:
                print(f"   ⏳ AI 忙線 (429) - 模型 {model_name} 休息 20 秒後重試...")
                time.sleep(20) 
                try:
                    response = ai_client.models.generate_content(
                        model=model_name, 
                        contents=prompt
                    )
                    return response.text.replace('\n', ' ').strip()
                except Exception as retry_e:
                    print(f"   ❌ 重試失敗: {retry_e}")
                    # 如果重試還是失敗，換下一個模型試試
                    continue
            
            # 情境 B: 找不到模型 (404) -> 換下一個名字試試
            elif "404" in error_msg or "not found" in error_msg.lower():
                # 默默跳過，嘗試下一個模型
                continue
            
            # 情境 C: 其他錯誤
            else:
                print(f"   ❌ 模型 {model_name} 發生錯誤: {error_msg[:30]}...")
                continue

    # 如果所有模型都試過了還是失敗
    return "AI 暫時無法服務 (全線忙碌)"

# ==========================================
# 3. 讀取 WATCH_LIST (含容錯與補零)
# ==========================================
def get_watch_list_from_sheet():
    try:
        client = get_gspread_client()
        try:
            # 嘗試開啟指定分頁
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
            
            # 自動補零 (946 -> 00946)
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
# 4. 技術指標運算
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
    # 智能後綴判斷
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
# 5. 核心數據抓取
# ==========================================
def generate_auto_analysis(r, is_hold, cost):
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
        else: hint = f"📦續抱觀察 {profit_str}"
    else:
        if r['score'] >= 8: hint = "🚀AI推薦關注"
        else: hint = "持續追蹤"

    return risk, trend_status, hint

def fetch_pro_metrics(stock_data):
    sid = stock_data['sid']
    is_hold = stock_data['is_hold']
    cost = stock_data['cost']

    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    try:
        df_hist = stock.history(period="6mo")
        if len(df_hist) < 60: return None
        
        latest = df_hist.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        today_amount = (curr_vol * curr_p) / 100_000_000
        
        rsi_series = calculate_rsi(df_hist['Close'])
        clean_rsi = 0.0 if pd.isna(rsi_series.iloc[-1]) else round(rsi_series.iloc[-1], 1)
        
        ma5 = df_hist['Close'].rolling(5).mean().iloc[-1]
        ma10 = df_hist['Close'].rolling(10).mean().iloc[-1]
        ma20 = df_hist['Close'].rolling(20).mean().iloc[-1]
        
        raw_yield = stock.info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) if len(df_hist) >= 121 else 0
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean()

        score = 0
        if curr_p > df_hist['Close'].iloc[0]: score += 3
        if 40 < clean_rsi < 70: score += 2
        if vol_ratio > 1.5: score += 2
        if is_hold: score += 1

        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/ETF"))
        market_label = '櫃' if '.TWO' in full_id else '市'

        res = {
            "id": f"{sid}{market_label}", "name": stock_name, 
            "score": score, "rsi": clean_rsi, "industry": industry,
            "vol_r": round(vol_ratio, 1), "p": round(curr_p, 2), 
            "yield": raw_yield, "amt_t": round(today_amount, 1),
            "d1": d1, "d5": d5, "m1": m1, "m6": m6,
            "is_hold": is_hold, "cost": cost,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2)
        }

        risk, trend, hint = generate_auto_analysis(res, is_hold, cost)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        
        # 呼叫 AI (現在會自動重試和切換模型)
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
    current_date = datetime.date.today().strftime('%Y-%m-%d')
    results_line, results_sheet = [], []

    watch_data_list = get_watch_list_from_sheet()
    
    if not watch_data_list:
        print("❌ 中止：觀察名單讀取失敗。")
        return

    # [關鍵] 將間隔拉長到 15 秒，這是免費版最安全的節奏
    print(f"🚀 開始分析 {len(watch_data_list)} 檔股票 (每檔間隔 15 秒，確保 AI 不中斷)...")

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
            
        time.sleep(15.0) 
    
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
            short_ai = r['ai_strategy'].split("。")[0]
            msg += (f"{r['name']}: {short_ai[:25]}...\n")

        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
        payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
        requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

    if results_sheet:
        sync_to_sheets(results_sheet)

if __name__ == "__main__":
    main()
