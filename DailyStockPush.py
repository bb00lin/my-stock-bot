import os
import yfinance as yf
import pandas as pd
import requests
import datetime
import time
from FinMind.data import DataLoader

# ==========================================
# 1. 配置區域
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
WATCH_LIST = ["6770", "6706", "6684", "6271", "6269", "3105", "2538", "2014", "2010", "2002", "00992A", "00946"]
MIN_AMOUNT_HUNDRED_MILLION = 1.0 

# --- 新增：全局對照表 ---
def get_global_stock_info():
    """獲取台股全市場對應表"""
    try:
        dl = DataLoader()
        df = dl.taiwan_stock_info()
        # 建立 { '股票代碼': ('中文名稱', '產業別') } 的字典
        return {str(row['stock_id']): (row['stock_name'], row['industry_category']) for _, row in df.iterrows()}
    except:
        return {}

STOCK_INFO_MAP = get_global_stock_info()

def get_tw_stock(sid):
    clean_id = str(sid).strip().upper()
    for suffix in [".TW", ".TWO"]:
        target = f"{clean_id}{suffix}"
        stock = yf.Ticker(target)
        if not stock.history(period="1d").empty:
            return stock, target
    return None, None

def calculate_rsi(series, period=14):
    delta = series.diff()
    if delta.empty: return 50
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    if loss.iloc[-1] == 0: return 100
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# ==========================================
# 2. 進階指標抓取與評分
# ==========================================
def fetch_pro_metrics(sid):
    stock, full_id = get_tw_stock(sid)
    if not stock: return None
    
    try:
        df_hist = stock.history(period="7mo")
        info = stock.info
        curr_p = df_hist['Close'].iloc[-1]
        curr_vol = df_hist['Volume'].iloc[-1]
        
        # A. 金流計算
        today_amount = (curr_vol * curr_p) / 100_000_000
        avg_amount_5d = ((df_hist['Volume'].iloc[-5:] * df_hist['Close'].iloc[-5:]).mean()) / 100_000_000
        if today_amount < MIN_AMOUNT_HUNDRED_MILLION: return None

        # B. 技術面 RSI
        rsi_series = calculate_rsi(df_hist['Close'])
        curr_rsi = rsi_series.iloc[-1]
        rsi_status = "⚠️過熱" if curr_rsi > 75 else ("🟢穩健" if curr_rsi < 35 else "中性")

        # C. 淨利趨勢
        try:
            income_stmt = stock.quarterly_financials
            margins = (income_stmt.loc['Net Income'] / income_stmt.loc['Total Revenue']).iloc[:2].tolist()
            this_q_m, last_q_m = margins[0] * 100, margins[1] * 100
            m_trend = "📈Y" if this_q_m > last_q_m else "📉N"
        except:
            this_q_m, last_q_m, m_trend = (info.get('profitMargins', 0) or 0) * 100, 0, "N/A"
        
        # D. 殖利率邏輯
        raw_yield = info.get('dividendYield', 0)
        dividend_yield = (float(raw_yield) if raw_yield and raw_yield > 0.5 else (float(raw_yield)*100 if raw_yield else 0))

        # E. 籌碼動向
        inst_own = (info.get('heldPercentInstitutions', 0) or 0) * 100
        d1 = ((curr_p / df_hist['Close'].iloc[-2]) - 1) * 100
        chip_status = "🔴法人加碼" if d1 > 0 and inst_own > 30 else "🟢法人觀望"
        vol_ratio = curr_vol / df_hist['Volume'].iloc[-6:-1].mean()

        # F. 評分邏輯 (12分制)
        score = 0
        if this_q_m > 0: score += 2
        if ((curr_p / df_hist['Close'].iloc[0]) - 1) * 100 > 0: score += 3 # 6M 趨勢
        if "📈" in m_trend: score += 2
        if 3.0 < dividend_yield < 15.0: score += 2
        if 40 < curr_rsi < 70: score += 1
        if today_amount > 10: score += 1
        if vol_ratio > 2.0: score += 1

        # --- 新增：中文名稱與產業獲取 ---
        stock_name, industry = STOCK_INFO_MAP.get(str(sid), (sid, "其他/未知"))

        return {
            "score": score, "name": stock_name, "industry": industry,
            "id": f"{sid}{'市' if '.TW' in full_id else '櫃'}",
            "rsi": f"{curr_rsi:.1f} ({rsi_status})", "yield": f"{dividend_yield:.2f}%",
            "chip": chip_status, "vol_r": f"{vol_ratio:.1f}",
            "amt_t": f"{today_amount:.1f} 億", "amt_5d": f"{avg_amount_5d:.1f} 億",
            "p": f"{curr_p:.1f}", "m_q": f"{this_q_m:.1f}%", "m_up": m_trend,
            "d1": f"{d1:+.1f}%", "m1": f"{(((curr_p/df_hist['Close'].iloc[-22])-1)*100):+.1f}%", 
            "m6": f"{(((curr_p/df_hist['Close'].iloc[0])-1)*100):+.1f}%"
        }
    except Exception as e:
        print(f"Error {sid}: {e}")
        return None

# ==========================================
# 3. 執行發送
# ==========================================
def main():
    results = [fetch_pro_metrics(sid) for sid in WATCH_LIST]
    results = [r for r in results if r]
    results.sort(key=lambda x: x['score'], reverse=True)
    
    now = datetime.datetime.now().strftime("%Y/%m/%d")
    msg = f"🏆 【{now} 全能法人金流診斷】\n已過濾成交額 < {MIN_AMOUNT_HUNDRED_MILLION} 億標的\n"
    
    for r in results:
        gem = "💎 " if r['score'] >= 9 else ""
        msg += f"━━━━━━━━━━━━━━\n"
        msg += f"{gem}Total Score: {r['score']} | RSI: {r['rsi']}\n"
        msg += f"股票代碼: {r['id']} | 名稱: {r['name']}\n"
        msg += f"產業類別: {r['industry']}\n"
        msg += f"籌碼動向: {r['chip']} | 量比: {r['vol_r']}\n"
        msg += f"收盤價: {r['p']} | 殖利率: {r['yield']}\n"
        msg += f"今日金流: {r['amt_t']} | 5日均金: {r['amt_5d']}\n"
        msg += f"本季淨利: {r['m_q']} | 淨利上升: {r['m_up']}\n"
        msg += f"漲幅: 1D:{r['d1']} | 1M:{r['m1']} | 6M:{r['m6']}\n"
    
    msg += "━━━━━━━━━━━━━━\n註：RSI > 75 為過熱；產業與名稱已自動校正。"
    
    requests.post("https://api.line.me/v2/bot/message/push", 
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"},
                  json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]})

if __name__ == "__main__":
    main()
