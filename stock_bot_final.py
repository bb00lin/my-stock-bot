import os
import yfinance as yf
import pandas as pd
import requests
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD

# 1. 設定 LINE 通知參數 (由 GitHub Secrets 傳入)
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    """傳送訊息到指定的 LINE USER ID"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("Error: LINE_ACCESS_TOKEN or LINE_USER_ID not set.")
        return
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message}]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        print("LINE 訊息傳送成功！")
    else:
        print(f"LINE 傳送失敗: {response.text}")

def get_stock_list():
    """獲取台灣 50 成分股或自訂清單"""
    # 這裡示範幾檔熱門權值股，你也可以透過 FinMind 抓取完整清單
    return ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2881.TW", "2882.TW", "2603.TW"]

def analyze_stock(ticker_symbol):
    """分析單一股票並判斷訊號"""
    try:
        # 抓取最近 6 個月的資料
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="6mo")
        
        if len(df) < 30:
            return None

        # --- 使用 'ta' 庫計算技術指標 ---
        # 1. RSI (14)
        df['RSI'] = RSIIndicator(close=df['Close'], window=14).rsi()
        
        # 2. 均線 (SMA 20)
        df['SMA20'] = SMAIndicator(close=df['Close'], window=20).sma_indicator()
        
        # 3. MACD
        macd_obj = MACD(close=df['Close'])
        df['MACD_Line'] = macd_obj.macd()
        df['MACD_Signal'] = macd_obj.macd_signal()
        df['MACD_Hist'] = macd_obj.macd_diff()

        # 取得最新一筆數據
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        current_price = round(latest['Close'], 2)
        rsi_val = round(latest['RSI'], 2)
        
        # --- 策略判斷邏輯 ---
        signal = ""
        # 策略 A: RSI 低檔超賣
        if rsi_val < 35:
            signal = "🔴 RSI 低檔超賣 (潛在反彈)"
        # 策略 B: MACD 柱狀體轉正 (黃金交叉)
        elif prev['MACD_Hist'] < 0 and latest['MACD_Hist'] > 0:
            signal = "🟢 MACD 黃金交叉"
        
        if signal:
            return f"股票: {ticker_symbol}\n現價: {current_price}\nRSI: {rsi_val}\n訊號: {signal}"
        
        return None

    except Exception as e:
        print(f"分析 {ticker_symbol} 時發生錯誤: {e}")
        return None

def main():
    print("開始執行股票分析...")
    stocks = get_stock_list()
    results = []
    
    for s in stocks:
        print(f"正在分析 {s}...")
        res = analyze_stock(s)
        if res:
            results.append(res)
    
    if results:
        final_msg = "📈 【每日追蹤報告】\n\n" + "\n---\n".join(results)
    else:
        final_msg = "今日市場波動平穩，未觸發特定技術訊號。"
    
    send_line_message(final_msg)

if __name__ == "__main__":
    main()
