import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time

# ==========================================
# 1. 環境與清單設定
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
WATCH_LIST = ["2330", "2317", "2882", "2886", "6223", "8069", "6770", "1101"]

def get_tw_stock(sid):
    clean_id = str(sid).strip().upper()
    for suffix in [".TW", ".TWO"]:
        target = f"{clean_id}{suffix}"
        stock = yf.Ticker(target)
        if not stock.history(period="1d").empty:
            return stock, target
    return None, None

# ==========================================
# 2. 核心指標計算邏輯 (比照您的欄位)
# ==========================================
def fetch_pro_data(sid):
    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    try:
        # 抓取 7 個月資料 (計算 6M 漲幅與還原價)
        hist = stock.history(period="7mo")
        info = stock.info
        
        # --- 價格指標 ---
        curr = hist['Close'].iloc[-1]
        prev = hist['Close'].iloc[-2]
        # 還原價 (yfinance history 已自動還原除權息)
        adj_price = curr 

        # --- 漲幅指標 ---
        d1 = ((curr / prev) - 1) * 100
        d5 = ((curr / hist['Close'].iloc[-6]) - 1) * 100
        m1 = ((curr / hist['Close'].iloc[-22]) - 1) * 100
        m6 = ((curr / hist['Close'].iloc[0]) - 1) * 100

        # --- 淨利率趨勢 (抓取最近兩季財報) ---
        quarterly_margins = []
        try:
            income_stmt = stock.quarterly_financials
            net_income = income_stmt.loc['Net Income']
            revenue = income_stmt.loc['Total Revenue']
            # 計算最近兩季淨利率
            margins = (net_income / revenue).iloc[:2].tolist()
            this_q_m = margins[0] * 100
            last_q_m = margins[1] * 100
            m_up = "Y" if this_q_m > last_q_m else "N"
        except:
            this_q_m = (info.get('profitMargins', 0) or 0) * 100
            last_q_m = 0
            m_up = "N/A"

        # --- 籌碼與量能 ---
        vol_5d = hist['Volume'].iloc[-6:-1].mean()
        vol_ratio = hist['Volume'].iloc[-1] / vol_5d if vol_5d > 0 else 0
        inst_own = info.get('heldPercentInstitutions', 0) * 100
        chip_trend = "🔥強勢" if inst_own > 30 else "🟢穩健"

        # --- 評分邏輯 (模擬 Total Score) ---
        score = 0
        if this_q_m > 0: score += 2
        if d1 > 0: score += 1
        if m6 > 0: score += 3
        if vol_ratio > 1.2: score += 1
        if info.get('trailingPE', 50) < 20: score += 3

        # 名稱對應
        name_map = {"TAIW": "台積電", "HON HAI": "鴻海", "CATHAY": "國泰金", "MEGA": "兆豐金", "TCC": "台泥", "POWERCHIP": "力積電", "MPI": "旺矽", "E INK": "元太"}
        short_name = info.get('shortName', sid).upper()
        c_name = sid
        for k, v in name_map.items():
            if k in short_name: c_name = v; break

        market = "市" if ".TW" in full_id else "櫃"

        return {
            "score": score, "risk": "低" if d1 < 3 else "高",
            "chip": chip_trend, "v_ratio": f"{vol_ratio:.1f}",
            "id": f"{sid}{market}", "name": c_name,
            "price": f"{curr:.1f}", "adj": f"{adj_price:.1f}",
            "pe_t": f"{info.get('trailingPE', 0):.1f}", "pe_f": f"{info.get('forwardPE', 0):.1f}",
            "m_q": f"{this_q_m:.1f}%", "m_l": f"{last_q_m:.1f}%", "m_up": m_up,
            "d1": f"{d1:+.1f}%", "d5": f"{d5:+.1f}%", "m1": f"{m1:+.1f}%", "m6": f"{m6:+.1f}%"
        }
    except: return None

# ==========================================
# 3. 訊息產出 (比照您的指定欄位)
# ==========================================
def main():
    results = []
    for sid in WATCH_LIST:
        data = fetch_pro_data(sid)
        if data: results.append(data)
        time.sleep(1)
    
    now = datetime.datetime.now().strftime("%Y/%m/%d")
    msg = f"🏆 【{now} 專業選股全指標】\n"
    
    for r in results:
        msg += f"━━━━━━━━━━━━━━\n"
        msg += f"【{r['name']} {r['id']}】 Score: {r['score']}\n"
        msg += f"風險評估: {r['risk']} | 籌碼: {r['chip']}\n"
        msg += f"量比(1D/5D): {r['v_ratio']}\n"
        msg += f"收盤價: {r['price']} (還原: {r['adj']})\n"
        msg += f"本益比(T/F): {r['pe_t']} / {r['pe_f']}\n"
        msg += f"淨利率(本/上): {r['m_q']} / {r['m_l']} ({r['m_up']})\n"
        msg += f"漲幅 1D:{r['d1']} | 5D:{r['d5']}\n"
        msg += f"漲幅 1M:{r['m1']} | 6M:{r['m6']}\n"
    
    msg += f"━━━━━━━━━━━━━━"
    
    # 發送至 LINE
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
    requests.post("https://api.line.me/v2/bot/message/push", 
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}, 
                  json=payload)

if __name__ == "__main__":
    main()
