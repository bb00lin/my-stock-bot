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
    print("\n" + "="*40)
    print(message)
    print("="*40)
    sys.stdout.flush()

    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    try:
        requests.post(url, headers=headers, json=payload)
    except: pass

# ==========================================
# 2. 產業與名稱獲取
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
        if pd.isna(ma60): ma60 = df['Close'].mean()
        
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi_series = RSIIndicator(df['Close']).rsi()
        rsi = rsi_series.iloc[-1] if not pd.isna(rsi_series.iloc[-1]) else 50.0
        
        is_data_distorted = abs(bias_60) > 30
        if is_data_distorted:
            recent_df = df.iloc[-20:]
            high_1y = recent_df['High'].max()
            support_line = max(recent_df['Low'].min(), curr_p * 0.95)
            stop_loss = support_line * 0.97
            warning_msg = "⚠️ 偵測到數據異常，已啟動校正值。\n"
        else:
            high_1y = df['High'].max()
            support_line = ma60
            stop_loss = ma60 * 0.97
            warning_msg = ""
        
        if bias_60 > 15 and not is_data_distorted:
            action = "❌ 過熱不追 (等待回檔)"
        elif -2 < bias_60 < 5 and rsi < 50:
            action = "🟡 支撐區試單 (分批佈局)"
        elif rsi > 60:
            action = "🔥 強勢持有 (注意乖離)"
        elif rsi < 30:
            action = "📉 超跌區 (等待反彈)"
        else:
            action = "☁️ 觀望盤整 (等待轉強)"

        raw_yield = info.get('dividendYield', 0)
        yield_val = (raw_yield if raw_yield and raw_yield > 0.5 else (raw_yield*100 if raw_yield else 0))
        y_growth = info.get('revenueGrowth')
        yoy_str = f"{y_growth*100:.2f}%" if y_growth else "N/A"

        f_net_val, t_net_val = 0, 0
        try:
            dl = DataLoader()
            start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
            chip_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start_date)
            if not chip_df.empty:
                f_buy = chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum()
                f_sell = chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()
                t_buy = chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum()
                t_sell = chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()
                f_net = (f_buy - f_sell) / 1000
                t_net = (t_buy - t_sell) / 1000
                f_net_val = int(f_net) if pd.notnull(f_net) else 0
                t_net_val = int(t_net) if pd.notnull(t_net) else 0
        except: pass
        chip_msg = f"● 外資: {f_net_val:+d} / 投信: {t_net_val:+d}"

        avg_vol_5d = df['Volume'].rolling(5).mean().iloc[-1]
        vol_2_percent = int((avg_vol_5d / 1000) * 0.02) if pd.notnull(avg_vol_5d) else 0
        if vol_2_percent < 1: vol_2_percent = 1

        report = (
            f"=== {clean_id} {stock_name} 診斷報告 ===\n"
            f"{warning_msg}"
            f"產業：[{industry}]\n"
            f"趨勢：{'🔥 多頭' if curr_p > ma60 else '☁️ 弱勢'}\n"
            f"位階：60MA乖離 {bias_60:+.1f}%\n"
            f"【關鍵數據】\n"
            f"● 營收YoY: {yoy_str} | 殖利率: {yield_val:.2f}%\n"
            f"{chip_msg}\n"
            f"【技術指標】\n"
            f"● 現價: {curr_p:.2f} | RSI: {rsi:.2f}\n"
            f"【🚀 實戰指引】\n"
            f"● 行動：{action}\n"
            f"● 壓力：{high_1y:.1f} / 支撐：{support_line:.1f}\n"
            f"● 停損：{stop_loss:.1f}\n\n"
            f"🔔 群益APP提示：\n"
            f"1. 上漲超過：{high_1y:.1f}\n"
            f"2. 下跌超過：{support_line:.1f}\n"
            f"💡 [盤中瞬間巨量] 代表單筆成交 > {vol_2_percent} 張\n"
            f"======================================="
        )
        return report

    except Exception as e:
        return f"❌ {sid} 診斷錯誤: {str(e)}"

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    
    all_reports = []
    current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    for t in targets:
        report = get_diagnostic_report(t.strip().upper())
        send_line_message(report)
        all_reports.append(report)
        time.sleep(1)
    
    # --- 雲端存檔功能 ---
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    filename = f"manual_report_{today_str}.txt"
    full_content = f"手動診斷時間: {current_time}\n" + "\n\n".join(all_reports)
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(full_content)
    
    # 更新最新檔案標記
    with open("latest_manual_report.txt", "w", encoding="utf-8") as f:
        f.write(f"最新手動診斷日期: {today_str}\n請查看 {filename}")
