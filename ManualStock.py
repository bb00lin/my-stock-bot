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
# 請確保環境變數中已設定 LINE_ACCESS_TOKEN 與 LINE_USER_ID
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("\n⚠️ 找不到 LINE 環境變數，僅在本地端顯示報告：")
        print(message)
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    res = requests.post(url, headers=headers, json=payload)
    if res.status_code == 200:
        print("✅ 報告已成功推送到 LINE")
    else:
        print(f"❌ LINE 推送失敗: {res.text}")

def save_to_txt(content):
    """將報告儲存至指定路徑：D:\\Mega\\下載\\個股\\"""
    target_dir = r"D:\Mega\下載\個股"
    
    # 如果資料夾不存在則建立
    if not os.path.exists(target_dir):
        try:
            os.makedirs(target_dir)
            print(f"📂 已建立新資料夾: {target_dir}")
        except Exception as e:
            print(f"❌ 無法建立資料夾，改存至目前目錄。錯誤: {e}")
            target_dir = "."

    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    full_path = os.path.join(target_dir, filename)
    
    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"💾 文字檔已儲存於：{full_path}")
    except Exception as e:
        print(f"❌ 存檔失敗：{e}")

# ==========================================
# 2. 產業與名稱獲取
# ==========================================
def get_stock_details(sid_clean):
    try:
        dl = DataLoader()
        df_info = dl.taiwan_stock_info()
        target = df_info[df_info['stock_id'] == sid_clean]
        if not target.empty:
            c_name = target.iloc[0]['stock_name']
            industry = target.iloc[0]['industry_category']
            return f"{c_name}", f"{industry}"
    except: pass
    return "未知名稱", "其他產業"

# ==========================================
# 3. 核心診斷邏輯 (含自動校正機制)
# ==========================================
def get_diagnostic_report(sid):
    try:
        clean_id = str(sid).split('.')[0].strip()
        stock_name, industry = get_stock_details(clean_id)
        
        stock_obj = None
        df = pd.DataFrame()

        for suffix in [".TW", ".TWO"]:
            target = f"{clean_id}{suffix}"
            temp_stock = yf.Ticker(target)
            df_test = temp_stock.history(period="10d")
            if not df_test.empty:
                stock_obj = temp_stock
                df = temp_stock.history(period="1y") 
                break
        
        if df.empty or stock_obj is None:
            return f"❌ 找不到 {clean_id} 的資料。"

        info = stock_obj.info
        latest = df.iloc[-1]
        curr_p = latest['Close']
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # 數據失真校正
        is_data_distorted = abs(bias_60) > 30
        if is_data_distorted:
            recent_df = df.iloc[-20:]
            high_1y = recent_df['High'].max()
            support_line = max(recent_df['Low'].min(), curr_p * 0.95)
            stop_loss = support_line * 0.97
            warning_msg = "⚠️ 數據校正\n"
        else:
            high_1y = df['High'].max()
            support_line = ma60
            stop_loss = ma60 * 0.97
            warning_msg = ""
        
        # 策略行為
        if bias_60 > 15 and not is_data_distorted: action = "❌過熱不追"
        elif -2 < bias_60 < 5 and rsi < 50: action = "🟡支撐試單"
        elif rsi > 60: action = "🔥強勢持有"
        elif rsi < 30: action = "📉超跌等待"
        else: action = "☁️觀望盤整"

        # 籌碼與營收
        yoy_str = "N/A"
        y_growth = info.get('revenueGrowth')
        if y_growth: yoy_str = f"{y_growth*100:.1f}%"

        chip_msg = "外/投:讀取中"
        try:
            dl = DataLoader()
            start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
            chip_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start_date)
            if not chip_df.empty:
                f_net = (chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum() - chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
                t_net = (chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum() - chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()) / 1000
                chip_msg = f"外:{int(pd.Series(f_net).fillna(0).iloc[0]):+d}/投:{int(pd.Series(t_net).fillna(0).iloc[0]):+d}"
        except: pass

        avg_vol_5d = df['Volume'].rolling(5).mean().iloc[-1]
        vol_2_percent = int(pd.Series(avg_vol_5d * 0.02).fillna(0).iloc[0])

        # 精簡格式報告
        report = (
            f"【{clean_id} {stock_name}】{warning_msg}"
            f"現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
            f"營收:{yoy_str} | {chip_msg}\n"
            f"🚩行動:{action}\n"
            f"🔔APP警示: 壓:{high_1y:.1f} / 支:{support_line:.1f} / 損:{stop_loss:.1f}\n"
            f"💡巨量: > {vol_2_percent} 張\n"
            f"-------------------"
        )
        return report
    except Exception as e:
        return f"❌ {sid} 錯誤: {str(e)}"

# ==========================================
# 4. 執行與合併發送
# ==========================================
if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    
    print(f"🚀 開始分析 {len(targets)} 檔個股...")
    combined_reports = []
    
    for t in targets:
        print(f"分析中: {t}")
        report = get_diagnostic_report(t.strip().upper())
        combined_reports.append(report)
        time.sleep(0.5)
    
    final_content = "📊 個股診斷集體報告 (" + datetime.date.today().strftime('%Y-%m-%d') + ")\n" + "="*20 + "\n"
    final_content += "\n".join(combined_reports)
    
    # 1. 存入 D:\Mega\下載\個股\
    save_to_txt(final_content)
    
    # 2. 推送到 LINE (一則訊息)
    send_line_message(final_content)
