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
# 2. 籌碼與財報獲取工具
# ==========================================
def get_deep_chip_info(sid_clean):
    """獲取大戶持股分布 (400張/1000張)"""
    try:
        dl = DataLoader()
        start_date = (datetime.date.today() - datetime.timedelta(days=20)).strftime('%Y-%m-%d')
        df_holder = dl.taiwan_stock_holding_shares_per(stock_id=sid_clean, start_date=start_date)
        if not df_holder.empty:
            # 取得最新的一筆週資料
            latest_date = df_holder['date'].max()
            current_week = df_holder[df_holder['date'] == latest_date]
            
            # 計算 400張以上大戶 (含 1000張以上)
            big_levels = ['400-600', '600-800', '800-1000', '1000以上']
            big_400 = current_week[current_week['hold_shares_level'].isin(big_levels)]['percent'].sum()
            big_1000 = current_week[current_week['hold_shares_level'] == '1000以上']['percent'].sum()
            return f"大戶持股(400+): {big_400:.1f}% | 巨鱷(1000+): {big_1000:.1f}%"
    except: pass
    return "籌碼數據：暫無最新週報資料"

# ==========================================
# 3. 核心診斷邏輯
# ==========================================
def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock = yf.Ticker(f"{clean_id}.TW" if int(clean_id) < 9000 else f"{clean_id}.TWO")
        df = stock.history(period="1y")
        if df.empty: return f"❌ 找不到 {clean_id} 的資料。"
        
        info = stock.info
        curr_p = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # 財報數據提取
        eps = info.get('trailingEps', 0) or 0
        margin = (info.get('grossMargins', 0) or 0) * 100
        pe_ratio = info.get('trailingPE', 0) or 0
        rev_growth = (info.get('revenueGrowth', 0) or 0) * 100
        
        # 籌碼數據提取
        chip_deep = get_deep_chip_info(clean_id)

        report = (
            f"=== {clean_id} {info.get('shortName','標的')} 深度診斷 ===\n"
            f"● 現價：{curr_p:.2f} | RSI：{rsi:.2f}\n\n"
            f"【📊 財報體質】\n"
            f"● EPS：{eps:.2f} | 本益比：{pe_ratio:.1f}\n"
            f"● 毛利率：{margin:.1f}% | 營收YoY：{rev_growth:+.1f}%\n\n"
            f"【💎 籌碼結構】\n"
            f"● {chip_deep}\n\n"
            f"【🚀 實戰指引】\n"
            f"● 趨勢：{'🔥多頭架構' if curr_p > ma60 else '☁️弱勢空頭'}\n"
            f"● 乖離：{((curr_p-ma60)/ma60)*100:+.1f}% (60MA)\n"
            f"● 提示：{'⚠️高檔乖離大，防回檔' if (curr_p-ma60)/ma60 > 0.15 else '✅位階尚可'}\n"
            f"======================================="
        )
        return report
    except Exception as e:
        return f"❌ {sid} 診斷錯誤: {str(e)}"

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2330"
    targets = input_str.replace(',', ' ').split()
    all_reports = []
    for t in targets:
        report = get_diagnostic_report(t.strip().upper())
        send_line_message(report)
        all_reports.append(report)
        time.sleep(1)
    
    # --- 儲存邏輯 (包含 D 槽同步) ---
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    filename = f"manual_report_{today}.txt"
    content = "\n\n".join(all_reports)
    with open(filename, "w", encoding="utf-8") as f: f.write(content)
    
    local_path = r"D:\MEGA\下載\股票"
    if os.path.exists(local_path):
        with open(os.path.join(local_path, filename), "w", encoding="utf-8") as f: f.write(content)
