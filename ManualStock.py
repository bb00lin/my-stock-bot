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
# 1. 環境設定 (已填入您的 LINE USER ID)
# ==========================================
# 請在這裡填入您的 LINE Channel Access Token
LINE_ACCESS_TOKEN = "你的_LINE_ACCESS_TOKEN_貼在這裡"
LINE_USER_ID = "U2e9b79c2f71cb2a3db62e5d75254270c" 

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or "你的" in LINE_ACCESS_TOKEN:
        print("\n⚠️ 錯誤：尚未設定 LINE_ACCESS_TOKEN，僅在本地顯示：")
        print(message)
        return
        
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json", 
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID, 
        "messages": [{"type": "text", "text": message}]
    }
    
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        print(f"❌ LINE 發送失敗，狀態碼：{response.status_code}")
        print(f"錯誤訊息：{response.text}")
    else:
        print(f"✅ 診斷報告已成功推送到 LINE")

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

        # 嘗試 TW (上市) 與 TWO (上櫃)
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
        
        # --- B. 技術面指標 ---
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((curr_p - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        
        # --- C. 壓力/支撐校正機制 (自動人工校正) ---
        is_data_distorted = abs(bias_60) > 30
        
        if is_data_distorted:
            recent_df = df.iloc[-20:]
            high_1y = recent_df['High'].max()
            support_line = max(recent_df['Low'].min(), curr_p * 0.95)
            stop_loss = support_line * 0.97
            warning_msg = "⚠️ 偵測到數據異常，已啟動人工智慧自動校正值。\n"
        else:
            high_1y = df['High'].max()
            support_line = ma60
            stop_loss = ma60 * 0.97
            warning_msg = ""
        
        # --- D. 策略建議邏輯 ---
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

        # --- E. 殖利率與營收 ---
        raw_yield = info.get('dividendYield')
        yield_val = (raw_yield if raw_yield and raw_yield > 0.5 else (raw_yield*100 if raw_yield else 0))
        yoy_str = "N/A"
        y_growth = info.get('revenueGrowth')
        if y_growth: yoy_str = f"近期: {y_growth*100:.2f}% (YF)"

        # --- F. 籌碼面 (NaN 防呆) ---
        chip_msg = "● 外資: +0 張 / 投信: +0 張"
        try:
            dl = DataLoader()
            start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
            chip_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start_date)
            if not chip_df.empty:
                f_net = (chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum() - chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
                t_net = (chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum() - chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()) / 1000
                f_net = int(f_net) if pd.notnull(f_net) else 0
                t_net = int(t_net) if pd.notnull(t_net) else 0
                chip_msg = f"● 外資: {f_net:+d} 張 / 投信: {t_net:+d} 張"
        except: pass

        # --- G. APP 警示數據參考 ---
        avg_vol_5d = df['Volume'].rolling(5).mean().iloc[-1]
        vol_2_percent = int(avg_vol_5d * 0.02) if pd.notnull(avg_vol_5d) else 0

        # --- H. 格式化報告 ---
        pe = info.get('trailingPE', 0)
        report = (
            f"=== {clean_id} {stock_name} 診斷報告 ===\n"
            f"{warning_msg}"
            f"產業：[{industry}]\n"
            f"趨勢：{'🔥 多頭' if curr_p > ma60 or is_data_distorted else '☁️ 弱勢'}\n"
            f"位階：60MA乖離 {bias_60:+.1f}% {'(數據斷層)' if is_data_distorted else ''}\n"
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
            f"● 支撐防線：{support_line:.1f}\n"
            f"● 停損保護：{stop_loss:.1f}\n\n"
            f"--- Alarm_Setting_Context ---\n"
            f"🔔 群益APP提示條件設定：\n"
            f"1. [上漲超過]：{high_1y:.1f}\n"
            f"2. [下跌超過]：{support_line:.1f}\n"
            f"3. [下跌超過]：{stop_loss:.1f}\n"
            f"💡 [盤中瞬間巨量] 已固定為5日均量2%，響起時單筆成交 > {vol_2_percent} 張\n"
            f"-----------------------------\n"
            f"======================================="
        )
        return report

    except Exception as e:
        return f"❌ {sid} 診斷錯誤: {str(e)}"

if __name__ == "__main__":
    # 預設執行華邦電測試，或接收外部參數
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2344"
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    for t in targets:
        report_msg = get_diagnostic_report(t.strip().upper())
        send_line_message(report_msg)
        time.sleep(1)
