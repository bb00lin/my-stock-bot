import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time
import sys
from FinMind.data import DataLoader
from ta.momentum import RSIIndicator

LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

def get_diagnostic_report(sid):
    try:
        # 1. 技術面與公司名稱 (自動切換 .TW / .TWO)
        stock = yf.Ticker(sid)
        df = stock.history(period="3mo")
        
        # 如果抓不到資料且是 .TW，自動嘗試 .TWO (上櫃)
        if df.empty and ".TW" in sid:
            sid = sid.replace(".TW", ".TWO")
            stock = yf.Ticker(sid)
            df = stock.history(period="3mo")
            
        if df.empty: return f"❌ 找不到 {sid} 的有效交易資料"
        
        info = stock.info
        name = info.get('shortName', sid)
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        vol_ratio = latest['Volume'] / df['Volume'].iloc[-11:-1].mean()
        change_pct = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
        
        # 2. 籌碼面 (FinMind)
        dl = DataLoader()
        stock_id_only = sid.split('.')[0]
        start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
        
        chip_df = dl.taiwan_stock_institutional_investors(stock_id=stock_id_only, start_date=start_date)
        foreign_buy, trust_buy = 0, 0
        if not chip_df.empty:
            foreign_buy = (chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum() - chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
            trust_buy = (chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum() - chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()) / 1000

        # 3. 基本面：營收 YoY (修正欄位名稱報錯)
        rev_start = (datetime.date.today() - datetime.timedelta(days=65)).strftime('%Y-%m-%d')
        rev_df = dl.taiwan_stock_month_revenue(stock_id=stock_id_only, start_date=rev_start)
        yoy_str = "N/A"
        if not rev_df.empty:
            last_rev = rev_df.iloc[-1]
            # 自動偵測欄位名稱，避免 KeyError
            yoy_col = 'revenue_year_growth' if 'revenue_year_growth' in rev_df.columns else 'revenue_year_growth_percent'
            yoy_val = last_rev.get(yoy_col, 0)
            yoy_str = f"{int(last_rev['revenue_month'])}月: {yoy_val:.2f}%"

        # 4. 格式化輸出
        pe = info.get('trailingPE', 0)
        pe_status = "合理偏高" if pe > 22 else ("合理" if pe > 12 else "合理偏低")
        
        report = (
            f"=== {sid} ({name}) 診斷報告 ===\n\n"
            f"【籌碼面：大戶力道】(近5日)\n"
            f"● 外資: {int(foreign_buy)} 張 ({'🔴加碼' if foreign_buy>0 else '🟢減碼'})\n"
            f"● 投信: {int(trust_buy)} 張 ({'🔴加碼' if trust_buy>0 else '🟢減碼'})\n\n"
            f"【基本面：成長力道】\n"
            f"● 營收 YoY: {yoy_str}\n"
            f"● 本益比 (P/E): {round(pe, 2) if pe else 'N/A'} ({pe_status})\n\n"
            f"【技術面：進場時機】\n"
            f"● 目前股價: {latest['Close']:.2f} ({'+' if change_pct>0 else ''}{change_pct:.2f}%)\n"
            f"● 心理力道: RSI={rsi:.2f}\n"
            f"● 量能倍率: {vol_ratio:.2f} 倍\n"
            f"======================================="
        )
        return report
    except Exception as e:
        return f"❌ {sid} 診斷過程發生錯誤: {str(e)}"

if __name__ == "__main__":
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2330.TW"
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    
    for t in targets:
        ticker = t.strip().upper()
        # 初步修正格式
        if "TW" in ticker and "." not in ticker:
            ticker = ticker.replace("TW", ".TW")
        elif "." not in ticker: # 純數字則預設加 .TW
            ticker = ticker + ".TW"
            
        report = get_diagnostic_report(ticker)
        send_line_message(report)
        time.sleep(1)
