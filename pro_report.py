import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time

# ==========================================
# 1. 配置區域
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
# 2. 指標抓取與 10 分制評分邏輯
# ==========================================
def fetch_pro_metrics(sid):
    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    
    try:
        df_hist = stock.history(period="7mo")
        info = stock.info
        curr_p = df_hist['Close'].iloc[-1]
        prev_p = df_hist['Close'].iloc[-2]
        
        # 漲幅計算
        d1, d5 = ((curr_p/prev_p)-1)*100, ((curr_p/df_hist['Close'].iloc[-6])-1)*100
        m1, m6 = ((curr_p/df_hist['Close'].iloc[-22])-1)*100, ((curr_p/df_hist['Close'].iloc[0])-1)*100
        
        # 淨利趨勢
        try:
            income_stmt = stock.quarterly_financials
            margins = (income_stmt.loc['Net Income'] / income_stmt.loc['Total Revenue']).iloc[:2].tolist()
            this_q_m, last_q_m = margins[0] * 100, margins[1] * 100
            m_trend = "📈Y" if this_q_m > last_q_m else "📉N"
        except:
            this_q_m, last_q_m, m_trend = (info.get('profitMargins', 0) or 0) * 100, 0, "N/A"

        # 估值與量能
        pe_t, pe_f = info.get('trailingPE', 0) or 0, info.get('forwardPE', 0) or 0
        vol_ratio = df_hist['Volume'].iloc[-1] / df_hist['Volume'].iloc[-6:-1].mean()
        
        # --- 評分權重優化 ---
        score = 0
        if this_q_m > 0: score += 2      # 基本面：有賺錢
        if m6 > 0: score += 3            # 動能面：半年線多頭
        if 0 < pe_t < 20: score += 2     # 估值面：PE合理
        if "📈" in m_trend: score += 2   # 成長面：獲利加速
        if vol_ratio > 1.2: score += 1   # 籌碼面：量能推升
        
        name_map = {"TAIW": "台積電", "HON HAI": "鴻海", "CATHAY": "國泰金", "MEGA": "兆豐金", "TCC": "台泥", "POWERCHIP": "力積電", "MPI": "旺矽", "E INK": "元太"}
        raw_name = info.get('shortName', sid).upper()
        c_name = sid
        for k, v in name_map.items():
            if k in raw_name: c_name = v; break

        return {
            "score": score, "name": c_name, "id": f"{sid}{'市' if '.TW' in full_id else '櫃'}",
            "risk": "高" if d1 > 3 or d5 > 8 else "低",
            "chip": "🔥強勢" if (info.get('heldPercentInstitutions', 0)*100) > 30 else "🟢穩健",
            "vol": f"{vol_ratio:.1f}", "p": f"{curr_p:.1f}", "adj": f"{curr_p:.1f}",
            "pe_t": f"{pe_t:.1f}" if pe_t else "N/A", "pe_f": f"{pe_f:.1f}" if pe_f else "N/A",
            "m_q": f"{this_q_m:.1f}%", "m_l": f"{last_q_m:.1f}%", "m_up": m_trend,
            "d1": f"{d1:+.1f}%", "d5": f"{d5:+.1f}%", "m1": f"{m1:+.1f}%", "m6": f"{m6:+.1f}%"
        }
    except: return None

# ==========================================
# 3. 排序與發送邏輯
# ==========================================
def main():
    results = [fetch_pro_metrics(sid) for sid in WATCH_LIST]
    results = [r for r in results if r]
    results.sort(key=lambda x: x['score'], reverse=True) # 依分數排序
    
    now = datetime.datetime.now().strftime("%Y/%m/%d")
    msg = f"🏆 【{now} 專業法人選股報表】\n排序依據：Total Score 綜合評分\n"
    
    for r in results:
        gem = "💎 " if r['score'] >= 8 else ""
        msg += f"━━━━━━━━━━━━━━\n"
        msg += f"{gem}Total Score: {r['score']} | 風險: {r['risk']}\n"
        msg += f"籌碼動向: {r['chip']} | 量比: {r['vol']}\n"
        msg += f"股票代碼: {r['id']} | 名稱: {r['name']}\n"
        msg += f"當日收盤價: {r['p']} (還原: {r['adj']})\n"
        msg += f"Trailing PE: {r['pe_t']} | Forward PE: {r['pe_f']}\n"
        msg += f"本季淨利率: {r['m_q']} | 上季: {r['m_l']}\n"
        msg += f"淨利率上升: {r['m_up']}\n"
        msg += f"漲幅：1D:{r['d1']} | 5D:{r['d5']}\n"
        msg += f"漲幅：1M:{r['m1']} | 6M:{r['m6']}\n"
    
    msg += "━━━━━━━━━━━━━━\n註：💎=Score 8分以上強烈建議關注"
    requests.post("https://api.line.me/v2/bot/message/push", 
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"},
                  json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]})

if __name__ == "__main__":
    main()
