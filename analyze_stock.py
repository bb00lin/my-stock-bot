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
    """發送訊息至指定 LINE User ID"""
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("Error: LINE_ACCESS_TOKEN or LINE_USER_ID not found.")
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
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"LINE 發送失敗: {e}")

# ==========================================
# 2. 核心診斷邏輯
# ==========================================
def get_diagnostic_report(sid):
    try:
        # --- A. 股票代碼格式自動適應 ---
        # 如果使用者輸入 2330，自動嘗試 2330.TW (上市) 或 2330.TWO (上櫃)
        suffixes = [".TW", ".TWO"] if "." not in sid else [""]
        stock_obj = None
        final_sid = sid
        df = pd.DataFrame()

        for s in suffixes:
            temp_sid = sid + s
            stock = yf.Ticker(temp_sid)
            df = stock.history(period="3mo")
            if not df.empty:
                stock_obj = stock
                final_sid = temp_sid
                break
        
        if df.empty:
            return f"❌ 找不到 {sid} 的有效交易資料，請確認代碼是否正確。"

        info = stock_obj.info
        name = info.get('shortName', final_sid)
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        # --- B. 技術指標計算 ---
        rsi = RSIIndicator(df['Close']).rsi().iloc[-1]
        # 量能倍率：今日成交量 / 過去10日平均成交量
        vol_ratio = latest['Volume'] / df['Volume'].iloc[-11:-1].mean()
        change_pct = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
        
        # --- C. 籌碼面 (FinMind) ---
        dl = DataLoader()
        stock_id_only = final_sid.split('.')[0]
        # 抓取最近 12 天資料以確保包含 5 個交易日
        start_date = (datetime.date.today() - datetime.timedelta(days=12)).strftime('%Y-%m-%d')
        
        chip_df = dl.taiwan_stock_institutional_investors(stock_id=stock_id_only, start_date=start_date)
        f_buy, t_buy = 0, 0
        if not chip_df.empty:
            # 統計期間內外資與投信的買賣差額 (換算為張數 / 1000)
            f_buy = (chip_df[chip_df['name'] == 'Foreign_Investor']['buy'].sum() - chip_df[chip_df['name'] == 'Foreign_Investor']['sell'].sum()) / 1000
            t_buy = (chip_df[chip_df['name'] == 'Investment_Trust']['buy'].sum() - chip_df[chip_df['name'] == 'Investment_Trust']['sell'].sum()) / 1000

        # --- D. 基本面：營收 YoY (修正補丁) ---
        rev_start = (datetime.date.today() - datetime.timedelta(days=90)).strftime('%Y-%m-%d')
        rev_df = dl.taiwan_stock_month_revenue(stock_id=stock_id_only, start_date=rev_start)
        yoy_str = "N/A"
        if not rev_df.empty:
            yoy_col = next((c for c in ['revenue_year_growth', 'revenue_year_growth_percent'] if c in rev_df.columns), None)
            # 優先讀取最新月份，若為 0 則往前遞補一月 (避免 API 資料同步延遲)
            last_rev = rev_df.iloc[-1]
            yoy_val = last_rev[yoy_col] if yoy_col else 0
            if yoy_val == 0 and len(rev_df) > 1:
                last_rev = rev_df.iloc[-2]
                yoy_val = last_rev[yoy_col] if yoy_col else 0
            yoy_str = f"{int(last_rev['revenue_month'])}月: {yoy_val:.2f}%"

        # --- E. 估值分析 (修正殖利率異常) ---
        pe = info.get('trailingPE', 0)
        pbr = info.get('priceToBook', 0)
        
        # 修正殖利率：yfinance 有時會給出配息金額而非比例
        yield_rate = info.get('dividendYield')
        if yield_rate and yield_rate > 0.5: # 數值 > 50% 顯然異常，進行校正
            yield_rate = yield_rate / latest['Close']
            
        pe_status = "合理偏高" if pe > 22 else ("合理" if pe > 12 else "合理偏低")
        pbr_status = "股價高估" if pbr > 3 else ("合理" if pbr > 1.2 else "價值低估")
        
        yield_str = f"{yield_rate * 100:.2f}%" if yield_rate else "N/A"
        yield_eval = "(高股息)" if yield_rate and yield_rate >= 0.05 else ""

        # --- F. 組合報告訊息 ---
        report = (
            f"=== {final_sid} ({name}) 診斷報告 ===\n\n"
            f"【籌碼面：大戶力道】(近5日)\n"
            f"● 外資: {int(f_buy)} 張 ({'🔴加碼' if f_buy>0 else '🟢減碼'})\n"
            f"● 投信: {int(t_buy)} 張 ({'🔴加碼' if t_buy>0 else '🟢減碼'})\n\n"
            f"【基本面：成長力道】\n"
            f"● 營收 YoY: {yoy_str}\n"
            f"● 本益比 (P/E): {round(pe, 2) if pe else 'N/A'} ({pe_status})\n"
            f"● 淨值比 (PBR): {round(pbr, 2) if pbr else 'N/A'} ({pbr_status})\n"
            f"● 現金殖利率: {yield_str} {yield_eval}\n\n"
            f"【技術面：進場時機】\n"
            f"● 目前股價: {latest['Close']:.2f} ({'+' if change_pct>0 else ''}{change_pct:.2f}%)\n"
            f"● 心理力道: RSI={rsi:.2f}\n"
            f"● 量能倍率: {vol_ratio:.2f} 倍\n"
            f"======================================="
        )
        return report

    except Exception as e:
        return f"❌ {sid} 診斷發生錯誤: {str(e)}"

# ==========================================
# 3. 執行進入點
# ==========================================
if __name__ == "__main__":
    # 支援 GitHub Actions 輸入參數，若無則預設診斷 2330
    input_str = sys.argv[1] if len(sys.argv) > 1 else "2330"
    
    # 處理換行、空格或逗號分隔的多筆輸入
    targets = input_str.replace('\n', ' ').replace(',', ' ').split()
    
    print(f"🔔 正在處理診斷任務: {targets}")
    
    for t in targets:
        ticker = t.strip().upper()
        report_msg = get_diagnostic_report(ticker)
        send_line_message(report_msg)
        print(f"✅ {ticker} 報告已送出")
        # 停頓 1.5 秒避免觸發 API 頻率限制
        time.sleep(1.5)
