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
# 2. 進階籌碼數據獲取 (FinMind)
# ==========================================
def get_detailed_chips(sid_clean):
    """獲取大戶持股與法人連買天數"""
    try:
        dl = DataLoader()
        # A. 大戶持股 (每週更新)
        start_date_w = (datetime.date.today() - datetime.timedelta(days=20)).strftime('%Y-%m-%d')
        df_holder = dl.taiwan_stock_holding_shares_per(stock_id=sid_clean, start_date=start_date_w)
        big_info = "大戶持股：無數據"
        if not df_holder.empty:
            latest_date = df_holder['date'].max()
            current_week = df_holder[df_holder['date'] == latest_date]
            big_levels = ['400-600', '600-800', '800-1000', '1000以上']
            big_400 = current_week[current_week['hold_shares_level'].isin(big_levels)]['percent'].sum()
            big_1000 = current_week[current_week['hold_shares_level'] == '1000以上']['percent'].sum()
            big_info = f"400張+: {big_400:.1f}% | 1000張+: {big_1000:.1f}%"

        # B. 法人連買 (每日更新)
        start_date_d = (datetime.date.today() - datetime.timedelta(days=40)).strftime('%Y-%m-%d')
        df_inst = dl.taiwan_stock_institutional_investors(stock_id=sid_clean, start_date=start_date_d)
        inst_info = "法人動向：無數據"
        if not df_inst.empty:
            foreign = df_inst[df_inst['name'] == 'Foreign_Investor'].sort_values('date', ascending=False)
            sitc = df_inst[df_inst['name'] == 'Investment_Trust'].sort_values('date', ascending=False)
            
            def count_streak(df):
                streak = 0
                for _, row in df.iterrows():
                    if (row['buy'] - row['sell']) > 0: streak += 1
                    else: break
                return streak
            
            f_streak = count_streak(foreign)
            s_streak = count_streak(sitc)
            inst_info = f"外資連買: {f_streak}天 | 投信連買: {s_streak}天"
            
        return f"{inst_info}\n● {big_info}"
    except:
        return "籌碼數據獲取失敗"

# ==========================================
# 3. 核心診斷邏輯
# ==========================================
def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock = yf.Ticker(f"{clean_id}.TW" if int(clean_id) < 9000 else f"{clean_id}.TWO")
        info = stock.info
        df = stock.history(period="1y")
        if df.empty: return f"❌ 找不到 {clean_id} 的資料。"
        
        curr_p = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # 財報
        eps = info.get('trailingEps', 0) or 0
        margin = (info.get('grossMargins', 0) or 0) * 100
        pe = info.get('trailingPE', 0) or 0
        
        # 籌碼
        chip_report = get_detailed_chips(clean_id)

        report = (
            f"=== {clean_id} {info.get('shortName','標的')} 診斷 ===\n"
            f"● 現價：{curr_p:.2f} | RSI：{rsi:.1f}\n\n"
            f"【📊 核心財報】\n"
            f"● EPS：{eps:.2f} | 本益比：{pe:.1f}\n"
            f"● 毛利率：{margin:.1f}%\n\n"
            f"【💎 籌碼動向】\n"
            f"● {chip_report}\n\n"
            f"【🚀 實戰指南】\n"
            f"● 趨勢：{'🔥多頭' if curr_p > ma60 else '☁️空頭'} (乖離 {((curr_p-ma60)/ma60)*100:+.1f}%)\n"
            f"● 提示：{'⚠️高檔防回檔' if (curr_p-ma60)/ma60 > 0.15 else '✅位階安全'}\n"
            f"================================"
        )
        return report
    except Exception as e:
        return f"❌ {sid} 診斷出錯: {e}"

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2330"
    targets = input_str.replace(',', ' ').split()
    all_reports = []
    for t in targets:
        rep = get_diagnostic_report(t.strip())
        send_line_message(rep)
        all_reports.append(rep)
        time.sleep(1)
    
    # 存檔至 D 槽
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    fname = f"manual_report_{today}.txt"
    l_path = r"D:\MEGA\下載\股票"
    if os.path.exists(l_path):
        with open(os.path.join(l_path, fname), "w", encoding="utf-8") as f:
            f.write("\n\n".join(all_reports))
