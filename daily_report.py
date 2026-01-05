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

# 監控清單
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
        df = stock.history(period="7mo")
        info = stock.info
        curr = df['Close'].iloc[-1]
        prev = df['Close'].iloc[-2]
        
        # 取得公司名稱 (優先取 shortName)
        # 針對台股常見名稱做簡單清理
        raw_name = info.get('shortName', sid)
        # 簡單映射常見名稱，若 yfinance 回傳英文則可在此手動優化
        name_map = {
            "TSMC": "台積電", "HON HAI": "鴻海", "CATHAY": "國泰金", 
            "MEGA": "兆豐金", "TCC": "台泥", "POWERCHIP": "力積電"
        }
        name = raw_name
        for k, v in name_map.items():
            if k in raw_name.upper():
                name = v
                break
        
        # 漲跌幅計算
        d1 = ((curr / prev) - 1) * 100
        m1 = ((curr / df['Close'].iloc[-22]) - 1) * 100
        m6 = ((curr / df['Close'].iloc[0]) - 1) * 100
        
        # 指標判定
        margin = info.get('profitMargins', 0) or 0
        pe = info.get('trailingPE', 0) or 0
        yield_val = (info.get('dividendYield', 0) or 0) * 100
        
        is_gem = margin > 0 and yield_val >= 5.0 and m6 > 0
        status = "💎鑽石" if is_gem else ("🔥強勢" if m6 > 0 else "☁️盤整")
        if margin < 0: status = "⚠️虧損"

        # 標註上市櫃
        market_type = "市" if ".TW" in full_id and ".TWO" not in full_id else "櫃"

        return {
            "ID": f"{sid}{market_type}",
            "名稱": name[:4], # 取前四個字避免跑版
            "價格": round(curr, 1),
            "1D%": f"{d1:+.1f}%",
            "M1%": f"{m1:+.1f}%",
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
        time.sleep(1)
    
    if not results: return
    
    now = datetime.datetime.now().strftime("%Y/%m/%d")
    msg = f"📊 【{now} 台股追蹤】\n"
    msg += "━━━━━━━━━━━━━━\n"
    msg += "名稱 (代號) | 現價 | 1D | 狀態\n"
    
    for r in results:
        # 格式化輸出：公司名稱 (代號)
        msg += f"{r['名稱']}({r['ID']}) | {r['價格']} | {r['1D%']} | {r['狀態']}\n"
    
    msg += "━━━━━━━━━━━━━━\n"
    msg += "註：💎=獲利+高息+多頭"
    
    send_line_message(msg)

if __name__ == "__main__":
    main()
