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
    requests.post(url, headers=headers, json=payload)

def get_stock_list():
    """【正式功能】獲取全台股上市清單"""
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        df = df[df['type'] == 'stock']
        full_list = [f"{sid}.TW" for sid in df['stock_id'].tolist()]
        print(f"✅ 成功獲取清單，共 {len(full_list)} 檔股票")
        return full_list 
    except Exception as e:
        print(f"❌ 清單獲取失敗: {e}")
        return ["2330.TW", "2317.TW", "2454.TW"]

def analyze_stock(ticker_symbol):
    """【正式功能】技術面過濾邏輯"""
    try:
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="6mo")
        if len(df) < 60: return None

        latest = df.iloc[-1]
        
        # --- 門檻過濾：股價 > 20 且 成交張數 > 1000張 (1,000,000股) ---
        # yfinance 的 Volume 單位是「股」
        if latest['Close'] < 20 or latest['Volume'] < 1000000:
            return None

        # --- 技術指標計算 ---
        close = df['Close']
        df['RSI'] = RSIIndicator(close, window=14).rsi()
        df['MA5'] = SMAIndicator(close, window=5).sma_indicator()
        df['MA20'] = SMAIndicator(close, window=20).sma_indicator()
        df['MA60'] = SMAIndicator(close, window=60).sma_indicator()
        macd_obj = MACD(close)
        df['MACD_Hist'] = macd_obj.macd_diff()

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        signals = []
        # A. 均線多頭排列 (強勢趨勢)
        if latest['MA5'] > latest['MA20'] > latest['MA60']:
            signals.append("🔥 多頭排列")
        # B. MACD 黃金交叉 (轉折點)
        if prev['MACD_Hist'] < 0 and latest['MACD_Hist'] > 0:
            signals.append("✨ MACD交叉")
        # C. 量大價昂 (成交量 > 10日均量 1.5倍)
        avg_vol = df['Volume'].iloc[-11:-1].mean()
        if latest['Volume'] > avg_vol * 1.5 and latest['Close'] > prev['Close']:
            signals.append("📊 爆量噴發")

        if signals:
            vol_shares = int(latest['Volume'] / 1000)
            return f"📍{ticker_symbol}\n現價: {round(latest['Close'], 1)}\n張數: {vol_shares}張\n訊號: {'/'.join(signals)}"
        return None
    except:
        return None

def main():
    print("🚀 啟動全台股正式掃描模式...")
    stocks = get_stock_list()
    results = []
    
    # 掃描全部，並在 Log 顯示進度
    for i, s in enumerate(stocks):
        if i % 100 == 0: print(f"進度: {i}/{len(stocks)}...")
        res = analyze_stock(s)
        if res:
            results.append(res)
        time.sleep(0.1) # 維持小停頓保護 IP
    
    if results:
        # 每 5 檔一則訊息，避免 LINE 訊息太長發不出去
        for i in range(0, len(results), 5):
            chunk = results[i:i+5]
            msg = "🔍 【台股強勢股掃描報告】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
    else:
        send_line_message("🏁 今日全台股掃描完成，未發現同時符合「低門檻」與「強勢指標」標的。")

if __name__ == "__main__":
    main()
