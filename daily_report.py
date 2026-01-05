import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time

# ==========================================
# 1. 環境設定與監控清單
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")

# 只需要輸入數字代號，代碼會自動判斷 .TW 或 .TWO
WATCH_LIST = ["2330", "2317", "2882", "2886", "6223", "8069", "6770", "1101"]

def send_line_message(message):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]}
    requests.post(url, headers=headers, json=payload)

# ==========================================
# 2. 自動判斷上市櫃邏輯
# ==========================================
def get_tw_stock(sid):
    clean_id = str(sid).strip().upper()
    for suffix in [".TW", ".TWO"]:
        target = f"{clean_id}{suffix}"
        stock = yf.Ticker(target)
        # 抓取 1 天資料驗證是否存在
        if not stock.history(period="1d").empty:
            return stock, target
    return None, None

# ==========================================
# 3. 抓取指標與生成報告
# ==========================================
def fetch_metrics(sid):
    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    
    try:
        # 抓取半年資料計算動能
        df = stock.history(period="7mo")
        info = stock.info
        curr = df['Close'].iloc[-1]
        prev = df['Close'].iloc[-2]
        
        # 漲跌幅計算
        d1 = ((curr / prev) - 1) * 100
        m1 = ((curr / df['Close'].iloc[-22]) - 1) * 100
        m6 = ((curr / df['Close'].iloc[0]) - 1) * 100
        
        # 基本面指標
        margin = info.get('profitMargins', 0) or 0
        pe = info.get('trailingPE', 0) or 0
        yield_val = (info.get('dividendYield', 0) or 0) * 100
        
        # 藍鑽石與狀態判定邏輯
        # 條件：獲利穩健(Margin>0) + 高息(Yield>=5%) + 非空頭(M6>0)
        is_gem = margin > 0 and yield_val >= 5.0 and m6 > 0
        status = "💎鑽石" if is_gem else ("🔥強勢" if m6 > 0 else "☁️盤整")
        if margin < 0: status = "⚠️虧損"

        return {
            "ID": full_id.replace(".TWO", "櫃").replace(".TW", "市"),
            "價格": round(curr, 1),
            "1D%": f"{d1:+.1f}%",
            "M1%": f"{m1:+.1f}%",
            "殖利率": f"{yield_val:.1f}%",
            "狀態": status
        }
    except Exception as e:
        print(f"Error {sid}: {e}")
        return None

def main():
    results = []
    for sid in WATCH_LIST:
        data = fetch_metrics(sid)
        if data: results.append(data)
        time.sleep(1) # 避免 API 頻率限制
    
    if not results: return
    
    now = datetime.datetime.now().strftime("%Y/%m/%d")
    msg = f"📊 【{now} 台股多指標追蹤】\n"
    msg += "━━━━━━━━━━━━━━\n"
    msg += "代號 | 現價 | 1D | 1M | 狀態\n"
    
    for r in results:
        msg += f"{r['ID']} | {r['價格']} | {r['1D%']} | {r['M1%']} | {r['狀態']}\n"
    
    msg += "━━━━━━━━━━━━━━\n"
    msg += "註：💎=獲利+高息+多頭"
    
    send_line_message(msg)

if __name__ == "__main__":
    main()
