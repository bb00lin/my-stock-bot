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

ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Gemini Client 初始化失敗: {e}")

# 優先使用 1.5-flash (速度快、額度較高)
MODEL_CANDIDATES = ["gemini-1.5-flash", "gemini-2.0-flash-exp", "gemini-pro"]

def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    if not json_key_str: return None
    try:
        creds_dict = json.loads(json_key_str)
        return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
    except: return None

def get_global_stock_info():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except: return {}

STOCK_INFO_MAP = get_global_stock_info()

# ==========================================
# 2. 輔助數據運算 (完整保留您原本的邏輯)
# ==========================================
def get_streak_only(sid_clean):
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

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    if loss.empty or loss.iloc[-1] == 0: return pd.Series([100.0] * len(series))
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def check_ma_status(p, ma5, ma10, ma20, ma60):
    """MA 智慧偵測：完整保留您的警示邏輯"""
    alerts = []
    THRESHOLD = 0.015 
    
    # 5日線
    if ma5 > 0:
        gap_ma5 = (p - ma5) / ma5
        if 0 < gap_ma5 <= THRESHOLD: alerts.append(f"⚡回測5日線(剩{gap_ma5:.1%})")
        elif -THRESHOLD <= gap_ma5 < 0: alerts.append(f"⚠️跌破5日線({gap_ma5:.1%})")

    # 20日線
    if ma20 > 0:
        gap_ma20 = (p - ma20) / ma20
        if 0 < gap_ma20 <= THRESHOLD: alerts.append(f"🛡️回測月線(剩{gap_ma20:.1%})")
        elif -THRESHOLD <= gap_ma20 < 0: alerts.append(f"☠️跌破月線({gap_ma20:.1%})")

    # 60日線
    if ma60 > 0:
        gap_ma60 = (p - ma60) / ma60
        if abs(gap_ma60) > 0.15: alerts.append("🔥乖離過大" if gap_ma60 > 0 else "❄️嚴重超跌")

    return " | ".join(alerts) if alerts else ""

# ==========================================
# 3. 核心數據抓取 (只抓數據，不呼叫 AI)
# ==========================================
def generate_auto_analysis(r, is_hold, cost):
    # 風險評級
    if r['rsi'] >= 80: risk = "🚨極度過熱"
    elif r['rsi'] >= 70: risk = "🚩高檔警戒"
    elif 40 <= r['rsi'] <= 60 and r['d1'] > 0: risk = "✅趨勢穩健"
    elif r['rsi'] <= 30: risk = "🛡️超跌打底"
    else: risk = "正常波動"

    # 動能狀態
    trends = []
    if r['vol_r'] > 2.0 and r['d1'] > 0: trends.append("🔥主力強攻")
    elif r['vol_r'] > 1.2: trends.append("📈有效放量")
    elif r['vol_r'] < 0.7: trends.append("⚠️縮量")
    trend_status = " | ".join(trends) if trends else "動能平淡"
    
    # 綜合提示 (優先顯示 MA 警示)
    hint = ""
    profit_pct = ((r['p'] - cost) / cost * 100) if (is_hold and cost > 0) else 0
    profit_str = f"({profit_pct:+.1f}%)" if (is_hold and cost > 0) else ""

    if r['ma_alert']:
        hint = r['ma_alert']
    elif is_hold:
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
    """
    這裡完整保留您原本的數據抓取邏輯
    唯一的改變是：這裡 '不' 呼叫 AI，只回傳數據
    """
    sid = stock_data['sid']
    is_hold = stock_data['is_hold']
    cost = stock_data['cost']

    clean_id = str(sid).strip().upper()
    target_id = f"{clean_id}.TWO" if clean_id.startswith(('3','4','5','6','8')) else f"{clean_id}.TW"

    try:
        stock = yf.Ticker(target_id)
        df_hist = stock.history(period="8mo")
        if len(df_hist) < 120: return None
        
        info = stock.info
        latest = df_hist.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        today_amount = (curr_vol * curr_p) / 100_000_000
        
        rsi_series = calculate_rsi(df_hist['Close'])
        clean_rsi = 0.0 if pd.isna(rsi_series.iloc[-1]) else round(rsi_series.iloc[-1], 1)
        
        # 均線計算
        ma5 = df_hist['Close'].rolling(5).mean().iloc[-1]
        ma10 = df_hist['Close'].rolling(10).mean().iloc[-1]
        ma20 = df_hist['Close'].rolling(20).mean().iloc[-1]
        ma60 = df_hist['Close'].rolling(60).mean().iloc[-1]
        
        # 乖離率
        bias_60 = ((curr_p - ma60) / ma60) * 100
        
        # 自動偵測 MA 警示
        ma_alert_str = check_ma_status(curr_p, ma5, ma10, ma20, ma60)
        
        raw_yield = info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) - 1
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean() if df_hist['Volume'].iloc[-6:-1].mean() > 0 else 0

        # 籌碼
        fs, ss = get_streak_only(sid)
        vol_str = get_vol_status_str(vol_ratio)

        # 計分
        score = 0
        if (info.get('profitMargins', 0) or 0) > 0: score += 2
        if curr_p > df_hist['Close'].iloc[0]: score += 3
        if 0.03 < raw_yield < 0.15: score += 2
        if 40 < clean_rsi < 70: score += 1
        if today_amount > 10: score += 1
        if vol_ratio > 1.5: score += 1
        if fs >= 3 or ss >= 2: score += 1.5
        if is_hold: score += 0.5 

        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/ETF"))
        market_label = '櫃' if '.TWO' in target_id else '市'

        res = {
            "id": f"{sid}{market_label}", "name": stock_name, 
            "score": score, "rsi": clean_rsi, "industry": industry,
            "vol_r": round(vol_ratio, 1), "p": round(curr_p, 2), 
            "yield": raw_yield, "amt_t": round(today_amount, 1),
            "d1": d1, "d5": d5, "m1": m1, "m6": m6,
            "is_hold": is_hold, "cost": cost,
            "bias_str": f"{bias_60:+.1f}%", "vol_str": vol_str, "fs": fs, "ss": ss,
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2),
            "ma_alert": ma_alert_str
        }

        risk, trend, hint = generate_auto_analysis(res, is_hold, cost)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        
        # 這裡不呼叫 get_gemini_strategy，改在主程式批次呼叫
        
        return res
    except Exception as e:
        print(f"Error analyzing {sid}: {e}")
        return None

# ==========================================
# 4. 新增：批次 AI 處理 (解決 429 額度問題的核心)
# ==========================================
def get_batch_gemini_strategies(stocks_batch):
    """
    [重要] 一次處理 5 檔股票，將 5 次請求合併為 1 次
    """
    if not ai_client: return ["AI 未啟動"] * len(stocks_batch)
    
    # 組合 Prompt
    prompt = "你是專業台股操盤手。請針對以下股票，分別給出約 60 字的精簡操作建議與防守價：\n"
    for i, data in enumerate(stocks_batch):
        profit_info = f"損益:{((data['p']-data['cost'])/data['cost']*100):+.1f}%" if data['is_hold'] else "觀察"
        prompt += f"{i+1}. {data['name']}({data['id']}): 現價{data['p']}, MA5:{data['ma5']}, MA20:{data['ma20']}, RSI{data['rsi']}, 訊號:[{data['hint']}], 狀態:{profit_info}\n"

    # 嘗試呼叫模型
    for model_name in MODEL_CANDIDATES:
        try:
            response = ai_client.models.generate_content(
                model=model_name, 
                contents=prompt
            )
            # 簡單回傳：為了避免 AI 格式亂掉，我們直接把整段文字回傳給每一檔
            # (進階做法可以用 Regex 切割，但簡單做法較穩)
            result_text = response.text.replace('\n', ' ').strip()
            return [result_text] * len(stocks_batch)
        except Exception as e:
            if "429" in str(e): 
                print(f"   ⏳ {model_name} 額度滿 (429)，切換模型...")
                continue
            else:
                print(f"   ⚠️ {model_name} 錯誤: {e}")
                
    return ["❌ AI 額度暫時用盡 (429)"] * len(stocks_batch)

# ==========================================
# 5. 主程序 (重構流程：先抓數據 -> 再批次 AI)
# ==========================================
def get_watch_list_from_sheet():
    try:
        client = get_gspread_client()
        if not client: return []
        try:
            sheet = client.open("WATCH_LIST").worksheet("WATCH_LIST")
        except:
            sheet = client.open("WATCH_LIST").get_worksheet(0)
        records = sheet.get_all_records()
        watch_data = []
        for row in records:
            sid = str(row.get('股票代號', '')).strip()
            if not sid: continue
            if sid.isdigit(): sid = sid.zfill(4) if len(sid) < 4 else sid
            is_hold = str(row.get('我的庫存倉位', '')).upper() == 'Y'
            cost = row.get('平均成本', 0)
            watch_data.append({'sid': sid, 'is_hold': is_hold, 'cost': float(cost or 0)})
        return watch_data
    except Exception as e:
        print(f"❌ 讀取名單失敗: {e}")
        return []

def main():
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    watch_data_list = get_watch_list_from_sheet()
    if not watch_data_list: return

    # --- 階段一：抓取所有股票的技術數據 (不呼叫 AI) ---
    print(f"🚀 開始計算 {len(watch_data_list)} 檔股票指標...")
    all_stocks_metrics = []
    
    for stock_data in watch_data_list:
        res = fetch_pro_metrics(stock_data)
        if res:
            all_stocks_metrics.append(res)
        time.sleep(1) # 禮貌性延遲，避免 FinMind/Yahoo 封鎖

    # --- 階段二：批次進行 AI 分析 (解決 429 關鍵) ---
    final_rows = []
    batch_size = 5 # 5檔一組
    print(f"🧠 開始 AI 批次分析 (共 {len(all_stocks_metrics)} 檔，分 {len(all_stocks_metrics)//batch_size + 1} 批)...")

    for i in range(0, len(all_stocks_metrics), batch_size):
        batch = all_stocks_metrics[i : i + batch_size]
        
        # 呼叫批次 AI
        ai_responses = get_batch_gemini_strategies(batch)
        
        # 組合結果
        for stock, ai_msg in zip(batch, ai_responses):
            hold_mark = "📦庫存" if stock['is_hold'] else "👀觀察"
            final_rows.append([
                current_time, stock['id'], stock['name'], hold_mark, 
                stock['score'], stock['rsi'], stock['industry'], 
                stock['bias_str'], stock['vol_str'], stock['fs'], stock['ss'],
                stock['p'], stock['yield'], stock['amt_t'], 
                stock['d1'], stock['d5'], stock['m1'], stock['m6'],
                stock['risk'], stock['trend'], stock['hint'],
                ai_msg # 這裡填入 AI 建議
            ])
        
        print(f"   ✅ 完成第 {i//batch_size + 1} 批...")
        time.sleep(15) # [重要] 批次之間的強力冷卻，防止 429

    # --- 階段三：寫入 Google Sheet 與 LINE 推播 ---
    try:
        client = get_gspread_client()
        if client:
            sheet = client.open("全能金流診斷報表").get_worksheet(0)
            sheet.append_rows(final_rows, value_input_option='USER_ENTERED')
            print(f"✅ 成功寫入 {len(final_rows)} 筆資料")
            
            # 排序與發送 LINE
            all_stocks_metrics.sort(key=lambda x: x['score'], reverse=True)
            msg = f"📊 【{current_time} 智慧診斷】\n"
            holdings = [r for r in all_stocks_metrics if r['is_hold']]
            if holdings:
                msg += "--- 📦 庫存訊號 ---\n"
                for r in holdings:
                    msg += (f"{r['name']} ({r['p']}): {r['hint']}\n")
            
            # 加入批次 AI 的最後總結 (選第一檔代表)
            if final_rows:
                msg += "\n💡 AI 總評請見報表。"

            requests.post("https://api.line.me/v2/bot/message/push", 
                          headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}, 
                          json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]})
    except Exception as e:
        print(f"⚠️ 報表同步失敗: {e}")

if __name__ == "__main__":
    main()
