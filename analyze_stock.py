import os
import yfinance as yf
import pandas as pd
import requests
import datetime
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

def get_diagnostic_report(sid):
    try:
        # 1. 技術面與基本資料 (yfinance)
        stock = yf.Ticker(sid)
        df = stock.history(period="3mo")
        if df.empty: return f"❌ 找不到 {sid} 的資料"
        
        info = stock.info
        name = info.get('shortName', sid)
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 技術指標計算
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        vol_ratio = latest['Volume'] / df['Volume'].iloc[-11:-1].mean()
        change_pct = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
        
        # 2. 籌碼面 (FinMind - 近5日法人買賣)
        dl = DataLoader()
        end_date = datetime.date.today().strftime('%Y-%m-%d')
        start_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime('%Y-%m-%d')
        
        # 獲取法人買賣超 (去掉 .TW/.TWO 進行查詢)
        stock_id_only = sid.split('.')[0]
        chip_df = dl.taiwan_stock_institutional_investors(stock_id=stock_id_only, start_date=start_date)
        
        foreign_buy = 0
        trust_buy = 0
        if not chip_df.empty:
            # 統計最近 5 個交易日的累積張數
            recent_chip = chip_df.tail(15) # 取足量數據過濾
            foreign_buy = int(recent_chip[recent_chip['name'] == 'Foreign_Investor']['buy'].sum() - recent_chip[recent_chip['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
            trust_buy = int(recent_chip[recent_chip['name'] == 'Investment_Trust']['buy'].sum() - recent_chip[recent_chip['name'] == 'Investment_Trust']['sell'].sum()) / 1000

        # 3. 格式化輸出
        pe = info.get('trailingPE', 0)
        pe_status = "合理偏高" if pe > 22 else "合理"
        
        report = (
            f"=== {sid} ({name}) 診斷報告 ===\n\n"
            f"【籌碼面：大戶力道】(近5日)\n"
            f"● 外資: {int(foreign_buy)} 張 ({'🔴加碼' if foreign_buy>0 else '🟢減碼'})\n"
            f"● 投信: {int(trust_buy)} 張 ({'🔴加碼' if trust_buy>0 else '🟢減碼'})\n\n"
            f"【基本面：成長力道】\n"
            f"● 本益比 (P/E): {round(pe, 2) if pe else 'N/A'} ({pe_status})\n\n"
            f"【技術面：進場時機】\n"
            f"● 目前股價: {latest['Close']:.2f} ({'+' if change_pct>0 else ''}{change_pct:.2f}%)\n"
            f"● 心理力道: RSI={rsi:.2f}\n"
            f"● 量能倍率: {vol_ratio:.2f} 倍\n"
            f"======================================="
        )
        return report
    except Exception as e:
        return f"❌ {sid} 診斷出錯: {e}"

if __name__ == "__main__":
    targets = ["2330.TW", "2317.TW", "2454.TW"] # 在此修改代碼
    for t in targets:
        send_line_message(get_diagnostic_report(t))
        time.sleep(1) # 避免 LINE API 過載
