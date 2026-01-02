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

def get_stock_info_map():
    """自動區分上市(.TW)與上櫃(.TWO)"""
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        stock_map = {}
        for _, row in df.iterrows():
            sid = row['stock_id']
            # 只取 4 碼（普通股）或 5 碼（KY股），排除 6 碼（權證）
            if 4 <= len(sid) <= 5:
                # 判斷市場類型
                suffix = ".TWO" if row['market_type'] in ['上櫃', '誠信上櫃'] else ".TW"
                stock_map[f"{sid}{suffix}"] = row['industry_category']
        print(f"✅ 成功獲取清單，共 {len(stock_map)} 檔股票")
        return stock_map
    except Exception as e:
        print(f"❌ 獲取清單失敗: {e}")
        return {"2330.TW": "半導體業"}

def analyze_stock(ticker_symbol, industry_name):
    try:
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="6mo")
        if len(df) < 60: return None

        latest = df.iloc[-1]
        # 門檻調整：股價 > 15，成交量 > 500張 (500,000股)
        if latest['Close'] < 15 or latest['Volume'] < 500000:
            return None

        # 計算指標... (維持原樣)
        
        # 靈敏度調整：只要符合「2項」以上訊號就報出
        if len(signals) >= 2:
            vol_shares = int(latest['Volume'] / 1000)
            return f"📍{ticker_symbol} [{industry_name}]\n現價: {round(latest['Close'], 1)}\n張數: {vol_shares}張\n訊號: {'/'.join(signals)}"
        return None
    except:
        return None

def analyze_stock(ticker_symbol, industry_name):
    """技術面過濾邏輯 + 加入產業資訊"""
    try:
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="6mo")
        if len(df) < 60: return None

        latest = df.iloc[-1]
        
        # 門檻過濾：股價 > 20 且 成交張數 > 1000張
        if latest['Close'] < 20 or latest['Volume'] < 1000000:
            return None

        # 技術指標計算
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
        if latest['MA5'] > latest['MA20'] > latest['MA60']:
            signals.append("🔥多頭")
        if prev['MACD_Hist'] < 0 and latest['MACD_Hist'] > 0:
            signals.append("✨MACD")
        
        # 量大價昂 (成交量 > 10日均量 1.5倍)
        avg_vol = df['Volume'].iloc[-11:-1].mean()
        if latest['Volume'] > avg_vol * 1.5 and latest['Close'] > prev['Close']:
            signals.append("📊爆量")

        # 為了避免完全沒訊號，只要符合任一項就報出 (你也可以改成 len(signals) >= 2 變嚴格)
        if len(signals) >= 1:
            vol_shares = int(latest['Volume'] / 1000)
            return f"📍{ticker_symbol} [{industry_name}]\n現價: {round(latest['Close'], 1)}\n張數: {vol_shares}張\n訊號: {'/'.join(signals)}"
        return None
    except:
        return None

def main():
    print("🚀 啟動全台股產業掃描模式...")
    # 1. 先抓取產業地圖
    stock_map = get_stock_info_map()
    results = []
    
    # 2. 開始掃描
    for i, (ticker, industry) in enumerate(stock_map.items()):
        if i % 100 == 0: print(f"進度: {i}/{len(stock_map)}...")
        
        res = analyze_stock(ticker, industry)
        if res:
            results.append(res)
        time.sleep(0.1)
    
    # 3. 發送訊息
    if results:
        for i in range(0, len(results), 5):
            chunk = results[i:i+5]
            msg = "🔍 【台股族群強勢股掃描】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
    else:
        send_line_message("🏁 今日全台股掃描完成，未發現符合強勢條件標的。")

if __name__ == "__main__":
    main()
