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
# 2. 核心診斷邏輯
# ==========================================
def get_diagnostic_report(sid):
    try:
        # --- A. 代碼偵測強化 ---
        clean_id = str(sid).split('.')[0].strip()
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
        name = info.get('shortName', final_sid)
        latest = df.iloc[-1]
        
        # --- B. 技術面指標 ---
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        bias_60 = ((latest['Close'] - ma60) / ma60) * 100
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        bias_note = "⚠️ 噴發過熱" if bias_60 > 15 else ("🟢 支撐區" if -3 < bias_60 < 5 else "正常")
        trend_label = "🔥 強勢多頭" if latest['Close'] > ma60 else "☁️ 弱勢整理"

        # --- C. 殖利率修正 ---
        raw_yield = info.get('dividendYield')
        yield_val = (raw_yield if raw_yield and raw_yield > 0.5 else (raw_yield*100 if raw_yield else 0))

        # --- D. 籌碼面：法人參與度 ---
        dl = DataLoader()
        start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
        chip_df = dl.taiwan_stock_institutional_investors(stock_id=clean_id, start_date=start_date)
        
        chip_msg = "無資料"
        if not chip_df.empty:
            f_net = (chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum() - chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
            t_net = (chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum() - chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()) / 1000
            vol_today = latest['Volume'] / 1000
            f_ratio = (f_net / vol_today) * 100 if vol_today > 0 else 0
            chip_msg = (f"● 外資: {int(f_net):+d} 張 ({f_ratio:+.1f}% 參與)\n"
                        f"● 投信: {int(t_net):+d} 張 ({'🔴加碼' if t_net>0 else '🟢減碼'})")

        # --- E. 基本面：營收 YoY (萬用備援機制) ---
        rev_start = (datetime.date.today() - datetime.timedelta(days=150)).strftime('%Y-%m-%d')
        rev_df = dl.taiwan_stock_month_revenue(stock_id=clean_id, start_date=rev_start)
        yoy_str = "N/A"
        
        if not rev_df.empty:
            # 遍歷所有欄位，只要包含 growth 或 percent 就試試看
            target_cols = [c for c in rev_df.columns if any(x in c.lower() for x in ['growth', 'percent'])]
            
            # 從最新的月份往前檢查
            found = False
            for i in range(1, len(rev_df) + 1):
                row = rev_df.iloc[-i]
                for col in target_cols:
                    val = row[col]
                    if val != 0 and pd.notnull(val):
                        yoy_str = f"{int(row['revenue_month'])}月: {val:.2f}%"
                        found = True
                        break
                if found: break

        # --- F. 組合報告 ---
        pe = info.get('trailingPE')
        pe_status = "偏高" if (pe and pe > 25) else ("便宜" if (pe and 0 < pe < 12) else "合理")

        report = (
            f"=== {final_sid} ({name}) 診斷報告 ===\n"
            f"趨勢：{trend_label}\n"
            f"位階：60MA乖離 {bias_60:+.1f}% ({bias_note})\n"
            f"品質：{('🟢 獲利穩健' if (info.get('profitMargins',0) or 0) > 0.1 else '🔴 獲利待強')}\n\n"
            f"【籌碼面：法人動態】\n"
            f"{chip_msg}\n\n"
            f"【基本面：成長與估值】\n"
            f"● 營超 YoY: {yoy_str}\n"
            f"● 本益比 (P/E): {f'{pe:.1f}' if pe else 'N/A'} ({pe_status})\n"
            f"● 現金殖利率: {yield_val:.2f}%\n\n"
            f"【技術面：進場時機】\n"
            f"● 目前股價: {latest['Close']:.2f} ({((latest['Close']/df['Close'].iloc[-2])-1)*100:+.1f}%)\n"
            f"● 心理力道: RSI={rsi:.2f}\n"
            f"● 量能倍率: {latest['Volume']/df['Volume'].iloc[-11:-1].mean():.2f} 倍\n"
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
