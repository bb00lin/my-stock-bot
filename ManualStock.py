import os
import platform
import subprocess
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
    """推播至 LINE (若額度滿則跳過)"""
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
            print(f"ℹ️ LINE 額度已滿，請查看下方 D 槽文字檔。")
    except:
        pass

def save_and_open_report(content):
    """儲存至 D 槽並自動開啟資料夾"""
    target_dir = r"D:\Mega\下載\個股"
    
    # 確保資料夾存在
    if not os.path.exists(target_dir):
        try:
            os.makedirs(target_dir)
        except:
            target_dir = "."

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    # 使用 normpath 確保路徑完全符合 Windows 格式
    full_path = os.path.normpath(os.path.join(target_dir, filename))
    
    try:
        # 1. 寫入檔案
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"💾 報告已存檔：{full_path}")
        
        # 2. 自動開啟資料夾 (多重嘗試機制)
        print(f"📂 正在自動為您開啟資料夾...")
        if platform.system() == "Windows":
            # 優先嘗試最直接的 explorer 指令
            try:
                subprocess.run(['explorer', target_dir], check=True)
            except:
                # 備援方案：嘗試直接打開檔案
                os.system(f'start "" "{target_dir}"')
        else:
            # 非 Windows 環境 (Mac/Linux)
            opener = "open" if platform.system() == "Darwin" else "xdg-open"
            subprocess.call([opener, target_dir])
            
    except Exception as e:
        print(f"⚠️ 存檔成功，但無法自動開啟資料夾：{e}")

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
        
        # 抓取股價資料
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
            warn = "⚠️(已修正數據)"
        else:
            high_v = df['High'].max()
            supp = ma60
            stop = ma60 * 0.97
            warn = ""
        
        # 籌碼面 (00992A 等 ETF 適用)
        chip_info = "外/投: 無數據"
        try:
            dl = DataLoader()
            start = (datetime.date.today() - datetime.timedelta(days=10)).strftime('%Y-%m-%d')
            c_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start)
            if not c_df.empty:
                f_n = (c_df[c_df['name']=='Foreign_Investor']['buy'].sum() - c_df[c_df['name']=='Foreign_Investor']['sell'].sum())/1000
                t_n = (c_df[c_df['name']=='Investment_Trust']['buy'].sum() - c_df[c_df['name']=='Investment_Trust']['sell'].sum())/1000
                chip_info = f"外:{int(pd.Series(f_n).fillna(0).iloc[0]):+d} / 投:{int(pd.Series(t_n).fillna(0).iloc[0]):+d}"
        except: pass

        report = (
            f"【{clean_id} {stock_name}】{warn}\n"
            f" 💰 現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
            f" 📊 {chip_info}\n"
            f" 🔔 APP警示: 壓:{high_v:.1f} / 支:{supp:.1f} / 損:{stop:.1f}\n"
            f" ------------------------------------"
        )
        return report
    except Exception as e:
        return f"❌ {sid} 診斷出錯: {str(e)}"

# ==========================================
# 3. 執行入口
# ==========================================
if __name__ == "__main__":
    # 支援輸入多個代碼，如 python ManualStock.py "2344 0052"
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace(',', ' ').split()
    
    print(f"🚀 開始分析標的：{targets}")
    
    results = []
    for t in targets:
        print(f"正在分析 {t}...")
        results.append(get_diagnostic_report(t.strip().upper()))
    
    header = f"📊 個股診斷集體報告 ({datetime.date.today()})\n"
    separator = "=" * 36 + "\n"
    final_output = header + separator + "\n".join(results)
    
    # 執行儲存與開啟
    save_and_open_report(final_output)
    
    # 執行 LINE 推播 (若額度滿則靜默失敗)
    send_line_message(final_output)
