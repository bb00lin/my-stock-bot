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
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"

def send_line_message(message):
    """同步輸出到控制台並發送 LINE 訊息"""
    print(f"\n--- 📤 發送 LINE 訊息 ---\n{message}\n", flush=True)
    if not LINE_ACCESS_TOKEN: return
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        print(f"❌ LINE 請求出錯: {e}", flush=True)

# ==========================================
# 2. 核心分析引擎
# ==========================================
def analyze_stock_smart_v3_1(ticker, stock_info, mode="NORMAL"):
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

        if mode == "NORMAL":
            if vol_ratio > 1.2 and latest['Volume'] >= 500000 and curr_p > prev['Close']:
                tag = "🔥 強勢攻擊"
                is_potential = (curr_p > ma20) and (curr_p - ma60)/ma60 < 0.30
        elif mode == "WEAK":
            if abs(curr_p - ma20)/ma20 < 0.025 and curr_p >= prev['Close'] and latest['Volume'] >= 300000:
                tag = "🛡️ 逆勢支撐"
                is_potential = True
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
    current_date = start_time.strftime('%Y-%m-%d')
    dynamic_filename = f"scan_report_{current_date}.txt"
    
    print(f"🚀 啟動 DailyStockBot 全市場掃描...", flush=True)

    dl = DataLoader()
    try:
        stock_df = dl.taiwan_stock_info()
    except Exception as e:
        print(f"❌ 數據失敗: {e}", flush=True)
        return

    stock_map = {}
    for _, row in stock_df.iterrows():
        sid = str(row['stock_id'])
        if len(sid) == 4:
            suffix = '.TWO' if '上櫃' in str(row.get('market_type', '')) else '.TW'
            stock_map[f"{sid}{suffix}"] = {'name': row.get('stock_name', sid), 'industry': row.get('industry_category', '股票')}

    all_report_sections = []
    final_results_found = False

    for mode_name, mode_key in [("強勢模式", "NORMAL"), ("弱勢抗跌模式", "WEAK"), ("避險/破位模式", "RISK")]:
        results = []
        for ticker, info in stock_map.items():
            res = analyze_stock_smart_v3_1(ticker, info, mode=mode_key)
            if res:
                results.append(res)
                time.sleep(0.01)
        
        if results:
            final_results_found = True
            msg_header = f"🔍 【市場結構掃描 - {mode_name}】"
            all_report_sections.append(f"{msg_header}\n" + "\n---\n".join(results))
            for i in range(0, len(results), 5):
                send_line_message(f"{msg_header}\n\n" + "\n---\n".join(results[i:i+5]))
            if mode_key != "RISK": break
    
    # --- 儲存與同步邏輯 ---
    report_content = "DailyStockBot 報告 (" + current_date + ")\n" + "="*40 + "\n" + "\n\n".join(all_report_sections) if all_report_sections else "📊 目前無符合條件標的。"
    
    # 1. 雲端存檔
    with open(dynamic_filename, "w", encoding="utf-8") as f:
        f.write(report_content)
    with open("latest_scan_report.txt", "w", encoding="utf-8") as f:
        f.write(f"最新掃描日期: {current_date}\n檔案: {dynamic_filename}")

    # 2. 本地 D 槽存檔 (當您在本機執行時)
    local_path = r"D:\MEGA\下載\股票"
    if os.path.exists(local_path):
        try:
            with open(os.path.join(local_path, dynamic_filename), "w", encoding="utf-8") as f:
                f.write(report_content)
            print(f"✅ D 槽同步成功: {dynamic_filename}")
        except: pass

    print(f"✅ 任務圓滿完成。", flush=True)

if __name__ == "__main__":
    main()
