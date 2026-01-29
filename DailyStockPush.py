import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import gspread
import logging
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

# Email 設定
MAIL_RECEIVERS = ['bb00lin@gmail.com']
MAIL_USER = os.environ.get('MAIL_USERNAME')
MAIL_PASS = os.environ.get('MAIL_PASSWORD')

# [付費版優化] 優先使用高額度正式版模型
MODEL_CANDIDATES = [
    "gemini-2.0-flash",      # 🚀 首選：RPM 2000+
    "gemini-1.5-flash",      
    "gemini-1.5-pro",
]

# 全域變數
HAS_GENAI = False
AI_CLIENT = None

# ==========================================
# [啟動檢查] AI 自我診斷
# ==========================================
def check_ai_health():
    """在程式開始前，測試 AI 是否存活"""
    global HAS_GENAI, AI_CLIENT
    print("🤖 正在進行 AI 模型連線測試 (使用 google-genai SDK)...")
    
    if not GEMINI_API_KEY:
        print("⚠️ 警告: 未設定 GEMINI_API_KEY，將跳過 AI 功能。")
        HAS_GENAI = False
        return

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        print("   ...嘗試生成測試訊號")
        for model_name in MODEL_CANDIDATES:
            try:
                response = client.models.generate_content(
                    model=model_name, 
                    contents="Hi"
                )
                if response and response.text:
                    print(f"✅ AI 測試成功！將使用模型: {model_name}")
                    HAS_GENAI = True
                    AI_CLIENT = client
                    return
            except Exception as e:
                err_msg = str(e)
                print(f"   ⚠️ 模型 {model_name} 失敗: {err_msg.split('(')[0][:80]}...")
                continue
        
        print("❌ 失敗: 所有候選模型皆無法連線。將以「無 AI 模式」繼續執行。")
        HAS_GENAI = False

    except Exception as e:
        print(f"❌ AI 初始化錯誤: {e}")
        HAS_GENAI = False

check_ai_health()

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
# 2. 輔助數據獲取
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
# [功能] 黃金進場公式檢測
# ==========================================
def check_golden_entry(df_hist):
    """黃金進場公式：量縮回後買上漲"""
    try:
        if len(df_hist) < 65: return False, ""
        
        latest = df_hist.iloc[-1]
        prev = df_hist.iloc[-2]
        close = latest['Close']
        ma20 = df_hist['Close'].rolling(20).mean().iloc[-1]
        ma60 = df_hist['Close'].rolling(60).mean().iloc[-1]
        
        # 1. 確認趨勢
        if not (close > ma20 and ma20 > ma60): return False, "非多頭趨勢"

        # 2. 確認回檔
        past_4_days = df_hist.iloc[-5:-1]
        drop_days = 0
        for i in range(len(past_4_days)):
            if past_4_days.iloc[i]['Close'] < past_4_days.iloc[i]['Open']: drop_days += 1
            elif past_4_days.iloc[i]['Close'] < past_4_days.iloc[i-1]['Close']: drop_days += 1
        if drop_days < 2: return False, "無明顯回檔"

        # 3. 進場信號 (紅K + 轉強)
        is_red = close > latest['Open']
        is_strong = close > prev['Close']
        if not (is_red and is_strong): return False, "今日未轉強"

        # 4. 量能
        vol_ma5 = df_hist['Volume'].iloc[-6:-1].mean()
        is_vol_shrink_yesterday = prev['Volume'] < vol_ma5
        if not is_vol_shrink_yesterday:
             if latest['Volume'] < prev['Volume']: return False, "攻擊量不足"

        return True, "🔥黃金買點:量縮回後買上漲"
    except: return False, ""

# ==========================================
# [功能] 漲停潛力股篩選器
# ==========================================
def get_limit_up_potential(r):
    """
    綜合判斷是否有漲停相 (Momentum)
    回傳: (分數 0-100, 原因字串)
    """
    score = 0
    reasons = []
    
    # 1. 技術面：多頭強勢 (30分)
    if r['p'] > r['ma5'] and r['ma5'] > r['ma10'] and r['ma10'] > r['ma20']:
        score += 30
        reasons.append("🔥均線多頭發散")
    
    # 2. 籌碼面：投信認養 (30分)
    if r['ss'] > 0: # 投信連買
        score += 30
        reasons.append("🏦投信點火")
    elif r['fs'] >= 3: # 外資連買
        score += 20
        reasons.append("💰外資連買")
        
    # 3. 動能面：爆量 (20分)
    if r['vol_r'] >= 1.8:
        score += 20
        reasons.append("📈出量攻擊")
        
    # 4. K線：今日強勢 (20分)
    if r['d1'] > 0.03: # 今日漲幅 > 3%
        score += 20
        reasons.append("🚀長紅棒")

    return score, " | ".join(reasons)

# ==========================================
# 3. AI 策略生成器 (單檔)
# ==========================================
def get_gemini_strategy(data):
    if not HAS_GENAI or not AI_CLIENT: return "AI 服務暫停"
    
    profit_info = "目前無庫存，純觀察"
    if data['is_hold']:
        roi = ((data['p'] - data['cost']) / data['cost']) * 100
        profit_info = f"🔴庫存持有中 (成本:{data['cost']} | 現價:{data['p']} | 損益:{roi:+.2f}%)"

    # [修改] Prompt 增加具體均線要求
    prompt = f"""
    角色：頂尖台股操盤手。
    任務：針對個股 {data['name']} ({data['id']}) 進行短線診斷。
    
    【關鍵均線價格】(重要參考)
    - 5日線: {data['ma5']}
    - 10日線: {data['ma10']}
    - 20日線: {data['ma20']}
    - 60日線: {data['ma60']}
    
    【技術數據】
    - 現價：{data['p']} (日漲跌 {data['d1']:.2%})
    - 籌碼：外資連買 {data['fs']} 天 | 投信連買 {data['ss']} 天
    - 量能：{data['vol_str']}
    - RSI：{data['rsi']}
    
    【訊號】
    - 均線警示：{data['ma_alert']}
    - 黃金進場：{'✅是' if data['is_golden'] else '❌否'}
    
    【資產狀態】
    - {profit_info}

    【指令】請給出約 80 字的操作建議：
    1. 若提到均線(如5日/10日線)，必須在括號內標註該均線價格。例如：「跌破5日線({data['ma5']})」。
    2. 給出明確指令：續抱/減碼/止損/觀望/佈局。
    3. 結合損益與技術面給出具體數字的防守價。
    """

    for model_name in MODEL_CANDIDATES:
        try:
            response = AI_CLIENT.models.generate_content(model=model_name, contents=prompt)
            return response.text.replace('\n', ' ').strip()
        except:
            time.sleep(1)
            continue
    return "AI 連線忙碌中"

# ==========================================
# 4. 全域戰略報告生成器 (核心升級)
# ==========================================
def generate_and_save_summary(data_list, report_time_str):
    print("🧠 正在生成全域總結報告 (使用 Gemini)...")
    
    if not HAS_GENAI or not AI_CLIENT:
        print("❌ AI 未啟動，跳過總結報告生成。")
        return "本次報告未包含 AI 總結 (連線失敗)"

    inventory_txt = ""
    watchlist_txt = ""
    golden_candidates = ""
    limit_up_candidates_txt = ""
    
    # 遍歷所有數據，準備給 AI 的素材
    for r in data_list:
        try:
            # 格式化每檔股票的資訊 (包含具體均線價格)
            stock_info = (
                f"- {r['name']}({r['id']}) | 現價:{r['p']} | 分數:{r['score']} | "
                f"MA5:{r['ma5']} | MA10:{r['ma10']} | MA20:{r['ma20']} | "
                f"AI建議:{r['ai_strategy'][:50]}...\n"
            )
            
            if r['is_hold']:
                inventory_txt += stock_info
            else:
                watchlist_txt += stock_info
                
            # 篩選黃金買點
            if r['is_golden']:
                golden_candidates += f"- {r['name']}: {r['golden_msg']} (防守MA20: {r['ma20']})\n"

            # [新增] 篩選漲停潛力股 (分數 > 60)
            limit_up_score, limit_up_reason = get_limit_up_potential(r)
            if limit_up_score >= 60:
                limit_up_candidates_txt += (
                    f"- {r['name']}: 潛力分{limit_up_score} ({limit_up_reason}) | "
                    f"籌碼:投{r['ss']}外{r['fs']} | 題材請AI補充\n"
                )
                
        except: continue

    if not golden_candidates: golden_candidates = "今日無符合標準之標的。"
    if not limit_up_candidates_txt: limit_up_candidates_txt = "今日無明顯漲停特徵股。"

    # [關鍵修改] 總結報告 Prompt：要求具體數字與漲停預測
    prompt = f"""
    角色：你是專業的台股投資總監。
    任務：根據今日數據，撰寫一份【戰略總結報告】。
    
    【庫存清單】(內含MA均線價格)
    {inventory_txt}
    
    【觀察清單】
    {watchlist_txt}
    
    【🔥 黃金進場公式篩選】
    {golden_candidates}
    
    【🚀 漲停潛力股獵殺名單】(基於技術+籌碼篩選)
    {limit_up_candidates_txt}
    
    請撰寫以下五個章節 (繁體中文，語氣專業)：
    
    ### 1. 庫存持股總體檢
    (分析持股強弱。⚠️重要：若建議防守或減碼，必須寫出具體的均線價格，例如「防守10日線(123.5)」)
    
    ### 2. 觀察名單潛力股
    (挑選 3-5 檔評分最高個股點評。⚠️重要：給出建議進場價或防守價時，務必參考提供的MA數值寫出具體金額)
    
    ### 3. 總結操作建議
    (給出未來一週策略：積極/保守/現金為王)
    
    ### 4. 黃金進場公式 (每日必檢)
    (針對篩選結果確認。若達標，請給出明確的「停損價格」；若無達標請重申口訣)
    
    ### 5. 🎯 漲停潛力股獵殺 (AI預測)
    (針對【漲停潛力股獵殺名單】中的股票，請結合你內建的知識庫，分析其「熱門題材」(如CoWoS/重電/IP/AI PC等)，並預測短期爆發力。若名單為空，請說明目前盤面缺乏攻擊動能)
    """

    for model_name in MODEL_CANDIDATES:
        try:
            response = AI_CLIENT.models.generate_content(model=model_name, contents=prompt)
            return response.text
        except Exception as e:
            time.sleep(2)
            continue

    return "AI 生成失敗"

# ==========================================
# 5. 抓取數據與計算
# ==========================================
def fetch_pro_metrics(stock_data):
    sid = stock_data['sid']
    is_hold = stock_data['is_hold']
    cost = stock_data['cost']

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
        
        # 計算均線 (保留小數點後2位)
        ma5 = round(df_hist['Close'].rolling(5).mean().iloc[-1], 2)
        ma10 = round(df_hist['Close'].rolling(10).mean().iloc[-1], 2)
        ma20 = round(df_hist['Close'].rolling(20).mean().iloc[-1], 2)
        ma60 = round(df_hist['Close'].rolling(60).mean().iloc[-1], 2)
        
        bias_60 = ((curr_p - ma60) / ma60) * 100
        ma_alert_str = check_ma_status(curr_p, ma5, ma10, ma20, ma60)
        is_golden, golden_msg = check_golden_entry(df_hist)
        
        raw_yield = info.get('dividendYield', 0) or 0
        d1 = (curr_p / df_hist['Close'].iloc[-2]) - 1
        d5 = (curr_p / df_hist['Close'].iloc[-6]) - 1
        m1 = (curr_p / df_hist['Close'].iloc[-21]) - 1
        m6 = (curr_p / df_hist['Close'].iloc[-121]) - 1
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean() if df_hist['Volume'].iloc[-6:-1].mean() > 0 else 0

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
        if is_golden: score += 3

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
            "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
            "ma_alert": ma_alert_str,
            "is_golden": is_golden,
            "golden_msg": golden_msg
        }

        risk, trend, hint = generate_auto_analysis(res, is_hold, cost)
        res.update({"risk": risk, "trend": trend, "hint": hint})
        res['ai_strategy'] = get_gemini_strategy(res)
        
        return res
    except Exception as e:
        print(f"⚠️ 分析過程出錯 ({sid}): {e}")
        return None

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
        if r['is_golden']: hint = "💰黃金買點浮現"
        elif r['score'] >= 9: hint = "⭐⭐優先佈局"
        elif r['score'] >= 8 and r['vol_r'] > 1.5: hint = "🚀放量轉強"
        else: hint = "持續追蹤"

    return risk, trend_status, hint

# ==========================================
# 6. 主程式
# ==========================================
def main():
    current_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')
    watch_data_list = get_watch_list_from_sheet()
    if not watch_data_list: return

    # 使用字典列表來儲存完整結果，以便傳遞給 summary 函式
    results_line = [] 
    results_sheet = []

    print(f"🚀 開始分析 {len(watch_data_list)} 檔股票 (每檔間隔 2 秒)...")

    for idx, stock_data in enumerate(watch_data_list):
        sid = stock_data['sid']
        print(f"[{idx+1}/{len(watch_data_list)}] 分析: {sid} ... ", end="", flush=True)
        
        try:
            res = fetch_pro_metrics(stock_data)
            if res:
                print(f"✅ ({res['name']})")
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
            else: print("⚠️ 失敗")
        except: print("❌ 錯誤")

        if idx < len(watch_data_list) - 1: time.sleep(2.0)
    
    # 生成報告
    if results_line:
        # [修改] 將完整數據結構傳入 summary 函式
        summary_text = generate_and_save_summary(results_line, current_time)
        
        # 同步與寄信 (略為簡化，保留核心功能)
        try:
            client = get_gspread_client()
            if client:
                sheet = client.open("全能金流診斷報表").get_worksheet(0)
                sheet.append_rows(results_sheet, value_input_option='USER_ENTERED')
                
                # 寫入 Summary 分頁
                try:
                    s_sheet = client.open("全能金流診斷報表").worksheet(current_time)
                    s_sheet.clear()
                except:
                    s_sheet = client.open("全能金流診斷報表").add_worksheet(title=current_time, rows=100, cols=10)
                
                s_sheet.update(range_name='A1', values=[[line] for line in summary_text.split('\n')])
        except: pass

        # 寄信
        if MAIL_USER and MAIL_PASS:
            email_body = f"""
            <html><body>
                <h2>📊 {current_time} 全能金流診斷</h2>
                <pre style="font-family: sans-serif; white-space: pre-wrap;">{summary_text}</pre>
                <hr>
                <p>詳細數據請見 Google Sheets。</p>
            </body></html>
            """
            msg = MIMEMultipart()
            msg['From'] = MAIL_USER
            msg['To'] = ", ".join(MAIL_RECEIVERS)
            msg['Subject'] = f"[{current_time}] 台股 AI 戰略日報"
            msg.attach(MIMEText(email_body, 'html'))
            
            try:
                server = smtplib.SMTP('smtp.gmail.com', 587)
                server.starttls()
                server.login(MAIL_USER, MAIL_PASS)
                server.send_message(msg)
                server.quit()
                print("✅ Email 已發送")
            except: print("❌ Email 發送失敗")

        # LINE 推播 (略)
        if LINE_ACCESS_TOKEN:
            msg = f"📊 {current_time} 戰略報告已生成，請查收 Email 或 Google Sheets。"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
            payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
            requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=payload)

if __name__ == "__main__":
    main()
