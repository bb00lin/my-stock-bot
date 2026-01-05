import os
import yfinance as yf
import pandas as pd
import requests
import time
import datetime
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

# ==========================================
# 1. 環境設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"

def send_line_message(message):
    if not LINE_ACCESS_TOKEN:
        print(message)
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

# ==========================================
# 2. 核心分析引擎 (支持雙模式)
# ==========================================
def analyze_stock_smart(ticker, industry, mode="NORMAL"):
    """
    mode="NORMAL": 強勢盤模式 (高量比、高門檻)
    mode="WEAK":   弱勢盤模式 (低量轉折、抗跌)
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y", progress=False)
        if len(df) < 60: return None
        if df.iloc[-1]['Volume'] == 0: df = df.iloc[:-1]
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        curr_p = latest['Close']
        
        # 指標計算
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        ma20 = SMAIndicator(df['Close'], 20).sma_indicator().iloc[-1]
        ma60 = SMAIndicator(df['Close'], 60).sma_indicator().iloc[-1]
        vol_ratio = latest['Volume'] / df['Volume'].iloc[-11:-1].mean()
        
        signals = []
        is_potential = False
        
        # --- 模式 A: 強勢盤模式 ---
        if mode == "NORMAL":
            # 條件：爆量(1.5倍) + 大量(1000張) + RSI轉強
            if vol_ratio > 1.5 and latest['Volume'] >= 1000000 and curr_p > prev['Close']:
                signals.append("金流爆量")
            if rsi > 50 and curr_p > ma20:
                signals.append("多頭結構")
            is_potential = (len(signals) >= 2) and (curr_p - ma60)/ma60 < 0.20

        # --- 模式 B: 弱勢盤模式 (自動切換) ---
        else:
            # 條件：量比微增(1.1倍) + 守穩月線 + RSI低檔回升
            if vol_ratio > 1.1 and latest['Volume'] >= 400000 and curr_p > prev['Close']:
                signals.append("縮量轉強")
            if abs(curr_p - ma20)/ma20 < 0.02 and curr_p >= prev['Close']:
                signals.append("逆勢抗跌")
            is_potential = (len(signals) >= 2) and (curr_p - ma60)/ma60 < 0.15

        if is_potential:
            high_1y = df['High'].max()
            stop_loss = ma60 * 0.97
            mode_tag = "🔥 強勢模式" if mode == "NORMAL" else "🛡️ 弱勢抗跌模式"
            
            msg = (
                f"📍{ticker} [{industry}] ({mode_tag})\n"
                f"現價: {curr_p:.2f} ({((curr_p/prev['Close'])-1)*100:+.1f}%)\n"
                f"量比: {vol_ratio:.2f} / RSI: {rsi:.1f}\n"
                f"訊號: {'/'.join(signals)}\n"
                f"【實戰指引】\n"
                f"● 壓力：{high_1y:.1f} / 支撐：{ma60:.1f}\n"
                f"● 停損建議：{stop_loss:.1f}"
            )
            return msg
        return None
    except:
        return None

# ==========================================
# 3. 主程序邏輯
# ==========================================
def main():
    print("🚀 啟動智能環境感知掃描...")
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    stock_map = {f"{row['stock_id']}{'.TWO' if '上櫃' in str(row.get('market_type','')) else '.TW'}": row.get('industry_category','股票') 
                 for _, row in stock_df.iterrows() if len(str(row['stock_id'])) == 4}

    # 第一輪：強勢盤掃描
    print("正在執行第一輪：強勢盤掃描...")
    results = []
    for ticker, industry in stock_map.items():
        res = analyze_stock_smart(ticker, industry, mode="NORMAL")
        if res: results.append(res)
        time.sleep(0.02)

    current_mode = "強勢盤模式"
    
    # 環境判定：如果沒股票，切換到弱勢盤模式
    if len(results) < 3:
        print("市場氛圍偏弱，切換至『弱勢盤模式』重新掃描...")
        results = []
        current_mode = "弱勢盤模式 (自動切換)"
        for ticker, industry in stock_map.items():
            res = analyze_stock_smart(ticker, industry, mode="WEAK")
            if res: results.append(res)
            time.sleep(0.02)

    # 發送結果
    if results:
        for i in range(0, len(results), 5):
            chunk = results[i:i+5]
            msg = f"🔍 【台股智能掃描報告 - {current_mode}】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
    else:
        send_line_message(f"📊 掃描完成。目前市場極度低迷，兩大模式均未發現安全標的，建議空手。")

if __name__ == "__main__":
    main()
