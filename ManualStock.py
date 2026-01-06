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
        print(message)
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

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
        # --- A. 代碼偵測與中文名稱強化 ---
        clean_id = str(sid).split('.')[0].strip()
        stock_name, industry = get_stock_details(clean_id)
        
        stock_obj = None
        df = pd.DataFrame()
        final_sid = clean_id

        for suffix in [".TW", ".TWO"]:
            target = f"{clean_id}{suffix}"
            temp_stock = yf.Ticker(target)
            df_test = temp_stock.history(period="5d")
            if not df_test.empty:
                stock_obj = temp_stock
                df = temp_stock.history(period="1y") 
                final_sid = target
                break
        
        if df.empty or stock_obj is None:
            return f"❌ 找不到 {clean_id} 的資料。"

        info = stock_obj.info
        latest = df.iloc[-1]
        curr_p = latest['Close']
        
        # --- B. 技術面指標 ---
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # --- C. 策略建議邏輯 ---
        high_1y = df['High'].max() # 壓力位
        stop_loss = ma60 * 0.97    # 停損位 (季線下破3%)
        
        if bias_60 > 15:
            action = "❌ 過熱不追 (等待回檔)"
        elif -2 < bias_60 < 5 and rsi < 50:
            action = "🟡 支撐區試單 (分批佈局)"
        elif rsi > 60:
            action = "🔥 強勢持有 (注意乖離)"
        else:
            action = "☁️ 觀望盤整 (等待轉強)"

        # --- D. 殖利率與營收 ---
        raw_yield = info.get('dividendYield')
        yield_val = (raw_yield if raw_yield and raw_yield > 0.5 else (raw_yield*100 if raw_yield else 0))

        yoy_str = "N/A"
        try:
            dl = DataLoader()
            rev_start = (datetime.date.today() - datetime.timedelta(days=150)).strftime('%Y-%m-%d')
            rev_df = dl.taiwan_stock_month_revenue(stock_id=clean_id, start_date=rev_start)
            if not rev_df.empty:
                target_cols = [c for c in rev_df.columns if any(x in c.lower() for x in ['growth', 'percent'])]
                found = False
                for i in range(1, len(rev_df) + 1):
                    row = rev_df.iloc[-i]
                    for col in target_cols:
                        if row[col] != 0:
                            yoy_str = f"{int(row['revenue_month'])}月: {row[col]:.2f}%"
                            found = True; break
                    if found: break
        except: pass
        
        if yoy_str == "N/A":
            y_growth = info.get('revenueGrowth')
            if y_growth: yoy_str = f"近期: {y_growth*100:.2f}% (YF)"

        # --- E. 籌碼面 ---
        chip_msg = "無資料"
        try:
            start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
            chip_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start_date)
            if not chip_df.empty:
                f_net = (chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum() - chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
                t_net = (chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum() - chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()) / 1000
                chip_msg = f"● 外資: {int(f_net):+d} 張 / 投信: {int(t_net):+d} 張"
        except: pass

        # --- F. APP 警示數據計算 (群益 APP 專用) ---
        avg_vol_5d = df['Volume'].rolling(5).mean().iloc[-1]
        moment_vol_trigger = int(avg_vol_5d * 0.02) # 對應「盤中瞬間巨量」單量 >= 5日均量 2%

        # --- G. 格式化報告 ---
        pe = info.get('trailingPE', 0)
        report = (
            f"=== {clean_id} {stock_name} 診斷報告 ===\n"
            f"產業：[{industry}]\n"
            f"趨勢：{'🔥 多頭' if curr_p > ma60 else '☁️ 弱勢'}\n"
            f"位階：60MA乖離 {bias_60:+.1f}%\n"
            f"品質：{'🟢 獲利穩健' if (info.get('profitMargins',0) or 0) > 0.1 else '🔴 待觀察'}\n\n"
            f"【關鍵數據】\n"
            f"● 營收 YoY: {yoy_str}\n"
            f"● 本益比: {f'{pe:.1f}' if pe else 'N/A'}\n"
            f"● 殖利率: {yield_val:.2f}%\n"
            f"{chip_msg}\n\n"
            f"【技術面指標】\n"
            f"● 目前股價: {curr_p:.2f} ({(curr_p/df['Close'].iloc[-2]-1)*100:+.2f}%)\n"
            f"● 心理力道: RSI={rsi:.2f}\n"
            f"● 量能倍率: {latest['Volume']/df['Volume'].iloc[-11:-1].mean():.2f} 倍\n\n"
            f"【🚀 實戰戰略指引】\n"
            f"● 建議行動：{action}\n"
            f"● 壓力參考：{high_1y:.1f}\n"
            f"● 支撐防線：{ma60:.1f}\n"
            f"● 停損保護：{stop_loss:.1f}\n\n"
            f"--- Alarm_Setting_Context ---\n"
            f"🔔 群益APP提示條件設定：\n"
            f"1. [上漲超過]：{high_1y:.1f}\n"
            f"2. [下跌超過]：{ma60:.1f}\n"
            f"3. [下跌超過(停損)]：{stop_loss:.1f}\n"
            f"4. [盤中瞬間巨量] 單量 >= {moment_vol_trigger} 張\n"
            f"-----------------------------\n"
            f"======================================="
        )
        return report

    except Exception as e:
        return f"❌ {sid} 診斷錯誤: {str(e)}"

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "6223"
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    for t in targets:
        report_msg = get_diagnostic_report(t.strip().upper())
        send_line_message(report_msg)
        time.sleep(1)
