import os
import yfinance as yf
import pandas as pd
import requests
import time
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator, MACD

# 1. 設定 LINE 通知參數
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    """傳送訊息到 LINE"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("❌ Error: LINE Secrets 未設定")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        response = requests.post(url, headers=headers, json=payload)
        print(f"📡 LINE 回應狀態: {response.status_code}")
    except Exception as e:
        print(f"❌ LINE 傳送異常: {e}")

def get_stock_list():
    """獲取少量清單進行測試"""
    try:
        print("🔍 正在獲取測試股票清單...")
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        df = df[df['type'] == 'stock']
        full_list = [f"{sid}.TW" for sid in df['stock_id'].tolist()]
        # 【測試專用】僅取前 10 檔，確保執行速度
        return full_list[:10]
    except Exception as e:
        print(f"❌ 獲取清單失敗: {e}")
        return ["2330.TW", "2317.TW"]

def analyze_stock_test(ticker_symbol):
    """測試版選股：極低門檻"""
    try:
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="3mo")
        if len(df) < 20: 
            print(f"⏩ {ticker_symbol}: 資料不足跳過")
            return None

        # --- 計算指標 ---
        close = df['Close']
        df['RSI'] = RSIIndicator(close, window=14).rsi()
        df['MA5'] = SMAIndicator(close, window=5).sma_indicator()
        df['MA20'] = SMAIndicator(close, window=20).sma_indicator()

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        signals = []
        # --- 測試用：只要符合一項即觸發 ---
        if latest['Close'] > prev['Close']: signals.append("📈 今日上漲")
        if latest['RSI'] > 50: signals.append("👍 RSI 強勢區")
        if latest['MA5'] > latest['MA20']: signals.append("✅ 短均在長均上")

        # 只要有任何訊號就回傳
        if signals:
            vol_shares = int(latest['Volume'] / 1000)
            return f"股票: {ticker_symbol}\n現價: {round(latest['Close'], 2)}\n張數: {vol_shares}張\n訊號: {'、'.join(signals)}"
        return None
    except Exception as e:
        print(f"❌ 分析 {ticker_symbol} 發生錯誤: {e}")
        return None

def main():
    print("🚀 啟動測試模式...")
    stocks = get_stock_list()
    results = []
    
    for s in stocks:
        print(f"正在檢查: {s}...")
        res = analyze_stock_test(s)
        if res:
            results.append(res)
        time.sleep(1) # 測試時慢慢跑
    
    if results:
        header = "🧪 【機器人功能測試 - 成功連線】\n"
        body = "\n---\n".join(results)
        send_line_message(header + body)
        print(f"✅ 測試完成，發送了 {len(results)} 檔標的")
    else:
        send_line_message("🧪 測試完成，但前 10 檔股票均未符合測試訊號。")

if __name__ == "__main__":
    main()
