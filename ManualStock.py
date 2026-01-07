import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time
import sys
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

def send_line_message(message):
    LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
    LINE_USER_ID = os.getenv("LINE_USER_ID")
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        res = requests.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            print(f"ℹ️ LINE 額度已滿，請直接看下方的螢幕輸出內容。")
    except: pass

def output_to_screen(content):
    """強迫將內容印在 GitHub Actions 控制台"""
    print("\n" + "★" * 50)
    print(f"📊 診斷報告發布 (台北時間: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    print("★" * 50)
    print(content)
    print("★" * 50 + "\n")
    # 強制重新整理輸出流，確保 GitHub Log 一定會顯示
    sys.stdout.flush()

def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
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
        
        return (f"【{clean_id}】 現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
                f" 🔔 建議警示位: 壓:{df['High'].max():.1f} / 支:{ma60:.1f}")
    except Exception as e: return f"❌ {sid} 錯誤: {e}"

if __name__ == "__main__":
    input_args = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_args.replace(',', ' ').split()
    
    print("🚀 正在產生雲端報告，請稍候展開此步驟查看...")
    
    reports = [get_diagnostic_report(t.strip().upper()) for t in targets]
    final_output = "\n".join(reports)
    
    # 這裡會直接印在畫面上
    output_to_screen(final_output)
    
    # 同時嘗試傳送 LINE
    send_line_message(final_output)
