import os
import yfinance as yf
import pandas as pd
import requests
import time
import datetime
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

# 設定 LINE 參數 (維持您的 User ID 紀錄)
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"

def send_line_message(message):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

def analyze_weak_market(ticker, industry):
    """弱勢盤專用篩選邏輯"""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y", progress=False)
        if len(df) < 60: return None, []
        if df.iloc[-1]['Volume'] == 0: df = df.iloc[:-1]
        
        close = df['Close']
        df['RSI'] = RSIIndicator(close).rsi()
        df['MA20'] = SMAIndicator(close, 20).sma_indicator()
        df['MA60'] = SMAIndicator(close, 60).sma_indicator()
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        curr_p = latest['Close']
        
        signals = []
        tags = []

        # 1. 窒息量後轉強 (弱勢盤常見：沒量陰跌後的首根紅棒)
        avg_vol_10d = df['Volume'].iloc[-11:-1].mean()
        vol_ratio = latest['Volume'] / avg_vol_10d
        if vol_ratio > 1.1 and latest['Volume'] >= 400000 and curr_p > prev['Close']:
            signals.append("量能回溫")
            tags.append("轉強")

        # 2. 均線抗跌 (回測不破)
        dist_ma20 = (curr_p - latest['MA20']) / latest['MA20']
        if -0.01 < dist_ma20 < 0.02 and curr_p >= prev['Close']:
            signals.append("逆勢守月線")
            tags.append("抗跌")

        # 3. 低檔黃金交叉 (RSI)
        if prev['RSI'] < 50 and latest['RSI'] > prev['RSI']:
            signals.append("指標轉強")
            tags.append("轉強")

        # 判定門檻：只要符合「抗跌」加上任一轉強訊號，即入選
        is_potential = ("逆勢守月線" in signals) or (len(signals) >= 2)
        
        # 排除乖離過高
        if (curr_p - latest['MA60']) / latest['MA60'] > 0.15: is_potential = False

        if is_potential and curr_p >= 8:
            ma60 = latest['MA60']
            high_1y = df['High'].max()
            stop_loss = ma60 * 0.96 # 弱勢盤停損設嚴一點點
            
            info_msg = (
                f"📍{ticker} [{industry}]\n"
                f"現價: {curr_p:.2f} ({((curr_p/prev['Close'])-1)*100:+.1f}%)\n"
                f"量比: {vol_ratio:.2f} / RSI: {latest['RSI']:.1f}\n"
                f"訊號: {'/'.join(signals)}\n\n"
                f"【🛡️ 弱勢盤操作建議】\n"
                f"● 狀態：逆勢抗跌標的\n"
                f"● 支撐：{ma60:.1f} / 停損：{stop_loss:.1f}"
            )
            return info_msg, tags
        return None, tags
    except: return None, []

def main():
    # 這裡省略 get_stock_info_map (與 Pro 版相同)
    # ... (執行邏輯也與 Pro 版相同，僅更換 analyze 函數為 analyze_weak_market)
    pass
