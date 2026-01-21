import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
import logging
import json
from google import genai
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 0. 靜音設定與全域變數
# ==========================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 初始化 Gemini Client
ai_client = None
if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        print("✅ Gemini AI Client 初始化成功")
    except Exception as e:
        print(f"❌ Gemini Client 初始化失敗: {e}")

# 模型清單 (優先順序：免費且快 -> 強大但慢 -> 舊版)
MODEL_CANDIDATES = [
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-2.0-flash-exp",
    "gemini-pro"
]

# ==========================================
# 1. Google Sheets 連線與資料獲取
# ==========================================
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 環境變數！")
        return None

    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ 解析金鑰或連線失敗: {e}")
        return None

def get_global_stock_info():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except: return {}

STOCK_INFO_MAP = get_global_stock_info()

# ==========================================
# 2. 輔助數據獲取 (FinMind & yfinance)
# ==========================================
def get_streak_only(sid_clean):
    """獲取外資與投信連買天數"""
    try:
        dl = DataLoader()
        start = (datetime.date.today() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        clean_id = ''.join(filter(str.isdigit, str(sid_clean)))
        # FinMind 會印出 download log，這是正常的
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

def check_ma_status(p, ma5, ma10, ma20, ma60):
    alerts = []
    THRESHOLD = 0.015 
    
    if ma5 > 0:
        gap_ma5 = (p - ma5) / ma5
        if 0 < gap_ma5 <= THRESHOLD:
            alerts.append(f"⚡回測5日線(剩{gap_ma5:.1%})")
        elif -THRESHOLD <= gap_ma5 < 0:
            alerts.append(f"⚠️跌破5日線({gap_ma5:.1%})")

    if ma20 > 0:
        gap_ma20 = (p - ma20) / ma20
        if 0 < gap_ma20 <= THRESHOLD:
            alerts.append(f"🛡️回測月線(剩{gap_ma20:.1%})")
        elif -THRESHOLD <= gap_ma20 < 0:
            alerts.append(f"☠️跌破月線({gap_ma20:.1%})")

    if ma60 > 0:
        gap_ma60 = (p - ma60) / ma60
        if abs(gap_ma60) > 0.15: 
            bias_status = "🔥乖離過大" if gap_ma60 > 0 else "❄️嚴重超跌"
            alerts.append(bias_status)

    return " | ".join(alerts) if alerts else ""

# ==========================================
# 3. AI 策略生成器 (個股與總結)
# ==========================================
def get_gemini_strategy(data):
    if not ai_client: return "AI 未啟動"
    
    profit_info = "目前無庫存，純觀察"
    if data['is_hold']:
        roi = ((data['p'] - data['cost']) / data['cost']) * 100
        profit_info = f"🔴庫存持有中 (成本:{data['cost']} | 現價:{data['p']} | 損益:{roi:+.2f}%)"

    prompt = f"""
    角色：頂尖台股操盤手。
    任務：針對個股 {data['name']} ({data['id']}) 進行全方位診斷，並給出下一步具體操作建議。
    
    【關鍵訊號】
    - 均線警示：{data['ma_alert']}
    - 5日:{data['ma5']} | 20日:{data['ma20']} | 60日:{data['ma60']}
    
    【技術數據】
    - 價格：{data['p']} (日漲跌 {data['d1']:.2%}) | 乖離率：{data['bias_str']}
    - 籌碼：外資連買 {data['fs']} 天 | 投信連買 {data['ss']} 天
    - 量能：{data['vol_str']}
    - RSI：{data['rsi']}
    - 風險評級：{data['risk']}
    
    【資產狀態】
    - {profit_info}

    【請給出約 80 字的操作建議】
    1. 若有均線警示，請指出價格並給出對策(如:守穩可接/跌破停損)。
    2. 給出明確指令：續抱/減碼/止損/觀望/佈局。
    3. 結合損益與技術面給出防守價。
    """

    for model_name in MODEL_CANDIDATES:
        try:
            response = ai_client.models.generate_content(
                model=model_name, 
                contents=prompt
            )
            return response.text.replace('\n', ' ').strip()
        except Exception as e:
            if "429" in str(e): # 額度滿了換下一個模型
                continue
            pass
    return "AI 分析暫時無法使用"

def generate_and_save_summary(data_rows, report_time_str):
    print("🧠 正在生成全域總結報告 (使用 Gemini)...")
    
    if not ai_client:
        print("❌ AI 未啟動，跳過總結報告")
        return

    inventory_txt = ""
    watchlist_txt = ""
    
    # 資料整理
    for row in data_rows:
        try:
            # 確保欄位足夠 (避免 Index Error)
            if len(row) < 22: continue
            
            name, sid, status, score = row[2], row[1], row[3], row[4]
            signal, ai_advice = row[20], row[21]
            
            stock_info = f"- {name}({sid}) | 評分:{score} | 訊號:{signal} | AI簡評:{ai_advice[:60]}...\n"
            
            if "庫存" in status:
                inventory_txt += stock_info
            else:
                watchlist_txt += stock_info
        except: continue

    if not inventory_txt and not watchlist_txt:
        print("⚠️ 無有效數據可供總結")
        return

    prompt = f"""
    角色：你是專業的台股投資總監。
    任務：根據今日的「全能金流診斷報表」數據，撰寫一份高層次的【戰略總結報告】。
    
    【庫存持股清單】
    {inventory_txt}
    
    【觀察名單清單】
    {watchlist_txt}
    
    請針對以上資訊，使用繁體中文，撰寫以下三個章節（請條理分明，語氣專業）：
    
    ### 1. 庫存持股總體檢
    (請分析目前持股的整體強弱、是否有出現危險訊號(如跌破均線/過熱)需要立刻處理的股票，並評估整體曝險狀況)
    
    ### 2. 觀察名單潛力股
    (從觀察名單中挑選出評分最高、或籌碼/型態最值得關注的 3-5 檔潛力股進行點評，說明為何值得關注)
    
    ### 3. 總結操作建議
    (給出明日或未來一週的整體操作策略，例如：積極做多、防守為主、現金為王或是調節持股)
    """

    summary_result = ""
    
    # 迴圈嘗試所有模型，修復 404 Error
    for model_name in MODEL_CANDIDATES:
        try:
            print(f"   ...嘗試使用模型: {model_name}")
            response = ai_client.models.generate_content(
                model=model_name, 
                contents=prompt
            )
            summary_result = response.text
            print("   ✅ 總結報告生成成功！")
            break
        except Exception as e:
            print(f"   ⚠️ 模型 {model_name} 失敗: {e}")
            continue

    if not summary_result:
        print("❌ 所有模型皆嘗試失敗，無法生成總結報告")
        return

    # 寫入新工作表
    try:
        client = get_gspread_client()
        if not client: return
        
        spreadsheet = client.open("全能金流診斷報表")
        sheet_title = report_time_str
        
        try:
            target_sheet = spreadsheet.worksheet(sheet_title)
            target_sheet.clear() 
            print(f"🧹 清除舊工作表: {sheet_title}")
        except gspread.WorksheetNotFound:
            try:
                target_sheet = spreadsheet.add_worksheet(title=sheet_title, rows=100, cols=10)
                print(f"🆕 建立新工作表: {sheet_title}")
            except: 
                print("⚠️ 建立分頁失敗，可能名稱重複或格式錯誤")
                return
            
        lines = summary_result.split('\n')
        cell_data = [[line] for line in lines]
        target_sheet.update(range_name='A1', values=cell_data)
        target_sheet.format("A1:A100", {"wrapStrategy": "WRAP"})
        target_sheet.columns_auto_resize(0, 0)
        
        print(f"✅ 戰略總結報告已寫入工作表: [{sheet_title}]")
        
    except Exception as e:
        print(f"⚠️ 寫入總結工作表失敗: {e}")

# ==========================================
# 4. 核心邏輯
# ==========================================
def get_watch_list_from_sheet():
    try:
        client = get_gspread_client()
        if not client: return []

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

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    if loss.empty or loss.iloc[-1] == 0: return pd.Series([100.0] * len(series))
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_tw_stock(sid):
    clean_id = str(sid).strip().upper()
    suffixes = [".TWO", ".TW"] if clean_id.startswith(('3', '4', '5', '6', '8')) else [".TW", ".TWO"]
        
    for suffix in suffixes:
        target = f"{clean_id}{suffix}"
        try:
            stock = yf.Ticker(target)
            hist = stock.history(period="5d")
            if not hist.empty: return stock, target
        except: continue
    return None, None

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

    if r['ma_alert']:
        hint = r['ma_alert']
    elif is_hold:
        if r['rsi'] >= 80: hint = f"❗分批止盈 {profit_str}"
        elif r['d1'] <= -0.04: hint = f"📢急跌守5日線 {profit_str}"
        else: hint = f"📦持股觀察 {profit_str}"
    else:
        if r['score'] >= 9: hint = "⭐⭐優先佈局"
        elif r['score'] >= 8 and r['vol_r'] > 1.5: hint = "🚀放量轉強"
        else: hint = "持續追蹤"

    return risk, trend_status, hint

def fetch_pro_metrics(stock_data):
    sid = stock_data['sid']
    is_hold = stock_data['is_hold']
    cost = stock_data['cost']

    # 1. 抓取股價 (Yahoo)
    stock, full_id = get_tw_stock(sid)
    if not stock: 
        print(f"⚠️ 無法獲取股價: {sid}")
        return None
    
    try:
        df_hist = stock.history(period="8mo")
        if len(df_hist) < 120: return None
        
        info = stock.info
        latest = df_hist.iloc[-1]
        curr_p, curr_vol = latest['Close'], latest['Volume']
        today_amount = (curr_vol * curr_p) / 100_000_000
        
        rsi_series = calculate_rsi(df_hist['Close'])
        clean_rsi = 0.0 if pd.isna(rsi_series.iloc[-1]) else round(rsi_series.iloc[-1], 1)
        
        ma5 = df_hist['Close'].rolling(5).mean().iloc[-1]
        ma20 = df_hist['Close'].rolling(20).mean().iloc[-1]
        ma60 = df_hist['Close'].rolling(60).mean().iloc[-1]
        
        bias_60 = ((curr_p - ma60) / ma60) * 100
        ma_alert_str = check_ma_status(curr_p, ma5, 0, ma20, ma60)
        
        raw_yield = info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) - 1
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean() if df_hist['Volume'].iloc[-6:-1].mean() > 0 else 0

        # 2. 抓取籌碼 (FinMind)
        pure_id = ''.join(filter(str.isdigit, sid))
        fs, ss = get_streak_only(pure_id) 
        vol_str = get_vol_status_str(vol_ratio)

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
        market_label = '櫃' if '.TWO' in full_id else '市'

        res = {
            "id": f"{sid}{market_label}", "name": stock_name, 
            "score": score, "rsi": clean_rsi, "industry": industry,
            "vol_r": round(vol_ratio, 1), "p": round(curr_p, 2), 
            "yield": raw_yield, "amt_t": round(today_amount, 1),
            "d1": d1, "d5": d5, "m1": m1, "m6": m6,
            "is_hold": is_hold, "cost": cost,
            "bias_str": f"{bias_60:+.1f}%",
            "vol_str": vol_str,
            "fs": fs, "ss": ss,
            "ma5": round(ma5, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2),
            "ma_alert": ma_alert_str
        }

        risk, trend, hint = generate_auto_analysis(res, is_hold, cost)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        
        # 3. 個股 AI 分析 (自動換模型)
        res['ai_strategy'] = get_gemini_strategy(res)
        
        return res
    except Exception as e:
        print(f"⚠️ 分析過程出錯 ({sid}): {e}")
        return None

def sync_to_sheets(data_list):
    try:
        client = get_gspread_client()
        if not client: return
        sheet = client.open("全能金流診斷報表").get_worksheet(0)
        sheet.append_rows(data_list, value_input_option='USER_ENTERED')
        print(f"✅ 成功同步 {len(data_list)} 筆數據至主報表")
    except Exception as e:
        print(f"⚠️ Google Sheets 同步失敗: {e}")

# ==========================================
# 5. 主程式入口
# ==========================================
def main():
    # 台灣時間修正
    current_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')
    results_line, results_sheet = [], []

    watch_data_list = get_watch_list_from_sheet()
    total_stocks = len(watch_data_list)
    
    if not watch_data_list:
        print("❌ 中止：觀察名單讀取失敗。")
        return

    print(f"🚀 開始分析 {total_stocks} 檔股票 (每檔間隔 15 秒)...")

    # 顯示進度條
    for idx, stock_data in enumerate(watch_data_list):
        sid = stock_data['sid']
        print(f"[{idx+1}/{total_stocks}] 正在分析: {sid} ... ", end="", flush=True)
        
        try:
            res = fetch_pro_metrics(stock_data)
            if res:
                print(f"✅ 完成 ({res['name']})")
                results_line.append(res)
                
                hold_mark = "📦庫存" if res['is_hold'] else "👀觀察"
                
                results_sheet.append([
                    current_time, res['id'], res['name'], hold_mark, 
                    res['score'], res['rsi'], res['industry'], 
                    res['bias_str'], res['vol_str'], res['fs'], res['ss'],
                    res['p'], res['yield'], res['amt_t'], 
                    res['d1'], res['d5'], res['m1'], res['m6'],
                    res['risk'], res['trend'], res['hint'],
                    res['ai_strategy']
                ])
            else:
                print("⚠️ 失敗 (無數據)")
        except Exception as e:
            print(f"❌ 嚴重錯誤: {e}")

        if idx < total_stocks - 1:
            time.sleep(15.0) 
    
    # 報告與總結
    if results_line:
        results_line.sort(key=lambda x: x['score'], reverse=True)
        
        # LINE 推播
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
        try:
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)
            print("✅ LINE 推播已發送")
        except: pass

    if results_sheet:
        # 1. 寫入原始報表
        sync_to_sheets(results_sheet)
        
        # 2. 生成並寫入 AI 總結報告 (新功能)
        generate_and_save_summary(results_sheet, current_time)
    else:
        print("❌ 本次執行沒有產生任何有效數據，無法更新報表。")

if __name__ == "__main__":
    main()
