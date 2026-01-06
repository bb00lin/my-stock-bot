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
# 已根據您的紀錄設定 LINE USER ID
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
# 2. 核心分析引擎 (加入中文名與產業整合)
# ==========================================
def analyze_stock_smart_v3_1(ticker, stock_info, mode="NORMAL"):
    """
    ticker: 股票代碼 (e.g., 2330.TW)
    stock_info: 包含 {'name': '台積電', 'industry': '半導體'} 的字典
    """
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

        # --- A. 強勢模式 (靈敏版) ---
        if mode == "NORMAL":
            if vol_ratio > 1.2 and latest['Volume'] >= 500000 and curr_p > prev['Close']:
                tag = "🔥 強勢攻擊"
                is_potential = (curr_p > ma20) and (curr_p - ma60)/ma60 < 0.30

        # --- B. 弱勢抗跌模式 ---
        elif mode == "WEAK":
            if abs(curr_p - ma20)/ma20 < 0.025 and curr_p >= prev['Close'] and latest['Volume'] >= 300000:
                tag = "🛡️ 逆勢支撐"
                is_potential = True

        # --- C. 避險/破位模式 ---
        elif mode == "RISK":
            if curr_p < ma60 and prev['Close'] >= ma60:
                tag = "⚠️ 趨勢破線"
                is_potential = True
            elif rsi < 35 and vol_ratio > 1.1:
                tag = "📉 弱勢盤整"
                is_potential = True

        if is_potential:
            bias = ((curr_p-ma60)/ma60)*100
            # 格式化輸出：顯示 中文名稱 與 產業
            msg = (
                f"📍{ticker} {stock_info['name']}\n"
                f"產業：[{stock_info['industry']}]\n"
                f"狀態：({tag})\n"
                f"現價：{curr_p:.2f} ({((curr_p/prev['Close'])-1)*100:+.1f}%)\n"
                f"RSI：{rsi:.1f} / 60MA乖離：{bias:+.1f}%\n"
                f"{'【警示】高檔乖離大，謹慎追高' if bias > 20 else '【指引】趨勢架構尚穩'}"
            )
            return msg
        return None
    except: return None

# ==========================================
# 3. 主程序邏輯
# ==========================================
def main():
    print("🚀 啟動 DailyStockBot 智能全市場掃描...")
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    
    # 建立強大的 stock_map，預存名稱與產業別
    # 結構: { '2330.TW': {'name': '台積電', 'industry': '半導體'} }
    stock_map = {}
    for _, row in stock_df.iterrows():
        sid = str(row['stock_id'])
        if len(sid) == 4: # 只掃描 4 碼普通股
            suffix = '.TWO' if '上櫃' in str(row.get('market_type', '')) else '.TW'
            ticker = f"{sid}{suffix}"
            stock_map[ticker] = {
                'name': row.get('stock_name', sid),
                'industry': row.get('industry_category', '股票')
            }

    # 依次執行模式
    for mode_name, mode_key in [("強勢模式", "NORMAL"), ("弱勢抗跌模式", "WEAK"), ("避險/破位模式", "RISK")]:
        print(f"正在執行：{mode_name}...")
        results = []
        for ticker, info in stock_map.items():
            res = analyze_stock_smart_v3_1(ticker, info, mode=mode_key)
            if res: results.append(res)
            time.sleep(0.01) # 微小延遲保護 API
        
        if results:
            msg_header = f"🔍 【市場結構掃描 - {mode_name}】"
            # 每次發送最多 5 檔，避免 LINE 訊息過長
            for i in range(0, len(results), 5):
                chunk = results[i:i+5]
                send_line_message(f"{msg_header}\n\n" + "\n---\n".join(chunk))
            
            # 若強勢或抗跌有結果就停止，否則繼續掃描 RISK
            if mode_key != "RISK": return 
        
    if not results:
        send_line_message("📊 掃描完成：市場目前處於低迷狀態，無符合條件標的，建議空手。")

if __name__ == "__main__":
    main()
