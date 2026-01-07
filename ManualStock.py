import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time
import sys
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

# ==========================================
# 1. 環境設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    """嘗試推播至 LINE，額度滿時僅在 Log 提示"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        res = requests.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            print(f"\nℹ️ LINE 額度已滿，請直接查看下方 Log 內的報告內容。")
    except:
        pass

def output_report(content):
    """將報告印在控制台 (GitHub Actions Log) 並儲存檔案"""
    # 1. 直接印在 GitHub Actions 的畫面上
    print("\n" + "="*50)
    print(f"📋 股票診斷報告輸出時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*50)
    print(content)
    print("="*50 + "\n")

    # 2. 同步存成文字檔 (供 GitHub Artifact 或 Commit 使用)
    try:
        filename = f"Stock_Report_{datetime.date.today()}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"💾 檔案已暫存於雲端路徑: {os.path.abspath(filename)}")
    except Exception as e:
        print(f"❌ 檔案存檔失敗: {e}")

# ==========================================
# 2. 核心診斷邏輯 (包含 ETF 保護)
# ==========================================
def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        # 獲取標的名稱
        try:
            dl = DataLoader()
            df_info = dl.taiwan_stock_info()
            target = df_info[df_info['stock_id'] == clean_id]
            stock_name = target.iloc[0]['stock_name'] if not target.empty else "標的"
        except:
            stock_name = "標的"
        
        # 獲取股價
        df = pd.DataFrame()
        for suffix in [".TW", ".TWO"]:
            df = yf.Ticker(f"{clean_id}{suffix}").history(period="1y")
            if not df.empty: break
            
        if df.empty: return f"❌ {clean_id}: 找不到資料"

        latest = df.iloc[-1]
        curr_p = latest['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # 數據校正 (針對 0052 等高乖離標的)
        is_distorted = abs(bias_60) > 30
        high_v = df.iloc[-20:]['High'].max() if is_distorted else df['High'].max()
        supp = max(df.iloc[-20:]['Low'].min(), curr_p * 0.95) if is_distorted else ma60

        return (f"【{clean_id} {stock_name}】\n"
                f" 現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
                f" 🔔 APP警示位: 壓:{high_v:.1f} / 支:{supp:.1f}\n"
                f" ------------------------------------")
    except Exception as e:
        return f"❌ {sid} 診斷出錯: {e}"

# ==========================================
# 3. 執行
# ==========================================
if __name__ == "__main__":
    # 用法範例: python ManualStock.py "2344 0052 00992A"
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace(',', ' ').split()
    
    print(f"🚀 GitHub Actions 診斷任務啟動...")
    
    reports = [get_diagnostic_report(t.strip().upper()) for t in targets]
    
    final_output = "\n".join(reports)
    
    # 輸出至控制台與存檔
    output_report(final_output)
    
    # 推送至 LINE (儘管額度可能已滿)
    send_line_message(final_output)
