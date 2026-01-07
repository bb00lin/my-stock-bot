import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time
import sys
import subprocess
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

# ==========================================
# 1. 環境設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        res = requests.post(url, headers=headers, json=payload)
        if res.status_code != 200:
            print(f"ℹ️ LINE 額度已滿，請直接查看本目錄下的文字檔。")
    except: pass

def save_to_current_dir(content):
    """
    強制存檔至程式碼所在的資料夾
    """
    # 獲取目前執行腳本的絕對路徑資料夾
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    full_path = os.path.join(base_dir, filename)
    
    try:
        # 1. 寫入檔案
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # 2. 顯示資訊
        print("-" * 40)
        print(f"✅ 報告已成功存檔！")
        print(f"📍 檔案就在這裡: {full_path}")
        print(f"📏 檔案大小: {os.path.getsize(full_path)} bytes")
        print("-" * 40)
        
        # 3. 自動開啟目前資料夾
        if os.name == 'nt': # Windows
            os.startfile(base_dir)
        else:
            subprocess.run(['open', base_dir])
            
    except Exception as e:
        print(f"❌ 存檔失敗：{e}")

# ==========================================
# 2. 核心診斷邏輯
# ==========================================
def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        # 獲取名稱
        dl = DataLoader()
        df_info = dl.taiwan_stock_info()
        target = df_info[df_info['stock_id'] == clean_id]
        stock_name = target.iloc[0]['stock_name'] if not target.empty else "標的"
        
        # 抓取股價
        df = pd.DataFrame()
        for suffix in [".TW", ".TWO"]:
            df = yf.Ticker(f"{clean_id}{suffix}").history(period="1y")
            if not df.empty: break
            
        if df.empty: return f"❌ {clean_id}: 找不到資料"

        curr_p = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        
        return (f"【{clean_id} {stock_name}】\n"
                f" 現價:{curr_p:.2f} | 乖離:{bias_60:+.1f}%\n"
                f" ------------------------------------")
    except Exception as e: return f"❌ {sid} 錯誤: {e}"

if __name__ == "__main__":
    targets = (sys.argv[1] if len(sys.argv) > 1 else "2344").replace(',', ' ').split()
    print(f"🚀 啟動診斷...")
    
    results = [get_diagnostic_report(t.strip().upper()) for t in targets]
    final_output = f"📊 診斷報告 ({datetime.date.today()})\n" + "="*35 + "\n" + "\n".join(results)
    
    # 執行存檔 (存放在程式目錄)
    save_to_current_dir(final_output)
    
    # LINE 推播
    send_line_message(final_output)
