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
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: 
        print("LINE 設定缺失，僅於終端機輸出。")
        print(message)
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        print(f"LINE 發送失敗: {e}")

# ==========================================
# 2. 股票清單獲取 (FinMind)
# ==========================================
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

# ==========================================
# 3. 核心潛力股分析邏輯
# ==========================================
def analyze_stock(ticker, industry):
    """
    優化版潛力篩選邏輯：
    1. 底部轉強：RSI 從低檔( < 45) 黃金交叉向上
    2. 回測支撐：股價靠近 20MA (月線) 且收紅
    3. 金流異動：量比 > 1.5 倍且成交量需 > 1,000張 (確保非殭屍股)
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period="7mo", progress=False) # 抓 7 個月計算 60MA
        if len(df) < 60: return None, []
        
        # 排除無交易量數據 (如假日抓取)
        if df.iloc[-1]['Volume'] == 0:
            df = df.iloc[:-1]
        
        close = df['Close']
        df['RSI'] = RSIIndicator(close).rsi()
        df['MA20'] = SMAIndicator(close, 20).sma_indicator()
        df['MA60'] = SMAIndicator(close, 60).sma_indicator()
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        stat_tags = []
        signals = []
        
        # --- A. 策略 1：低位轉強 (抓起漲點) ---
        if prev['RSI'] < 45 and latest['RSI'] > prev['RSI']:
            signals.append("底部轉強")
            stat_tags.append("底部轉強")

        # --- B. 策略 2：回測月線 (抓支撐點) ---
        # 股價在月線上方 2.5% 以內，且今日未跌破
        dist_to_ma20 = (latest['Close'] - latest['MA20']) / latest['MA20']
        if 0 < dist_to_ma20 < 0.025 and latest['Close'] > prev['Close']:
            signals.append("回測月線")
            stat_tags.append("回測支撐")

        # --- C. 策略 3：金流動能 (抓主力盤) ---
        avg_vol_10d = df['Volume'].iloc[-11:-1].mean()
        vol_ratio = latest['Volume'] / avg_vol_10d
        # 門檻：量比 1.5 倍 且 總量 > 1,000張 (1,000,000股)
        if vol_ratio > 1.5 and latest['Volume'] >= 1000000:
            signals.append("金流湧入")
            stat_tags.append("爆量")

        # --- D. 綜合判定 ---
        # 潛力股標準：符合兩個以上訊號，或是有強大金流且收紅
        is_potential = (len(signals) >= 2) or ("金流湧入" in signals and latest['Close'] > prev['Close'])
        
        # 排除乖離過大（噴太高）的標的，避免追高
        bias_60 = (latest['Close'] - latest['MA60']) / latest['MA60']
        if bias_60 > 0.20: is_potential = False 

        result_msg = None
        if is_potential and latest['Close'] >= 10:
            vol_k = int(latest['Volume'] / 1000)
            result_msg = (
                f"🌟【潛力觀測】{ticker} [{industry}]\n"
                f"現價: {latest['Close']:.2f} ({((latest['Close']-prev['Close'])/prev['Close'])*100:+.1f}%)\n"
                f"張數: {vol_k}張 (量比:{vol_ratio:.1f})\n"
                f"訊號: {'/'.join(signals)}"
            )
        
        return result_msg, stat_tags
    except:
        return None, []

# ==========================================
# 4. 主程式與統計
# ==========================================
def main():
    start_time = time.time()
    now = datetime.datetime.now()
    print(f"🚀 啟動潛力股全台掃描 (時間: {now.strftime('%Y-%m-%d %H:%M')})...")
    
    stock_map = get_stock_info_map()
    if not stock_map: return
    
    results = []
    # 初始化統計數據
    stats = {"底部轉強": 0, "回測支撐": 0, "爆量": 0, "總掃描": 0}
    
    total = len(stock_map)
    for i, (ticker, industry) in enumerate(stock_map.items()):
        if i % 100 == 0: 
            print(f"掃描進度: {i}/{total} (已發現 {len(results)} 檔潛力股)...")
        
        res_msg, tags = analyze_stock(ticker, industry)
        stats["總掃描"] += 1
        for t in tags:
            stats[t] = stats.get(t, 0) + 1
            
        if res_msg:
            results.append(res_msg)
        
        # 適度延遲避免 API 封鎖
        time.sleep(0.05)
    
    # 1. 發送潛力股結果 (每 5 檔一則訊息)
    if results:
        for i in range(0, len(results), 5):
            chunk = results[i:i+5]
            msg = "🔍 【全台股潛力掃描：轉折與金流名單】\n\n" + "\n---\n".join(chunk)
            send_line_message(msg)
    else:
        send_line_message("🔍 今日掃描完成：未發現符合潛力轉折條件之標的。")
    
    # 2. 發送大盤統計摘要 (修改後的 Summary)
    cost_time = int(time.time() - start_time)
    
    # 計算市場情緒比例
    potential_count = len(results)
    potential_ratio = round((potential_count / stats["總掃描"]) * 100, 1) if stats["總掃描"] > 0 else 0
    
    summary_msg = (
        f"📊 【台股市場結構掃描摘要】\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ 總掃描檔數：{stats['總掃描']} 檔\n"
        f"🌟 底部轉強標的：{stats.get('底部轉強', 0)} 檔\n"
        f"🛡️ 回測支撐標的：{stats.get('回測支撐', 0)} 檔\n"
        f"💥 金流異動標的：{stats.get('爆量', 0)} 檔\n"
        f"━━━━━━━━━━━━━━\n"
        f"💡 本次篩中率：{potential_ratio}%\n"
        f"⏱️ 掃描總耗時：{cost_time // 60}分{cost_time % 60}秒\n\n"
        f"📌 投資建議：優先關注「回測支撐」＋「金流湧入」雙重訊號標的，此為法人回補最常見的起漲點。"
    )
    send_line_message(summary_msg)
    print("🏁 任務結束")

if __name__ == "__main__":
    main()
