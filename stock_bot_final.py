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
        print("Error: LINE Secrets 未設定")
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
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        print("LINE 訊息傳送成功！")
    except Exception as e:
        print(f"LINE 傳送失敗: {e}")

def get_stock_list():
    """使用 FinMind 獲取台股上市股票清單"""
    # try:
    #     print("正在從 FinMind 獲取股票清單...")
    #     dl = DataLoader()
    #     df = dl.taiwan_stock_info()
    #     # 過濾出普通股
    #     df = df[df['type'] == 'stock']
    #     # 轉換成 yfinance 格式 (例如 2330.TW)
    #     full_list = [f"{sid}.TW" for sid in df['stock_id'].tolist()]
    #     # 為了避免 GitHub Actions 執行過久，預設取前 60 檔進行掃描
    #     # 你可以修改成 full_list[:] 來掃描全部，但建議先小量測試
    #     return full_list[:60]
    # except Exception as e:
    #     print(f"獲取清單失敗: {e}，改用預設清單")
    #     return ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2881.TW"]

    """獲取全台股上市清單"""
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        df = df[df['type'] == 'stock']
        full_list = [f"{sid}.TW" for sid in df['stock_id'].tolist()]
        # 移除 [:60] 的限制，掃描全部
        print(f"成功取得清單，共 {len(full_list)} 檔股票")
        return full_list 
    except Exception as e:
        return ["2330.TW", "2317.TW", "2454.TW"]

def analyze_stock(ticker_symbol):
    """多重指標選股條件"""
    try:
        stock = yf.Ticker(ticker_symbol)
        # 抓取 6 個月資料以計算長週期均線
        df = stock.history(period="6mo")
        
        if len(df) < 60:
            return None

        # --- 技術指標計算 ---
        close_prices = df['Close']
        
        # 1. RSI (14)
        df['RSI'] = RSIIndicator(close=close_prices, window=14).rsi()
        
        # 2. 均線 (5日, 20日, 60日)
        df['MA5'] = SMAIndicator(close=close_prices, window=5).sma_indicator()
        df['MA20'] = SMAIndicator(close=close_prices, window=20).sma_indicator()
        df['MA60'] = SMAIndicator(close=close_prices, window=60).sma_indicator()
        
        # 3. MACD
        macd_obj = MACD(close=close_prices)
        df['MACD_Hist'] = macd_obj.macd_diff()

        # 取得最新與前一筆數據
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        current_price = round(latest['Close'], 2)
        
        # --- 選股邏輯判斷 ---
        signals = []
        
        # 條件 A: 均線多頭排列 (強勢趨勢)
        if latest['MA5'] > latest['MA20'] > latest['MA60']:
            signals.append("🔥 均線多頭排列")
            
        # 條件 B: MACD 黃金交叉
        if prev['MACD_Hist'] < 0 and latest['MACD_Hist'] > 0:
            signals.append("✨ MACD 黃金交叉")
            
        # 條件 C: RSI 從低檔反彈
        if prev['RSI'] < 35 and latest['RSI'] > 35:
            signals.append("🚀 RSI 底部反彈")

        # 條件 D: 價揚量增 (成交量是大於 10日平均的 1.5 倍)
        avg_vol = df['Volume'].iloc[-11:-1].mean()
        if latest['Volume'] > avg_vol * 1.5 and latest['Close'] > prev['Close']:
            signals.append("📊 量大價昂")

        "目前的條件比較嚴格，你可以試著把其中一個改為「寬鬆版」：
        "RSI 反彈：從 35 改為 40。
        "量大價昂：從 1.5 倍 改為 1.2 倍。

        if signals:
            return f"股票: {ticker_symbol}\n現價: {current_price}\n訊號: {'、'.join(signals)}"
        
        return None

    except Exception:
        return None

def main():
    print("🚀 開始台股多重指標掃描...")
    stocks = get_stock_list()
    results = []
    
    for i, s in enumerate(stocks):
        if i % 10 == 0:
            print(f"進度: {i}/{len(stocks)}...")
        
        res = analyze_stock(s)
        if res:
            results.append(res)
        
        # 關鍵：稍微停頓避免被 Yahoo 封鎖 IP
        time.sleep(0.8)
    
    # 組合訊息
    if results:
        header = f"🔍 【台股強勢股掃描報告】\n掃描時間: {time.strftime('%Y-%m-%d %H:%M')}\n"
        # 分批發送，避免訊息過長被 LINE 拒絕 (每 5 檔股票一則訊息)
        for i in range(0, len(results), 5):
            chunk = results[i:i + 5]
            body = "\n---\n".join(chunk)
            send_line_message(header + "\n" + body)
    else:
        send_line_message("今日掃描完成，未發現符合技術面強勢條件之股票。")

if __name__ == "__main__":
    main()
