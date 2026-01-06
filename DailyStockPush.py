import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time
from FinMind.data import DataLoader

# 1. 基礎設定
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
WATCH_LIST = ["6770", "6706", "6684", "6271", "6269", "3105", "2538", "2014", "2010", "2002", "00992A", "00946"]

# 2. 預先抓取台股名稱對照表 (FinMind)
def get_stock_info_map():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except:
        return {}

STOCK_MAP = get_stock_info_map()

# 3. 核心抓取邏輯
def fetch_data(sid):
    try:
        # 判定市場後綴
        target = f"{sid}.TW"
        stock = yf.Ticker(target)
        df = stock.history(period="7mo")
        if df.empty:
            target = f"{sid}.TWO"
            stock = yf.Ticker(target)
            df = stock.history(period="7mo")
        
        if df.empty: return None
        
        info = stock.info
        curr_p = df['Close'].iloc[-1]
        prev_p = df['Close'].iloc[-2]
        vol = df['Volume'].iloc[-1]
        
        # 基本計算
        amt = (vol * curr_p) / 100_000_000 # 億
        d1 = ((curr_p / prev_p) - 1) * 100
        
        # 抓取中文名與產業
        c_name, industry = STOCK_MAP.get(str(sid), (sid, "其他"))
        
        return (
            f"━━━━━━━━━━━━━━\n"
            f"📍 {sid} {c_name}\n"
            f"產業: [{industry}]\n"
            f"現價: {curr_p:.2f} ({d1:+.2f}%)\n"
            f"今日成交額: {amt:.2f} 億\n"
        )
    except:
        return None

# 4. 執行與發送
def main():
    if not LINE_ACCESS_TOKEN: return
    
    reports = []
    for sid in WATCH_LIST:
        res = fetch_data(sid)
        if res: reports.append(res)
        time.sleep(1)
        
    if reports:
        full_msg = f"🏆 【{datetime.date.today()} 法人金流診斷】\n" + "".join(reports)
        requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={"Authorization": f"Bearer {LINE_ACCESS_TOKEN}", "Content-Type": "application/json"},
            json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": full_msg}]}
        )

if __name__ == "__main__":
    main()
