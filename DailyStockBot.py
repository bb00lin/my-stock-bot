import os, yfinance as yf, pandas as pd, requests, time, datetime
import numpy as np
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

def calculate_indicators(df):
    """計算 RSI 與 KD 指標"""
    close = df['Close']
    low_min = df['Low'].rolling(window=9).min()
    high_max = df['High'].rolling(window=9).max()
    
    # 計算 RSI (14)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # 計算 KD (9, 3, 3)
    rsv = (close - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2).mean() # 類似傳統 DEMA 平滑
    d = k.ewm(com=2).mean()
    
    return rsi, k, d

def analyze_v12(ticker, name):
    """核心篩選邏輯 - 1000檔 | 重複過濾 | KD & RSI & 乖離 & 量能"""
    try:
        s = yf.Ticker(ticker)
        i = s.info
        m = i.get('grossMargins', 0) or 0
        e = i.get('trailingEps', 0) or 0
        if m < 0.10 or e <= 0: return None

        df = s.history(period="1y")
        if len(df) < 60: return None
        
        cp = df.iloc[-1]['Close']
        ma5 = df['Close'].rolling(5).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        
        # 1. 指標計算
        rsi_series, k_series, d_series = calculate_indicators(df)
        rsi_val = rsi_series.iloc[-1]
        k_val = k_series.iloc[-1]
        d_val = d_series.iloc[-1]
        
        # 2. 量能診斷
        vol_today = df.iloc[-1]['Volume']
        vol_avg = df['Volume'].iloc[-11:-1].mean()
        vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0
        vol_tag = f"🔥爆量({vol_ratio:.1f}x)" if vol_ratio > 2.0 else f"{vol_ratio:.1f}x"
        
        # 3. 狀態標籤
        bias_5 = ((cp - ma5) / ma5) * 100
        kd_status = "高檔" if k_val > 80 else ("低檔" if k_val < 20 else "穩定")
        
        if bias_5 > 7 or rsi_val > 75 or k_val > 85:
            status_msg = f"⚠️過熱(乖離{bias_5:.1f}%|RSI:{rsi_val:.0f}|K:{k_val:.0f})"
        else:
            status_msg = f"✅安全(乖離{bias_5:.1f}%|RSI:{rsi_val:.0f}|K:{k_val:.0f})"

        # 4. 籌碼篩選
        fs, ss = get_streak_only(ticker.split('.')[0])
        
        if (fs >= 2 or ss >= 1) and cp > ma60 and vol_ratio > 1.1:
            type_tag = "🌟投信認養" if ss >= 2 else "🔍法人掃貨"
            return (f"📍{ticker} {name} ({type_tag})\n"
                    f"法人：外資{fs}d | 投信{ss}d\n"
                    f"量比：{vol_tag}\n"
                    f"狀態：{status_msg} [{kd_status}]\n"
                    f"現價：{cp:.2f}\n"
                    f"-----------------------------------")
    except: return None

def main():
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    m_col = 'market_type' if 'market_type' in stock_df.columns else 'type'
    
    # 擴大掃描至前 1000 檔
    targets = stock_df[stock_df['stock_id'].str.len() == 4].head(1000) 
    
    results = []
    seen_ids = set()
    print(f"啟動旗艦級掃描 (1000檔)... 預計耗時 25 分鐘")
    
    for _, row in targets.iterrows():
        sid = row['stock_id']
        if sid in seen_ids: continue
        seen_ids.add(sid)
        
        if m_col and m_col in row:
            suffix = ".TWO" if '上櫃' in str(row[m_col]) or 'OTC' in str(row[m_col]) else ".TW"
        else:
            suffix = ".TWO" if int(sid) >= 8000 else ".TW"
            
        t = f"{sid}{suffix}"
        res = analyze_v12(t, row['stock_name'])
        if res: results.append(res)
        time.sleep(0.4)

    if results:
        msg = f"🔍 【{datetime.date.today()} 法人精選(1000檔規模)】\n\n" + "\n".join(results)
        send_line(msg)
        with open("latest_scan.txt", "w", encoding="utf-8") as f: f.write(msg)
    else:
        print("今日無符合標的。")

if __name__ == "__main__":
    main()
