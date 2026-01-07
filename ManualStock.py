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
# 請確保環境變數中有 LINE_ACCESS_TOKEN 與 LINE_USER_ID
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    """推播報告至 LINE (若額度滿會印出提示)"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        res = requests.post(url, headers=headers, json=payload)
        if res.status_code == 200:
            print("✅ 報告已推送到 LINE")
        else:
            # 針對額度已滿的狀況優化提示
            print(f"ℹ️ LINE 額度已滿 (Limit Reached)，請查看產出的文字檔。")
    except:
        pass

def save_and_verify_report(content):
    """
    強制存檔至 D:\Mega\下載\個股
    並修正自動開啟資料夾的 Windows 指令錯誤
    """
    # 1. 定義路徑 (使用原始字串避開轉義字元)
    base_dir = r"D:\Mega\下載\個股"
    
    # 2. 建立資料夾
    if not os.path.exists(base_dir):
        try:
            os.makedirs(base_dir)
            print(f"📂 已建立新資料夾: {base_dir}")
        except:
            print(f"⚠️ 無法在 D 槽建立，改用桌面...")
            base_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    # 3. 檔名與路徑標準化 (核心修正點：確保全為反斜線 \)
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    full_path = os.path.normpath(os.path.join(base_dir, filename))
    
    try:
        # 4. 強制寫入檔案
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # 5. 二次確認
        if os.path.exists(full_path):
            print("-" * 35)
            print(f"✅ 報告存檔成功！")
            print(f"📍 位置: {full_path}")
            print(f"📏 大小: {os.path.getsize(full_path)} bytes")
            print("-" * 35)
            
            # 6. 自動彈出資料夾 (修正 Errno 2)
            try:
                # 方法一：Windows 標準開啟
                os.startfile(base_dir)
                print(f"📂 已為您彈出資料夾視窗。")
            except:
                # 方法二：備援 explorer 指令 (使用串列格式避開引號解析問題)
                subprocess.run(['explorer', base_dir])
        else:
            print("❌ 存檔後找不到檔案，請檢查權限。")
            
    except Exception as e:
        print(f"❌ 發生存檔異常：{e}")

# ==========================================
# 2. 核心診斷邏輯 (包含 00992A 等 ETF 保護)
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
        
        # 抓取資料
        df = pd.DataFrame()
        for suffix in [".TW", ".TWO"]:
            df = yf.Ticker(f"{clean_id}{suffix}").history(period="1y")
            if not df.empty: break
        
        if df.empty: return f"❌ {clean_id}: 找不到歷史資料"

        latest = df.iloc[-1]
        curr_p = latest['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # 數據自動校正
        is_data_distorted = abs(bias_60) > 30
        if is_data_distorted:
            recent = df.iloc[-20:]
            high_v = recent['High'].max()
            supp = max(recent['Low'].min(), curr_p * 0.95)
            stop = supp * 0.97
            warn = "⚠️(數據校正)\n"
        else:
            high_v = df['High'].max()
            supp = ma60
            stop = ma60 * 0.97
            warn = ""
        
        # 籌碼面 (處理 00992A 債券 ETF 邏輯)
        chip_info = "外/投:讀取失敗"
        try:
            dl = DataLoader()
            start = (datetime.date.today() - datetime.timedelta(days=10)).strftime('%Y-%m-%d')
            c_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start)
            if not c_df.empty:
                f_n = (c_df[c_df['name']=='Foreign_Investor']['buy'].sum() - c_df[c_df['name']=='Foreign_Investor']['sell'].sum())/1000
                t_n = (c_df[c_df['name']=='Investment_Trust']['buy'].sum() - c_df[c_df['name']=='Investment_Trust']['sell'].sum())/1000
                chip_info = f"外:{int(pd.Series(f_n).fillna(0).iloc[0]):+d}/投:{int(pd.Series(t_n).fillna(0).iloc[0]):+d}"
        except: pass

        return (f"【{clean_id} {stock_name}】{warn}"
                f" 現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
                f" {chip_info}\n"
                f" 🔔APP警示: 壓:{high_v:.1f} / 支:{supp:.1f} / 損:{stop:.1f}\n"
                f" ------------------------------------")
    except Exception as e:
        return f"❌ {sid} 錯誤: {e}"

# ==========================================
# 3. 執行入口
# ==========================================
if __name__ == "__main__":
    # 用法：python ManualStock.py "2344 0052 00992A"
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace(',', ' ').split()
    
    print(f"🚀 啟動個股掃描...")
    
    reports = [get_diagnostic_report(t.strip().upper()) for t in targets]
    
    final_output = f"📊 個股診斷集體報告 ({datetime.date.today()})\n"
    final_output += "=" * 35 + "\n" + "\n".join(reports)
    
    # 執行儲存、驗證與彈出視窗
    save_and_verify_report(final_output)
    
    # 嘗試發送 LINE
    send_line_message(final_output)
