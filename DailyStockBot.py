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

def analyze_v9(ticker, name):
    """核心篩選邏輯 - 整合量能(🔥)與乖離率警示"""
    try:
        s = yf.Ticker(ticker)
        i = s.info
        m = i.get('grossMargins', 0) or 0
        e = i.get('trailingEps', 0) or 0
        
        # 基本面過濾：毛利 > 10% 且 EPS > 0
        if m < 0.10 or e <= 0: return None

        df = s.history(period="1y")
        if len(df) < 60: return None
        
        cp = df.iloc[-1]['Close']
        ma5 = df['Close'].rolling(5).mean().iloc[-1]   # 5日線
        ma60 = df['Close'].rolling(60).mean().iloc[-1] # 季線
        
        # 計算量比
        vol_today = df.iloc[-1]['Volume']
        vol_avg = df['Volume'].iloc[-11:-1].mean()
        vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0
        
        # 量能標籤
        vol_tag = f"🔥爆量({vol_ratio:.1f}x)" if vol_ratio > 2.0 else f"{vol_ratio:.1f}x"
        
        # 計算 5日線乖離率
        bias_5 = ((cp - ma5) / ma5) * 100
        if bias_5 > 7:
            bias_msg = f"⚠️過熱({bias_5:.1f}%)"
        elif bias_5 < -5:
            bias_msg = f"📉超跌({bias_5:.1f}%)"
        else:
            bias_msg = f"✅安全({bias_5:.1f}%)"

        fs, ss = get_streak_only(ticker.split('.')[0])
        
        # 篩選條件：法人有買 且 股價站上季線 且 量比大於 1.1
        if (fs >= 2 or ss >= 1) and cp > ma60 and vol_ratio > 1.1:
            type_tag = "🌟投信認養" if ss >= 2 else "🔍法人掃貨"
            return (f"📍{ticker} {name} ({type_tag})\n"
                    f"法人：外資{fs}d | 投信{ss}d\n"
                    f"量比：{vol_tag}\n"
                    f"狀態：{bias_msg}\n"
                    f"現價：{cp:.2f}\n"
                    f"-----------------------------------")
    except: return None

def main():
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    
    m_col = 'market_type' if 'market_type' in stock_df.columns else 'type'
    if m_col not in stock_df.columns: m_col = None

    # 掃描權值前 200 檔
    targets = stock_df[stock_df['stock_id'].str.len() == 4].head(200) 
    
    results = []
    for _, row in targets.iterrows():
        sid = row['stock_id']
        if m_col and m_col in row:
            suffix = ".TWO" if '上櫃' in str(row[m_col]) or 'OTC' in str(row[m_col]) else ".TW"
        else:
            suffix = ".TWO" if int(sid) >= 8000 else ".TW"
            
        t = f"{sid}{suffix}"
        res = analyze_v9(t, row['stock_name'])
        if res: results.append(res)
        time.sleep(0.4)

    if results:
        msg = f"🔍 【{datetime.date.today()} 法人精選清單】\n\n" + "\n".join(results)
        send_line(msg)
        
        # 存檔供備查
        today_str = datetime.date.today().strftime('%Y-%m-%d')
        with open(f"scan_report_{today_str}.txt", "w", encoding="utf-8") as f: f.write(msg)
        with open("latest_scan.txt", "w", encoding="utf-8") as f: f.write(msg)
    else:
        print("今日篩選完畢，無符合條件標的。")

if __name__ == "__main__":
    main()
