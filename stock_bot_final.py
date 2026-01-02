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
    try:
        requests.post(url, headers=headers, json=payload)
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
                suffix = ".TWO" if m_col and str(row[m_col]) in ['上櫃', '誠信上櫃', 'OTC'] else ".TW"
                stock_map[f"{sid}{suffix}"] = row.get('industry_category', '股票')
        print(f"✅ 成功獲取清單，共 {len(stock_map)} 檔股票")
        return stock_map
    except Exception as e:
        print(f"❌ 獲取清單失敗: {e}")
        return {"2330.TW": "半導體業"}

def analyze_stock(ticker, industry):
    """回傳 (是否選中標的訊息, 統計標籤清單)"""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="6mo", progress=False)
        if len(df) < 60: return None, []
        
        latest = df.iloc[-1]
        close = df['Close']
        df['RSI'] = RSIIndicator(close).rsi()
        df['MA5'] = SMAIndicator(close, 5).sma_indicator()
        df['MA20'] = SMAIndicator(close, 20).sma_indicator()
        df['MA60'] = SMAIndicator(close, 60).sma_indicator()
        df['MACD_Hist'] = MACD(close).macd_diff()

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # 統計用的標籤
        stat_tags = []
        if latest['MA5'] > latest['MA20'] > latest['MA60']: stat_tags.append("多頭")
        if prev['MACD_Hist'] < 0 and latest['MACD_Hist'] > 0: stat_tags.append("MACD金叉")
        
        # 選股篩選條件 (股價>15, 成交量>500張, 且符合1項以上訊號)
        signals = []
        if "多頭" in stat_tags: signals.append("🔥多頭")
        if "MACD金叉" in stat_tags: signals.append("✨MACD")
        avg_vol = df['Volume'].iloc[-11:-1].mean()
        if latest['Volume'] > avg_vol * 1.5 and latest['Close'] > prev['Close']:
            signals.append("📊爆量")
            stat_tags.append("爆量")

        result_msg = None
        if latest['Close'] >= 15 and latest['Volume'] >= 500000 and len(signals) >= 1:
            vol = int(latest['Volume'] / 1000)
            result_msg = f"📍{ticker} [{industry}]\n現價: {round(latest['Close'], 2)}\n張數: {vol}張\n訊號: {'/'.join(signals)}"
        
        return result_msg, stat_tags
    except:
        return None, []

def main():
    print("🚀 啟動全台股實戰掃描與統計模式...")
    stock_map = get_stock_info_map()
    if not stock_map: return
    
    results = []
    stats = {"多頭": 0, "MACD金叉": 0, "爆量": 0, "總掃描": 0}
    
    for i, (ticker, industry) in enumerate(stock_map.items()):
        if i % 100 == 0: print(f"進度: {i}/{len(stock_map)}...")
        
        res_msg, tags = analyze_stock(ticker, industry)
        stats["總掃描"] += 1
        for t in tags:
            stats[t] += 1
            
        if res_msg:
            results.append(res_msg)
        time.sleep(0.1)
        
    # --- 1. 發送選股結果 ---
    if results:
        for i in range(0, len(results), 5):
            chunk = results[i:i+5]
            msg = "🔍 【台股強勢股掃描報告】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
    
    # --- 2. 發送大盤統計摘要 (不論有沒有選到股都會發) ---
    bull_ratio = round((stats["多頭"] / stats["總掃描"]) * 100, 1) if stats["總掃描"] > 0 else 0
    summary_msg = (
        f"📊 【今日台股掃描數據摘要】\n\n"
        f"✅ 總掃描檔數：{stats['總掃描']} 檔\n"
        f"📈 均線多頭排列：{stats['多頭']} 檔 ({bull_ratio}%)\n"
        f"✨ MACD金叉：{stats['MACD_Hist'] if 'MACD_Hist' in stats else stats['MACD_金叉'] if 'MACD_金叉' in stats else stats['MACD金叉']} 檔\n"
        f"💥 今日爆量增長：{stats['爆量']} 檔\n\n"
        f"💡 說明：多頭比例越高代表市場環境越安全。"
    )
    # 修正統計字典 Key 錯誤的可能性
    summary_msg = summary_msg.replace("None", "0")
    send_line_message(summary_msg)
    
    print("🏁 任務結束")

if __name__ == "__main__":
    main()
