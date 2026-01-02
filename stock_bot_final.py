import os
import yfinance as yf
import pandas as pd
import requests
import time
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD

# 1. 設定 LINE 參數
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, headers=headers, json=payload)
    except: pass

def get_stock_info_map():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        stock_map = {}
        # 彈性偵測市場欄位
        m_col = 'market_type' if 'market_type' in df.columns else ('category' if 'category' in df.columns else None)
        for _, row in df.iterrows():
            sid = str(row['stock_id'])
            if 4 <= len(sid) <= 5:
                suffix = ".TW"
                if m_col and str(row[m_col]) in ['上櫃', '誠信上櫃', 'OTC']:
                    suffix = ".TWO"
                stock_map[f"{sid}{suffix}"] = row.get('industry_category', '股票')
        print(f"✅ 成功獲取清單，共 {len(stock_map)} 檔股票")
        return stock_map
    except Exception as e:
        print(f"❌ 獲取清單失敗: {e}")
        return {"2330.TW": "半導體業"}

def analyze_stock(ticker, industry):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="6mo", progress=False)
        if len(df) < 60: return None
        latest = df.iloc[-1]
        # 門檻：股價>15, 成交量>500張
        if latest['Close'] < 15 or latest['Volume'] < 500000: return None
        
        close = df['Close']
        df['RSI'] = RSIIndicator(close).rsi()
        df['MA5'] = SMAIndicator(close, 5).sma_indicator()
        df['MA20'] = SMAIndicator(close, 20).sma_indicator()
        df['MA60'] = SMAIndicator(close, 60).sma_indicator()
        df['MACD_Hist'] = MACD(close).macd_diff()

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        signals = []
        if latest['MA5'] > latest['MA20'] > latest['MA60']: signals.append("🔥多頭")
        if prev['MACD_Hist'] < 0 and latest['MACD_Hist'] > 0: signals.append("✨MACD")
        if prev['RSI'] < 40 and latest['RSI'] > 40: signals.append("🚀RSI反彈")
        
        if len(signals) >= 2:
            vol = int(latest['Volume'] / 1000)
            return f"📍{ticker} [{industry}]\n現價: {round(latest['Close'], 2)}\n張數: {vol}張\n訊號: {'/'.join(signals)}"
        return None
    except: return None

def main():
    print("🚀 啟動全台股實戰掃描模式...")
    stock_map = get_stock_info_map()
    if not stock_map: return
    
    results = []
    total = len(stock_map)
    for i, (ticker, industry) in enumerate(stock_map.items()):
        if i % 100 == 0: print(f"進度: {i}/{total}...")
        res = analyze_stock(ticker, industry)
        if res: results.append(res)
        time.sleep(0.1) # 保護 API
        
    if results:
        for i in range(0, len(results), 5):
            chunk = results[i:i+5]
            msg = "🔍 【台股強勢股掃描報告】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
    else:
        send_line_message("🏁 今日全台股掃描完成，未發現符合強勢條件標的。")
    print("🏁 任務結束")

if __name__ == "__main__":
    main()
