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
            print(f"ℹ️ LINE 額度已滿，請查看下方 D 槽文字檔。")
    except: pass

def save_and_verify_report(content):
    """
    100% 強制存檔並開啟資料夾
    """
    # 確保路徑完全符合 Windows 格式
    base_dir = r"D:\Mega\下載\個股"
    
    if not os.path.exists(base_dir):
        try:
            os.makedirs(base_dir)
        except:
            base_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    # 使用 normpath 確保全為反斜線
    full_path = os.path.normpath(os.path.join(base_dir, filename))
    
    try:
        # 1. 寫入檔案
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # 2. 驗證
        if os.path.exists(full_path):
            print("-" * 35)
            print(f"✅ 報告存檔成功！")
            print(f"📍 檔案位置: {full_path}")
            print(f"📏 檔案大小: {os.path.getsize(full_path)} bytes")
            print("-" * 35)
            
            # 3. 嘗試三種方式開啟 (Windows 專用)
            try:
                # 方式 A: 最原始的 CMD 開啟
                os.system(f'start "" "{base_dir}"')
                print(f"📂 已執行系統開啟指令。")
            except:
                # 方式 B: 使用 PowerShell 開啟 (避開 explorer 指令缺失問題)
                subprocess.run(["powershell", "-Command", f"ii '{base_dir}'"], shell=True)
        else:
            print("❌ 存檔失敗。")
            
    except Exception as e:
        print(f"❌ 發生異常：{e}")

# ==========================================
# 2. 核心診斷與執行 (保持不變)
# ==========================================
def get_stock_details(sid_clean):
    try:
        dl = DataLoader()
        df_info = dl.taiwan_stock_info()
        target = df_info[df_info['stock_id'] == sid_clean]
        if not target.empty:
            return target.iloc[0]['stock_name'], target.iloc[0]['industry_category']
    except: pass
    return "標的", "其他"

def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock_name, industry = get_stock_details(clean_id)
        df = pd.DataFrame()
        for suffix in [".TW", ".TWO"]:
            df = yf.Ticker(f"{clean_id}{suffix}").history(period="1y")
            if not df.empty: break
        if df.empty: return f"❌ {clean_id}: 找不到資料"

        curr_p = df.iloc[-1]['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        is_data_distorted = abs(bias_60) > 30
        high_v = df.iloc[-20:]['High'].max() if is_data_distorted else df['High'].max()
        supp = max(df.iloc[-20:]['Low'].min(), curr_p * 0.95) if is_data_distorted else ma60

        return (f"【{clean_id} {stock_name}】"
                f" 現價:{curr_p:.2f} | 乖離:{bias_60:+.1f}%\n"
                f" 🔔 壓:{high_v:.1f} / 支:{supp:.1f}\n"
                f" ------------------------------------")
    except Exception as e: return f"❌ {sid} 錯誤: {e}"

if __name__ == "__main__":
    targets = (sys.argv[1] if len(sys.argv) > 1 else "2344").replace(',', ' ').split()
    print(f"🚀 啟動診斷...")
    reports = [get_diagnostic_report(t.strip().upper()) for t in targets]
    final_output = f"📊 診斷報告 ({datetime.date.today()})\n" + "="*35 + "\n" + "\n".join(reports)
    save_and_verify_report(final_output)
    send_line_message(final_output)
