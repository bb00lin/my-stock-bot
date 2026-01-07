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
            print(f"ℹ️ LINE 額度已滿 (Limit Reached)，請查看 D 槽文字檔報告。")
    except: pass

def save_and_verify_report(content):
    """
    強制存檔至 D:\Mega\下載\個股
    並使用最強效的 Windows 開啟指令
    """
    # 修正路徑格式，確保完全符合 Windows 規範
    base_dir = r"D:\Mega\下載\個股"
    
    if not os.path.exists(base_dir):
        try:
            os.makedirs(base_dir)
        except:
            base_dir = os.path.join(os.path.expanduser("~"), "Desktop")

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    # 使用 normpath 確保斜線方向正確 (\)
    full_path = os.path.normpath(os.path.join(base_dir, filename))
    
    try:
        # 1. 寫入檔案
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # 2. 驗證
        if os.path.exists(full_path):
            print("-" * 35)
            print(f"✅ 報告存檔成功！")
            print(f"📍 實際位置: {full_path}")
            print(f"📏 檔案大小: {os.path.getsize(full_path)} bytes")
            print("-" * 35)
            
            # 3. 自動開啟資料夾 (使用 shell=True 解決找不到 explorer 的問題)
            try:
                # 這是最暴力但對 Windows 最有效的方法
                subprocess.run(f'explorer.exe "{base_dir}"', shell=True)
                print(f"📂 已嘗試開啟資料夾視窗。")
            except Exception as e:
                print(f"💡 請手動開啟此路徑查看報告: {base_dir}")
        else:
            print("❌ 存檔失敗。")
            
    except Exception as e:
        print(f"❌ 發生異常：{e}")

# ==========================================
# 2. 核心診斷邏輯
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
        if is_data_distorted:
            recent = df.iloc[-20:]
            high_v = recent['High'].max()
            supp = max(recent['Low'].min(), curr_p * 0.95)
            stop = supp * 0.97
            warn = "⚠️(數據校正)\n"
        else:
            high_v, supp, stop = df['High'].max(), ma60, ma60 * 0.97
            warn = ""
        
        chip_info = "外/投:無數據"
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
                f" 🔔APP提示: 壓:{high_v:.1f} / 支:{supp:.1f} / 損:{stop:.1f}\n"
                f" ------------------------------------")
    except Exception as e: return f"❌ {sid} 錯誤: {e}"

if __name__ == "__main__":
    targets = (sys.argv[1] if len(sys.argv) > 1 else "2344").replace(',', ' ').split()
    print(f"🚀 啟動診斷程式...")
    reports = [get_diagnostic_report(t.strip().upper()) for t in targets]
    final_output = f"📊 個股診斷報告 ({datetime.date.today()})\n" + "=" * 35 + "\n" + "\n".join(reports)
    
    save_and_verify_report(final_output)
    send_line_message(final_output)
