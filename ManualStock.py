import os, yfinance as yf, pandas as pd, requests, datetime, time, sys
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

# ==========================================
# 1. 環境設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"

def send_line_message(message):
    print("\n" + "="*40 + "\n" + message + "\n" + "="*40)
    sys.stdout.flush()
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try: requests.post(url, headers=headers, json=payload)
    except: pass

# ==========================================
# 2. 強化的籌碼與量能邏輯
# ==========================================
def get_detailed_chips(sid_clean):
    inst_info = "法人：FinMind 無回應"
    big_info = "大戶：FinMind 無回應"
    vol_msg = ""

    # --- A. 嘗試從 FinMind 抓法人與大戶 (分開 try) ---
    try:
        dl = DataLoader()
        # 法人
        start_d = (datetime.date.today() - datetime.timedelta(days=30)).strftime('%Y-%m-%d')
        df_i = dl.taiwan_stock_institutional_investors(stock_id=sid_clean, start_date=start_d)
        if df_i is not None and not df_i.empty:
            def count_s(name):
                d = df_i[df_i['name'] == name].sort_values('date', ascending=False)
                c = 0
                for _, r in d.iterrows():
                    if (r['buy'] - r['sell']) > 0: c += 1
                    else: break
                return c
            inst_info = f"外資連買: {count_s('Foreign_Investor')}d | 投信連買: {count_s('Investment_Trust')}d"
        
        # 大戶
        start_w = (datetime.date.today() - datetime.timedelta(days=20)).strftime('%Y-%m-%d')
        df_h = dl.taiwan_stock_holding_shares_per(stock_id=sid_clean, start_date=start_w)
        if df_h is not None and not df_h.empty:
            latest = df_h[df_h['date'] == df_h['date'].max()]
            b400 = latest[latest['hold_shares_level'].isin(['400-600','600-800','800-1000','1000以上'])]['percent'].sum()
            big_info = f"大戶持股(400+): {b400:.1f}%"
    except:
        pass # 如果 FinMind 失敗，保持預設字串

    # --- B. 強制執行的 yfinance 量能診斷 (備援) ---
    try:
        ticker = f"{sid_clean}.TW" if int(sid_clean) < 9000 else f"{sid_clean}.TWO"
        s_obj = yf.Ticker(ticker)
        h = s_obj.history(period="5d")
        if len(h) >= 3:
            v_today = h['Volume'].iloc[-1]
            v_avg = h['Volume'].iloc[:-1].mean()
            v_ratio = v_today / v_avg if v_avg > 0 else 0
            v_status = "🔥爆量" if v_ratio > 2.0 else "☁️量平"
            vol_msg = f"● {v_status} (量比:{v_ratio:.1f}x)"
    except:
        vol_msg = "● 量能：數據獲取失敗"

    return f"{inst_info}\n● {big_info}\n{vol_msg}"

# ==========================================
# 3. 核心診斷邏輯
# ==========================================
def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock_ticker = f"{clean_id}.TW" if int(clean_id) < 9000 else f"{clean_id}.TWO"
        stock = yf.Ticker(stock_ticker)
        info = stock.info
        df = stock.history(period="1y")
        
        if df.empty: return f"❌ 找不到 {clean_id} 的資料。"
        
        curr_p = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # 財報 (增加判斷，避免新藥股無 PE 導致報錯)
        eps = info.get('trailingEps', 0) or 0
        margin = (info.get('grossMargins', 0) or 0) * 100
        pe = info.get('trailingPE', 0) or "N/A"
        
        # 籌碼與備援量能
        chip_report = get_detailed_chips(clean_id)

        report = (
            f"=== {clean_id} {info.get('shortName', '標的')} 診斷 ===\n"
            f"● 現價：{curr_p:.2f} | RSI：{rsi:.1f}\n\n"
            f"【📊 核心財報】\n"
            f"● EPS：{eps:.2f} | 本益比：{pe}\n"
            f"● 毛利率：{margin:.1f}%\n\n"
            f"【💎 籌碼/量能】\n"
            f"● {chip_report}\n\n"
            f"【🚀 實戰指南】\n"
            f"● 趨勢：{'🔥多頭' if curr_p > ma60 else '☁️空頭'} (乖離 {((curr_p-ma60)/ma60)*100:+.1f}%)\n"
            f"● 提示：{'⚠️高檔防回檔' if (curr_p-ma60)/ma60 > 0.15 else '✅位階安全'}\n"
            f"================================"
        )
        return report
    except Exception as e:
        return f"❌ {sid} 總體診斷出錯: {e}"

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2330"
    targets = input_str.replace(',', ' ').split()
    all_reports = []
    for t in targets:
        rep = get_diagnostic_report(t.strip())
        send_line_message(rep)
        all_reports.append(rep)
        time.sleep(1)
    
    # 存檔 (確保 GitHub 行動成功)
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    content = "\n\n".join(all_reports)
    with open(f"manual_report_{today}.txt", "w", encoding="utf-8") as f: f.write(content)
    with open("latest_manual.txt", "w", encoding="utf-8") as f: f.write(content)
    
    # 本機同步
    l_path = r"D:\MEGA\下載\股票"
    if os.path.exists(l_path):
        try:
            with open(os.path.join(l_path, f"manual_report_{today}.txt"), "w", encoding="utf-8") as f: f.write(content)
        except: pass
