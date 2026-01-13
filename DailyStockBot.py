import os, yfinance as yf, pandas as pd, requests, time, datetime
from FinMind.data import DataLoader

# ==========================================
# 設定與環境變數
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"

def send_line(msg):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
    try: requests.post(url, headers=headers, json=payload)
    except: pass

def get_streak_only(sid_clean):
    """獲取法人連買天數"""
    try:
        dl = DataLoader()
        start = (datetime.date.today() - datetime.timedelta(days=20)).strftime('%Y-%m-%d')
        df = dl.taiwan_stock_institutional_investors(stock_id=sid_clean, start_date=start)
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

def analyze_v7(ticker, name):
    """核心篩選邏輯"""
    try:
        s = yf.Ticker(ticker)
        i = s.info
        m = i.get('grossMargins', 0) or 0
        e = i.get('trailingEps', 0) or 0
        
        # 門檻：毛利 > 10% 且 EPS > 0
        if m < 0.10 or e <= 0: return None

        df = s.history(period="1y")
        if len(df) < 60: return None
        
        cp = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        vol_ratio = df.iloc[-1]['Volume'] / df['Volume'].iloc[-11:-1].mean()
        
        fs, ss = get_streak_only(ticker.split('.')[0])
        
        # 條件：法人連買 且 股價站上 MA60 且 有量能
        if (fs >= 2 or ss >= 1) and cp > ma60 and vol_ratio > 1.1:
            tag = "🌟投信認養" if ss >= 2 else "🔍法人掃貨"
            return (f"📍{ticker} {name} ({tag})\n"
                    f"法人：外資連買{fs}d | 投信連買{ss}d\n"
                    f"現價：{cp:.2f} | 量比：{vol_ratio:.1f}\n"
                    f"-----------------------------------")
    except: return None

def main():
    dl = DataLoader()
    # 獲取上市櫃股票清單
    stock_df = dl.taiwan_stock_info()
    # 優先掃描市值較大的前 200 檔以節省 GitHub 執行時間
    targets = stock_df[stock_df['stock_id'].str.len() == 4].head(200) 
    
    results = []
    for _, row in targets.iterrows():
        t = f"{row['stock_id']}{'.TWO' if '上櫃' in str(row['market_type']) else '.TW'}"
        res = analyze_v7(t, row['stock_name'])
        if res: results.append(res)
        time.sleep(0.5)

    if results:
        msg = f"🔍 【{datetime.date.today()} 法人精選清單】\n\n" + "\n".join(results)
        send_line(msg)
        
        # 存檔供 GitHub Artifacts 下載
        fname = f"scan_report_{datetime.date.today()}.txt"
        with open(fname, "w", encoding="utf-8") as f: f.write(msg)
        with open("latest_scan.txt", "w", encoding="utf-8") as f: f.write(msg)

if __name__ == "__main__":
    main()
