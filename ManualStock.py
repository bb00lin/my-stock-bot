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
# 請確保環境變數已設定，或直接在此填入字串
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("\n--- 預覽報告內容 ---")
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
    """產生文字檔存檔"""
    date_str = datetime.date.today().strftime('%Y-%m-%d')
    filename = f"Stock_Report_{date_str}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"💾 文字檔已儲存: {filename}")

# ==========================================
# 2. 產業與名稱獲取 (FinMind 強化版)
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
    except:
        pass
    return "未知名稱", "其他產業"

# ==========================================
# 3. 核心診斷邏輯
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
        
        is_data_distorted = abs(bias_60) > 30
        if is_data_distorted:
            recent_df = df.iloc[-20:]
            high_1y = recent_df['High'].max()
            support_line = max(recent_df['Low'].min(), curr_p * 0.95)
            stop_loss = support_line * 0.97
            warning_msg = "⚠️ 數據異常(已自動校正)\n"
        else:
            high_1y = df['High'].max()
            support_line = ma60
            stop_loss = ma60 * 0.97
            warning_msg = ""
        
        if bias_60 > 15 and not is_data_distorted:
            action = "❌ 過熱不追"
        elif -2 < bias_60 < 5 and rsi < 50:
            action = "🟡 支撐區試單"
        elif rsi > 60:
            action = "🔥 強勢持有"
        elif rsi < 30:
            action = "📉 超跌等待"
        else:
            action = "☁️ 觀望盤整"

        raw_yield = info.get('dividendYield')
        yield_val = (raw_yield if raw_yield and raw_yield > 0.5 else (raw_yield*100 if raw_yield else 0))
        yoy_str = "N/A"
        y_growth = info.get('revenueGrowth')
        if y_growth: yoy_str = f"{y_growth*100:.1f}%"

        chip_msg = "外資/投信: 待查"
        try:
            dl = DataLoader()
            start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
            chip_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start_date)
            if not chip_df.empty:
                f_net = (chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum() - chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
                t_net = (chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum() - chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()) / 1000
                f_net = int(f_net) if pd.notnull(f_net) else 0
                t_net = int(t_net) if pd.notnull(t_net) else 0
                chip_msg = f"外:{f_net:+d} / 投:{t_net:+d}"
        except: pass

        avg_vol_5d = df['Volume'].rolling(5).mean().iloc[-1]
        vol_2_percent = int(avg_vol_5d * 0.02) if pd.notnull(avg_vol_5d) else 0

        report = (
            f"【{clean_id} {stock_name}】{warning_msg}"
            f"現價:{curr_p:.2f} | RSI:{rsi:.1f} | 乖離:{bias_60:+.1f}%\n"
            f"營收:{yoy_str} | 殖利率:{yield_val:.2f}% | {chip_msg}\n"
            f"🚩行動:{action}\n"
            f"🔔APP警示: 壓:{high_1y:.1f} / 支:{support_line:.1f} / 損:{stop_loss:.1f}\n"
            f"💡巨量張數: > {vol_2_percent} 張\n"
            f"-------------------"
        )
        return report
    except Exception as e:
        return f"❌ {sid} 錯誤: {str(e)}"

# ==========================================
# 4. 執行與發送
# ==========================================
if __name__ == "__main__":
    # 可以同時輸入多個代碼，例如: python ManualStock.py 2301,2303,2344
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    
    combined_reports = []
    print(f"🚀 開始診斷 {len(targets)} 檔標的...")
    
    for t in targets:
        print(f"正在分析 {t}...")
        report = get_diagnostic_report(t.strip().upper())
        combined_reports.append(report)
        time.sleep(1) # 避免抓取過快
    
    # 合併所有報告內容
    final_content = "📊 每日個股診斷集體報告\n" + "="*20 + "\n"
    final_content += "\n".join(combined_reports)
    
    # 1. 儲存文字檔
    save_to_txt(final_content)
    
    # 2. 推送到 LINE (一則長訊息只扣 1 點額度)
    send_line_message(final_content)
