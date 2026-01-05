import os
import yfinance as yf
import pandas as pd
import requests
import time
import datetime
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

# 1. 設定環境參數
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c" # 已根據您的紀錄設定

def send_line_message(message):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try: requests.post(url, headers=headers, json=payload)
    except: pass

def get_stock_info_map():
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        stock_map = {}
        m_col = 'market_type' if 'market_type' in df.columns else ('category' if 'category' in df.columns else None)
        for _, row in df.iterrows():
            sid = str(row['stock_id'])
            if 4 <= len(sid) <= 5:
                suffix = ".TWO" if m_col and str(row[m_col]) in ['上櫃', 'OTC'] else ".TW"
                stock_map[f"{sid}{suffix}"] = row.get('industry_category', '股票')
        return stock_map
    except: return {"2330.TW": "半導體"}

def analyze_pro(ticker, industry):
    """整合深度診斷的掃描函數"""
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
        ma60 = latest['MA60']
        rsi = latest['RSI']
        
        # --- 潛力篩選邏輯 ---
        signals = []
        tags = []
        # 1. 底部轉強
        if prev['RSI'] < 45 and rsi > prev['RSI']: signals.append("底部轉強"); tags.append("轉強")
        # 2. 回測月線
        dist_ma20 = (curr_p - latest['MA20']) / latest['MA20']
        if 0 < dist_ma20 < 0.025 and curr_p > prev['Close']: signals.append("回測月線"); tags.append("支撐")
        # 3. 金流爆量
        avg_vol = df['Volume'].iloc[-11:-1].mean()
        vol_ratio = latest['Volume'] / avg_vol
        if vol_ratio > 1.5 and latest['Volume'] > 1000000: signals.append("金流湧入"); tags.append("爆量")

        # 判定是否值得推薦
        is_hit = (len(signals) >= 2) or ("金流湧入" in signals and curr_p > prev['Close'])
        # 排除過熱
        bias_60 = (curr_p - ma60) / ma60
        if bias_60 > 0.20: is_hit = False

        if is_hit and curr_p >= 10:
            # 計算戰略數據
            high_1y = df['High'].max()
            stop_loss = ma60 * 0.97
            action = "🟡 支撐區佈局" if bias_60 < 0.07 else "🔥 強勢跟進"
            
            # 籌碼簡易抓取 (當前 turn)
            info_msg = f"📍{ticker} [{industry}]\n現價: {curr_p:.2f} ({((curr_p/prev['Close'])-1)*100:+.1f}%)\n量比: {vol_ratio:.1f} / RSI: {rsi:.1f}\n訊號: {'/'.join(signals)}\n\n【🚀 戰略指引】\n● 建議：{action}\n● 壓力：{high_1y:.1f}\n● 支撐：{ma60:.1f}\n● 停損：{stop_loss:.1f}"
            return info_msg, tags
        return None, tags
    except: return None, []

def main():
    print(f"🚀 啟動 Pro 級全台股潛力掃描...")
    stock_map = get_stock_info_map()
    results = []
    stats = {"轉強": 0, "支撐": 0, "爆量": 0, "總掃描": 0}
    
    total = len(stock_map)
    for i, (ticker, industry) in enumerate(stock_map.items()):
        if i % 100 == 0: print(f"進度: {i}/{total}...")
        res_msg, tags = analyze_pro(ticker, industry)
        stats["總掃描"] += 1
        for t in tags: stats[t] += 1
        if res_msg: results.append(res_msg)
        time.sleep(0.05)
    
    if results:
        # 每一檔發一則詳細報告，或 3 檔一組避免訊息太長
        for i in range(0, len(results), 3):
            chunk = results[i:i+3]
            msg = "🔍 【Pro級掃描：潛力個股與戰略建議】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
    
    summary = (
        f"📊 【市場結構掃描完成】\n"
        f"✅ 總掃描：{stats['總掃描']} 檔\n"
        f"🌟 底部轉強：{stats['轉強']} 檔\n"
        f"🛡️ 回測支撐：{stats['支撐']} 檔\n"
        f"💥 金流異動：{stats['爆量']} 檔\n\n"
        f"💡 建議：優先挑選符合「支撐區佈局」且量比 > 1.5 的標的。"
    )
    send_line_message(summary)
    print("🏁 掃描結束")

if __name__ == "__main__":
    main()
