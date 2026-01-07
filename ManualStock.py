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
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("\n⚠️ 找不到 LINE 環境變數。")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    res = requests.post(url, headers=headers, json=payload)
    if res.status_code == 200:
        print("✅ 報告已成功推送到 LINE")
    else:
        print(f"❌ LINE 推送失敗: {res.text} (原因：額度已滿，請看產出的文字檔)")

def save_to_txt(content):
    """將報告儲存至 D:\\Mega\\下載\\個股 並修正路徑格式"""
    target_dir = r"D:\Mega\下載\個股"
    
    if not os.path.exists(target_dir):
        try:
            os.makedirs(target_dir)
            print(f"📂 已建立資料夾: {target_dir}")
        except:
            target_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    # 使用 normpath 確保斜線符合 Windows 規範 (全部變成 \)
    full_path = os.path.normpath(os.path.join(target_dir, filename))
    
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"💾 文字檔已儲存於：{full_path}")
        
        # 修正 Windows explorer 指令語法
        # 使用 list 形式避免 shell 引號錯誤
        subprocess.run(['explorer', '/select,', full_path])
        
    except Exception as e:
        print(f"❌ 存檔或開啟失敗：{e}")

# ==========================================
# 2. 核心診斷邏輯 (略，同前版)
# ==========================================
def get_stock_details(sid_clean):
    try:
        dl = DataLoader()
        df_info = dl.taiwan_stock_info()
        target = df_info[df_info['stock_id'] == sid_clean]
        if not target.empty:
            return target.iloc[0]['stock_name'], target.iloc[0]['industry_category']
    except: pass
    return "未知名稱", "其他產業"

def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock_name, industry = get_stock_details(clean_id)
        stock_obj = None
        df = pd.DataFrame()
        for suffix in [".TW", ".TWO"]:
            target = f"{clean_id}{suffix}"
            temp_stock = yf.Ticker(target)
            if not temp_stock.history(period="10d").empty:
                stock_obj = temp_stock
                df = temp_stock.history(period="1y") 
                break
        if df.empty: return f"❌ 找不到 {clean_id}"

        latest = df.iloc[-1]
        curr_p = latest['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        is_data_distorted = abs(bias_60) > 30
        if is_data_distorted:
            recent_df = df.iloc[-20:]
            high_1y = recent_df['High'].max()
            support_line = max(recent_df['Low'].min(), curr_p * 0.95)
            stop_loss = support_line * 0.97
            warn = "⚠️ 數據校正\n"
        else:
            high_1y = df['High'].max()
            support_line = ma60
            stop_loss = ma60 * 0.97
            warn = ""
        
        report = (
            f"【{clean_id} {stock_name}】{warn}"
            f"現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
            f"🔔APP警示: 壓:{high_1y:.1f} / 支:{support_line:.1f} / 損:{stop_loss:.1f}\n"
            f"-------------------"
        )
        return report
    except Exception as e: return f"❌ {sid} 錯誤: {str(e)}"

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    
    combined_reports = []
    for t in targets:
        combined_reports.append(get_diagnostic_report(t.strip().upper()))
    
    final_content = f"📊 個股診斷報告 ({datetime.date.today()})\n" + "="*20 + "\n"
    final_content += "\n".join(combined_reports)
    
    save_to_txt(final_content)
    send_line_message(final_content)
