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
    """傳送訊息到 LINE"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("❌ LINE Secrets 未設定")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"❌ LINE 發送失敗: {e}")

def get_stock_info_map():
    """獲取全台股清單並自動識別上市(.TW)與上櫃(.TWO)"""
    try:
        print("🔍 正在從 FinMind 獲取全台股清單...")
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        
        stock_map = {}
        for _, row in df.iterrows():
            sid = row['stock_id']
            # 過濾普通股與 KY 股 (4-5 碼)，排除權證與權利證書 (6 碼以上)
            if 4 <= len(sid) <= 5:
                # 判斷市場類型決定後綴
                suffix = ".TWO" if row['market_type'] in ['上櫃', '誠信上櫃'] else ".TW"
                stock_map[f"{sid}{suffix}"] = row['industry_category']
        
        if not stock_map:
            print("⚠️ 無法獲取動態清單，啟動備援名單")
            return {"2330.TW": "半導體業", "2317.TW": "其他電子業"}
            
        print(f"✅ 成功獲取清單，共 {len(stock_map)} 檔股票")
        return stock_map
    except Exception as e:
        print(f"❌ 獲取清單失敗: {e}")
        return {"2330.TW": "半導體業"}

def analyze_stock(ticker_symbol, industry_name):
    """技術面過濾邏輯"""
    try:
        # 靜默模式抓取，減少 Log 雜訊
        stock = yf.Ticker(ticker_symbol)
        df = stock.history(period="6mo", progress=False)
        
        if len(df) < 60:
            return None

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- 門檻過濾 (可自行調整) ---
        # 股價 > 15元 且 成交量 > 500張 (500,000股)
        if latest['Close'] < 15 or latest['Volume'] < 500000:
            return None

        # --- 技術指標計算 ---
        close = df['Close']
        # 1. RSI
        df['RSI'] = RSIIndicator(close, window=14).rsi()
        # 2. 均線
        df['MA5'] = SMAIndicator(close, window=5).sma_indicator()
        df['MA20'] = SMAIndicator(close, window=20).sma_indicator()
        df['MA60'] = SMAIndicator(close, window=60).sma_indicator()
        # 3. MACD
        macd_obj = MACD(close)
        df['MACD_Hist'] = macd_obj.macd_diff()

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        signals = []
        # 條件 A: 均線多頭排列
        if latest['MA5'] > latest['MA20'] > latest['MA60']:
            signals.append("🔥多頭")
        # 條件 B: MACD 黃金交叉 (柱狀體翻正)
        if prev['MACD_Hist'] < 0 and latest['MACD_Hist'] > 0:
            signals.append("✨MACD")
        # 條件 C: RSI 底部反彈 (低於 40 轉強)
        if prev['RSI'] < 40 and latest['RSI'] > 40:
            signals.append("🚀RSI反彈")
        # 條件 D: 爆量長紅
        avg_vol = df['Volume'].iloc[-11:-1].mean()
        if latest['Volume'] > avg_vol * 1.5 and latest['Close'] > prev['Close']:
            signals.append("📊爆量")

        # --- 輸出判斷：符合 2 項以上訊號才報出 ---
        if len(signals) >= 2:
            vol_shares = int(latest['Volume'] / 1000)
            return f"📍{ticker_symbol} [{industry_name}]\n現價: {round(latest['Close'], 2)}\n張數: {vol_shares}張\n訊號: {'/'.join(signals)}"
        
        return None
    except:
        # 發生錯誤時跳過，保持 Log 清潔
        return None

def main():
    print("🚀 啟動全台股實戰掃描模式...")
    stock_map = get_stock_info_map()
    results = []
    
    # 開始遍歷
    for i, (ticker, industry) in enumerate(stock_map.items()):
        if i % 100 == 0:
            print(f"進度: {i}/{len(stock_map)}...")
        
        res = analyze_stock(ticker, industry)
        if res:
            results.append(res)
        
        # 維持小停頓，避免被 yfinance 封鎖
        time.sleep(0.1)
    
    # 傳送結果
    if results:
        # 分批發送，每 5 檔一則訊息
        for i in range(0, len(results), 5):
            chunk = results[i:i+5]
            msg = "🔍 【台股強勢股掃描報告】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
        print(f"✅ 掃描完成，共發現 {len(results)} 檔符合條件標的")
    else:
        send_line_message("🏁 今日全台股掃描完成，未發現強勢標的。")
        print("🏁 掃描結束，無符合標的。")

if __name__ == "__main__":
    main()
