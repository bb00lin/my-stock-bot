import os
import yfinance as yf
import pandas as pd
import requests
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

# 設定 LINE 參數 (從 Secret 讀取)
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

def get_diagnostic(ticker_symbol):
    try:
        # 1. 技術面與公司名稱 (yfinance)
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="6mo")
        if df.empty: return f"❌ 找不到 {ticker_symbol} 的資料"
        
        # 取得中文名稱 (yfinance 有時只有英文，若無則顯示代碼)
        info = stock.info
        name = info.get('shortName', ticker_symbol)
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        close = df['Close']
        
        # 計算指標
        rsi = RSIIndicator(close).rsi().iloc[-1]
        ma5 = SMAIndicator(close, 5).sma_indicator().iloc[-1]
        ma20 = SMAIndicator(close, 20).sma_indicator().iloc[-1]
        vol_ratio = latest['Volume'] / df['Volume'].iloc[-11:-1].mean()
        change_pct = ((latest['Close'] - prev['Close']) / prev['Close']) * 100

        # 2. 籌碼面與基本面 (FinMind)
        # 這裡示範抓取本益比與營收 (簡化版)
        pe_ratio = info.get('trailingPE', 'N/A')
        if pe_ratio != 'N/A':
            pe_status = "合理偏低" if pe_ratio < 15 else ("合理" if pe_ratio < 22 else "合理偏高")
        else:
            pe_status = "數據不足"

        # 3. 格式化報告
        report = (
            f"=== {ticker_symbol} ({name}) 診斷報告 ===\n\n"
            f"【基本面】\n"
            f"● 本益比 (P/E): {pe_ratio} ({pe_status})\n\n"
            f"【技術面】\n"
            f"● 目前股價: {latest['Close']:.2f} ({'+' if change_pct>0 else ''}{change_pct:.2f}%)\n"
            f"● 均線支撐: MA5={ma5:.2f} / MA20={ma20:.2f}\n"
            f"● 心理力道: RSI={rsi:.2f}\n"
            f"● 量能倍率: {vol_ratio:.2f} 倍\n"
            f"======================================="
        )
        return report
    except Exception as e:
        return f"❌ {ticker_symbol} 診斷出錯: {e}"

if __name__ == "__main__":
    # 這裡你可以手動修改想診斷的代碼清單
    targets = ["2330.TW", "2317.TW", "2454.TW"]
    
    full_report = ""
    for t in targets:
        full_report += get_diagnostic(t) + "\n\n"
    
    send_line_message(full_report)
    print("✅ 診斷報告已發送至 LINE")
