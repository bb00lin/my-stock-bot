import os, yfinance as yf, pandas as pd, requests, time, datetime
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"

def send_line_message(message):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

def analyze_stock_v4(ticker, stock_info, mode="NORMAL"):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # --- 基本面初篩 ---
        margin = info.get('grossMargins', 0) or 0
        eps = info.get('trailingEps', 0) or 0
        
        # 強勢攻擊模式下，過濾掉毛利太低標的
        if mode == "NORMAL" and margin < 0.10: return None

        df = stock.history(period="1y", progress=False)
        if len(df) < 60: return None
        if df.iloc[-1]['Volume'] == 0: df = df.iloc[:-1]
        
        curr_p = df.iloc[-1]['Close']
        prev_p = df.iloc[-2]['Close']
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        ma60 = SMAIndicator(df['Close'], 60).sma_indicator().iloc[-1]
        vol_ratio = df.iloc[-1]['Volume'] / df['Volume'].iloc[-11:-1].mean()
        
        is_potential = False
        tag = ""

        if mode == "NORMAL":
            if vol_ratio > 1.3 and df.iloc[-1]['Volume'] >= 500 and curr_p > prev_p:
                tag = "🔥 強勢攻擊"
                is_potential = (curr_p > ma60) and ((curr_p - ma60)/ma60 < 0.25)
        
        if is_potential:
            return (
                f"📍{ticker} {stock_info['name']}\n"
                f"體質：毛利 {margin*100:.1f}% | EPS {eps:.2f}\n"
                f"狀態：({tag})\n"
                f"現價：{curr_p:.2f} ({((curr_p/prev_p)-1)*100:+.1f}%)\n"
                f"RSI：{rsi:.1f} | 量比：{vol_ratio:.1f}"
            )
    except: return None

def main():
    start_time = datetime.datetime.now()
    current_date = start_time.strftime('%Y-%m-%d')
    dynamic_filename = f"scan_report_{current_date}.txt"
    
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    stock_map = {f"{row['stock_id']}{'.TWO' if '上櫃' in str(row['market_type']) else '.TW'}": 
                 {'name': row['stock_name'], 'industry': row['industry_category']} 
                 for _, row in stock_df.iterrows() if len(str(row['stock_id'])) == 4}

    all_sections = []
    # 範例僅掃描 NORMAL 模式以求精準
    for mode_name, mode_key in [("強勢成長股", "NORMAL")]:
        results = []
        for ticker, info in stock_map.items():
            res = analyze_stock_v4(ticker, info, mode=mode_key)
            if res: results.append(res)
        
        if results:
            msg_header = f"🔍 【市場核心掃描 - {mode_name}】"
            all_sections.append(f"{msg_header}\n" + "\n---\n".join(results))
            for i in range(0, len(results), 5):
                send_line_message(f"{msg_header}\n\n" + "\n---\n".join(results[i:i+5]))

    # --- 儲存與同步 ---
    report_content = "\n\n".join(all_sections) if all_sections else "本日市場無符合基本面之強勢標的。"
    with open(dynamic_filename, "w", encoding="utf-8") as f: f.write(report_content)
    
    local_path = r"D:\MEGA\下載\股票"
    if os.path.exists(local_path):
        with open(os.path.join(local_path, dynamic_filename), "w", encoding="utf-8") as f: f.write(report_content)

if __name__ == "__main__":
    main()
