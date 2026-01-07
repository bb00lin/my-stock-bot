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
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    res = requests.post(url, headers=headers, json=payload)
    if res.status_code != 200:
        print(f"❌ LINE 額度已滿 (Limit Reached)，請直接查看 D 槽文字檔報告。")

def save_to_txt(content):
    """將報告儲存至 D:\\Mega\\下載\\個股"""
    target_dir = r"D:\Mega\下載\個股"
    
    if not os.path.exists(target_dir):
        try:
            os.makedirs(target_dir)
        except:
            target_dir = "."

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    full_path = os.path.normpath(os.path.join(target_dir, filename))
    
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ 診斷報告已儲存：{full_path}")
        
        # 使用 Windows 內建最穩定的方式開啟資料夾並選取檔案
        os.startfile(target_dir)
    except Exception as e:
        print(f"❌ 存檔失敗：{e}")

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
    return "個股", "其他"

def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock_name, industry = get_stock_details(clean_id)
        
        # 嘗試抓取資料
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
        
        # 自動校正 (處理 0052, 2344 數據斷層)
        is_data_distorted = abs(bias_60) > 30
        if is_data_distorted:
            recent = df.iloc[-20:]
            high_v, low_v = recent['High'].max(), recent['Low'].min()
            supp, stop = max(low_v, curr_p * 0.95), max(low_v, curr_p * 0.95) * 0.97
            warn = "⚠️(數據校正)"
        else:
            high_v, supp, stop = df['High'].max(), ma60, ma60 * 0.97
            warn = ""
        
        # 籌碼面防呆 (解決 00992A 的 NaN 錯誤)
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

        return (f"【{clean_id} {stock_name}】{warn}\n"
                f" 現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
                f" {chip_info}\n"
                f" 🔔APP提示: 壓:{high_v:.1f} / 支:{supp:.1f} / 損:{stop:.1f}\n"
                f" -------------------")
    except Exception as e:
        return f"❌ {sid} 錯誤: {str(e)}"

if __name__ == "__main__":
    targets = (sys.argv[1] if len(sys.argv) > 1 else "2344").replace(',', ' ').split()
    print(f"🚀 正在分析並存檔至 D:\\Mega\\下載\\個股...")
    
    results = [get_diagnostic_report(t.strip().upper()) for t in targets]
    final_content = f"📊 個股診斷報告 ({datetime.date.today()})\n" + "="*25 + "\n" + "\n".join(results)
    
    save_to_txt(final_content)
    send_line_message(final_content)
