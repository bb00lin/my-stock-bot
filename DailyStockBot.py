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
# 1. 環境設定與參數
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
# 根據您的紀錄預設 LINE USER ID
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c"
OUTPUT_FILENAME = "scan_results.txt"

def send_line_message(message):
    """同步輸出到 Log 並發送 LINE 訊息"""
    print(f"\n--- 📤 發送 LINE 訊息 ---\n{message}\n", flush=True)
    
    if not LINE_ACCESS_TOKEN:
        print("⚠️ 提醒：找不到 LINE_ACCESS_TOKEN，僅在控制台輸出結果。", flush=True)
        return
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json", 
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            print(f"❌ LINE 發送失敗，狀態碼: {response.status_code}", flush=True)
    except Exception as e:
        print(f"❌ LINE 請求過程出錯: {e}", flush=True)

# ==========================================
# 2. 核心分析引擎
# ==========================================
def analyze_stock_smart_v3_1(ticker, stock_info, mode="NORMAL"):
    """
    執行單檔股票診斷
    """
    try:
        stock = yf.Ticker(ticker)
        # 抓取一年資料以計算 60MA
        df = stock.history(period="1y", progress=False)
        if len(df) < 60: return None
        # 排除當日尚未開盤或無成交量數據
        if df.iloc[-1]['Volume'] == 0: df = df.iloc[:-1]
        
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        curr_p = latest['Close']
        
        # 計算技術指標
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        ma20 = SMAIndicator(df['Close'], 20).sma_indicator().iloc[-1]
        ma60 = SMAIndicator(df['Close'], 60).sma_indicator().iloc[-1]
        vol_ratio = latest['Volume'] / df['Volume'].iloc[-11:-1].mean()
        
        is_potential = False
        tag = ""

        # A. 強勢模式
        if mode == "NORMAL":
            if vol_ratio > 1.2 and latest['Volume'] >= 500000 and curr_p > prev['Close']:
                tag = "🔥 強勢攻擊"
                is_potential = (curr_p > ma20) and (curr_p - ma60)/ma60 < 0.30

        # B. 弱勢抗跌模式
        elif mode == "WEAK":
            if abs(curr_p - ma20)/ma20 < 0.025 and curr_p >= prev['Close'] and latest['Volume'] >= 300000:
                tag = "🛡️ 逆勢支撐"
                is_potential = True

        # C. 避險/破位模式
        elif mode == "RISK":
            if curr_p < ma60 and prev['Close'] >= ma60:
                tag = "⚠️ 趨勢破線"
                is_potential = True
            elif rsi < 35 and vol_ratio > 1.1:
                tag = "📉 弱勢盤整"
                is_potential = True

        if is_potential:
            bias = ((curr_p - ma60) / ma60) * 100
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
    except:
        return None

# ==========================================
# 3. 主程序邏輯
# ==========================================
def main():
    start_time = datetime.datetime.now()
    print(f"🚀 啟動 DailyStockBot 智能全市場掃描...", flush=True)
    print(f"⏰ 當前時間：{start_time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    # 獲取台股清單
    dl = DataLoader()
    try:
        stock_df = dl.taiwan_stock_info()
    except Exception as e:
        print(f"❌ 獲取 FinMind 數據失敗: {e}", flush=True)
        return

    # 預處理股票清單
    stock_map = {}
    for _, row in stock_df.iterrows():
        sid = str(row['stock_id'])
        if len(sid) == 4:  # 只掃描普通股
            suffix = '.TWO' if '上櫃' in str(row.get('market_type', '')) else '.TW'
            ticker = f"{sid}{suffix}"
            stock_map[ticker] = {
                'name': row.get('stock_name', sid),
                'industry': row.get('industry_category', '股票')
            }

    print(f"📦 已載入 {len(stock_map)} 檔標的，開始掃描各模式...", flush=True)

    all_report_sections = []
    final_results_found = False

    # 依次執行模式掃描
    for mode_name, mode_key in [("強勢模式", "NORMAL"), ("弱勢抗跌模式", "WEAK"), ("避險/破位模式", "RISK")]:
        print(f"🔍 正在搜尋：{mode_name}...", flush=True)
        results = []
        
        # 遍歷所有股票
        for ticker, info in stock_map.items():
            res = analyze_stock_smart_v3_1(ticker, info, mode=mode_key)
            if res:
                results.append(res)
                print(f"   ✅ 發現符合：{ticker} {info['name']}", flush=True)
            time.sleep(0.01) # API 保護微延遲
        
        if results:
            final_results_found = True
            msg_header = f"🔍 【市場結構掃描 - {mode_name}】"
            report_section = f"{msg_header}\n" + "\n---\n".join(results)
            all_report_sections.append(report_section)
            
            # 發送 LINE 訊息 (每 5 檔一封，避免過長)
            for i in range(0, len(results), 5):
                chunk = results[i:i+5]
                send_line_message(f"{msg_header}\n\n" + "\n---\n".join(chunk))
            
            # 策略：如果強勢模式或抗跌模式有結果，通常代表市場有主線，不再掃描 RISK 避險模式
            if mode_key != "RISK":
                print(f"✨ {mode_name} 掃描完成且有產出，終止後續模式掃描。", flush=True)
                break
    
    # --- 輸出 .txt 檔案 ---
    print(f"📝 正在整理診斷結果並生成 {OUTPUT_FILENAME}...", flush=True)
    with open(OUTPUT_FILENAME, "w", encoding="utf-8") as f:
        f.write(f"DailyStockBot 診斷報告\n")
        f.write(f"生成時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("="*30 + "\n")
        if all_report_sections:
            f.write("\n\n".join(all_report_sections))
        else:
            no_res_msg = "📊 掃描完成：目前市場無符合條件標的，建議保守觀望。"
            f.write(no_res_msg)
            if not final_results_found:
                send_line_message(no_res_msg)

    print(f"✅ 任務圓滿完成，報告已存檔。", flush=True)

if __name__ == "__main__":
    main()
