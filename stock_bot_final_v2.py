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
# 2. 核心分析引擎 (V3.1 靈敏度調整)
# ==========================================
def analyze_stock_smart_v3_1(ticker, industry, mode="NORMAL"):
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="1y", progress=False)
        if len(df) < 60: return None
        if df.iloc[-1]['Volume'] == 0: df = df.iloc[:-1]
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        curr_p = latest['Close']
        
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        ma20 = SMAIndicator(df['Close'], 20).sma_indicator().iloc[-1]
        ma60 = SMAIndicator(df['Close'], 60).sma_indicator().iloc[-1]
        vol_ratio = latest['Volume'] / df['Volume'].iloc[-11:-1].mean()
        
        is_potential = False
        tag = ""

        # --- A. 強勢模式 (放寬門檻) ---
        if mode == "NORMAL":
            # 降低至 500 張, 1.2 倍量
            if vol_ratio > 1.2 and latest['Volume'] >= 500000 and curr_p > prev['Close']:
                tag = "🔥 強勢攻擊"
                # 放寬乖離率至 30%
                is_potential = (curr_p > ma20) and (curr_p - ma60)/ma60 < 0.30

        # --- B. 弱勢抗跌模式 (維持穩定) ---
        elif mode == "WEAK":
            if abs(curr_p - ma20)/ma20 < 0.025 and curr_p >= prev['Close'] and latest['Volume'] >= 300000:
                tag = "🛡️ 逆勢支撐"
                is_potential = True

        # --- C. 避險/破位模式 (偵測破位) ---
        elif mode == "RISK":
            if curr_p < ma60 and prev['Close'] >= ma60:
                tag = "⚠️ 趨勢破線"
                is_potential = True
            elif rsi < 35 and vol_ratio > 1.1:
                tag = "📉 弱勢盤整"
                is_potential = True

        if is_potential:
            bias = ((curr_p-ma60)/ma60)*100
            msg = (
                f"📍{ticker} [{industry}] ({tag})\n"
                f"現價: {curr_p:.2f} ({((curr_p/prev['Close'])-1)*100:+.1f}%)\n"
                f"RSI: {rsi:.1f} / 60MA乖離: {bias:+.1f}%\n"
                f"{'【警示】高檔乖離大，謹慎追高' if bias > 20 else '【指引】趨勢架構尚穩'}"
            )
            return msg
        return None
    except: return None

# ==========================================
# 3. 主程序邏輯
# ==========================================
def main():
    print("🚀 啟動 V3.1 靈敏度優化版掃描...")
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    stock_map = {f"{row['stock_id']}{'.TWO' if '上櫃' in str(row.get('market_type','')) else '.TW'}": row.get('industry_category','股票') 
                 for _, row in stock_df.iterrows() if len(str(row['stock_id'])) == 4}

    # 依次執行模式
    for mode_name, mode_key in [("強勢模式", "NORMAL"), ("弱勢抗跌模式", "WEAK"), ("避險/破位模式", "RISK")]:
        print(f"正在執行：{mode_name}...")
        results = []
        for ticker, industry in stock_map.items():
            res = analyze_stock_smart_v3_1(ticker, industry, mode=mode_key)
            if res: results.append(res)
            time.sleep(0.01)
        
        if results:
            msg_header = f"🔍 【V3.1 掃描報告 - {mode_name}】"
            # 每次發送最多 5 檔，避免 LINE 訊息過長
            for i in range(0, len(results), 5):
                chunk = results[i:i+5]
                send_line_message(f"{msg_header}\n\n" + "\n---\n".join(chunk))
            
            # 如果強勢模式有東西，就不用跑後面了
            if mode_key != "RISK": return 
        
    if not results:
        send_line_message("📊 市場極度枯竭，V3.1 仍未發現適合標的。")

if __name__ == "__main__":
    main()
