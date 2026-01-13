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

def get_streak_only(sid_clean):
    """每日掃描專用：僅獲取連買天數"""
    try:
        dl = DataLoader()
        start = (datetime.date.today() - datetime.timedelta(days=20)).strftime('%Y-%m-%d')
        df = dl.taiwan_stock_institutional_investors(stock_id=sid_clean, start_date=start)
        if df.empty: return 0, 0
        foreign = df[df['name'] == 'Foreign_Investor'].sort_values('date', ascending=False)
        sitc = df[df['name'] == 'Investment_Trust'].sort_values('date', ascending=False)
        
        def count_s(d):
            c = 0
            for _, r in d.iterrows():
                if (r['buy'] - r['sell']) > 0: c += 1
                else: break
            return c
        return count_s(foreign), count_s(sitc)
    except: return 0, 0

def analyze_stock_v6(ticker, stock_info):
    try:
        clean_id = ticker.split('.')[0]
        stock = yf.Ticker(ticker)
        info = stock.info
        
        # 基本面過濾：毛利 > 10% 且 EPS > 0
        margin = info.get('grossMargins', 0) or 0
        eps = info.get('trailingEps', 0) or 0
        if margin < 0.10 or eps <= 0: return None

        df = stock.history(period="1y", progress=False)
        if len(df) < 60: return None
        
        curr_p = df.iloc[-1]['Close']
        vol_ratio = df.iloc[-1]['Volume'] / df['Volume'].iloc[-11:-1].mean()
        ma60 = SMAIndicator(df['Close'], 60).sma_indicator().iloc[-1]
        
        # 獲取法人連買
        f_streak, s_streak = get_streak_only(clean_id)
        
        # 篩選條件：(投信連買 > 1天 或 外資連買 > 2天) 且 量大增且價格站上MA60
        if (f_streak >= 2 or s_streak >= 1) and vol_ratio > 1.2 and curr_p > ma60:
            inst_tag = "🚀 投信認養" if s_streak >= 2 else "🔍 外資掃貨"
            return (
                f"📍{ticker} {stock_info['name']} ({inst_tag})\n"
                f"法人：外資連買{f_streak}天 | 投信連買{s_streak}天\n"
                f"數據：毛利 {margin*100:.1f}% | EPS {eps:.2f}\n"
                f"現價：{curr_p:.2f} | 量比：{vol_ratio:.1f}\n"
                f"-----------------------------------"
            )
    except: return None

def main():
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    # 掃描前 100 檔或特定產業以節省 API 額度，此處示範掃描全市場前 300 檔
    target_list = stock_df[stock_df['stock_id'].str.len() == 4].head(300) 
    
    results = []
    for _, row in target_list.iterrows():
        t = f"{row['stock_id']}{'.TWO' if '上櫃' in str(row['market_type']) else '.TW'}"
        res = analyze_stock_v6(t, {'name': row['stock_name']})
        if res: results.append(res)
        time.sleep(0.5) # 避開 FinMind 頻率限制

    if results:
        header = f"🔍 【{datetime.date.today()} 法人連續加碼清單】"
        for i in range(0, len(results), 5):
            send_line_message(f"{header}\n\n" + "\n".join(results[i:i+5]))
    
if __name__ == "__main__":
    main()
