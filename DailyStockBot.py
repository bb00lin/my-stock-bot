import os, yfinance as yf, pandas as pd, requests, time, datetime, sys
import numpy as np
import gspread
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 1. 設定與環境變數
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# 初始化 Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    ai_model = genai.GenerativeModel('gemini-1.5-flash')

def send_line(msg):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
    try: requests.post(url, headers=headers, json=payload)
    except: pass

def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name('google_key.json', scope)
    return gspread.authorize(creds)

def get_gemini_advice(s):
    """
    將數據發送給 Gemini 生成精簡操作建議
    """
    if not GEMINI_API_KEY: return "AI 未啟動"
    
    prompt = f"""
    你是一位台股操盤手。請根據數據提供「操作建議」(50字內)。
    標的：{s['name']} ({s['id']}) | 現價：{s['cp']}
    狀態：{s['status']} | 乖離率：{s['bias']} | 量能：{s['vol_str']}
    籌碼：外資{s['fs']}連買 | 投信{s['ss']}連買 | RSI：{s['rsi']}
    
    請回答：
    1. 關鍵防守價或目標價。
    2. 明日看盤重點(如補量或過高)。
    3. 給出簡評(如:強勢續抱、拉回佈局)。
    """
    try:
        response = ai_model.generate_content(prompt)
        return response.text.replace('\n', ' ').strip()
    except:
        return "AI 分析異常"

def sync_to_sheets(data_list):
    """將結果寫入 '法人精選監測' Google Sheets"""
    try:
        client = get_gspread_client()
        sheet = client.open("法人精選監測").get_worksheet(0)
        # 欄位順序對應: 
        # A:日期, B:代號, C:名稱, D:標籤, E:乖離%, F:量能狀態, 
        # G:外資, H:投信, I:量比, J:狀態, K:RSI, L:K值, M:現價, N:AI策略
        sheet.append_rows(data_list, value_input_option='USER_ENTERED')
        print(f"✅ 成功同步 {len(data_list)} 筆數據至 '法人精選監測'")
    except Exception as e:
        print(f"⚠️ '法人精選監測' 同步失敗: {e}")

def update_watch_list_sheet(recommended_stocks):
    """將推薦標的匯入 'WATCH_LIST'"""
    if not recommended_stocks: return

    try:
        client = get_gspread_client()
        try:
            sheet = client.open("WATCH_LIST").worksheet("WATCH_LIST")
        except:
            sheet = client.open("WATCH_LIST").get_worksheet(0)

        existing_records = sheet.get_all_records()
        existing_ids = set(str(row.get('股票代號', '')).strip() for row in existing_records)
        
        new_rows = []
        today_str = datetime.date.today().strftime('%Y-%m-%d')

        print(f"📋 準備將 {len(recommended_stocks)} 檔潛力股匯入 WATCH_LIST...")

        for stock in recommended_stocks:
            sid = stock['id']
            if sid not in existing_ids:
                reason_note = f"{today_str} {stock['reason']}"
                new_rows.append([sid, "", "", "", "", reason_note])
                existing_ids.add(sid)

        if new_rows:
            sheet.append_rows(new_rows, value_input_option='USER_ENTERED')
            print(f"✅ 已將 {len(new_rows)} 檔新標的加入 'WATCH_LIST'")
        else:
            print("ℹ️ 推薦標的已存在於 WATCH_LIST，無新增項目。")

    except Exception as e:
        print(f"⚠️ 更新 WATCH_LIST 失敗: {e}")

def get_streak_only(sid_clean):
    try:
        dl = DataLoader()
        start = (datetime.date.today() - datetime.timedelta(days=20)).strftime('%Y-%m-%d')
        df = dl.taiwan_stock_institutional_investors(stock_id=sid_clean, start_date=start)
        if df is None or df.empty: return 0, 0
        
        def count_s(name):
            d = df[df['name'] == name].sort_values('date', ascending=False)
            c = 0
            for _, r in d.iterrows():
                if (r['buy'] - r['sell']) > 0: c += 1
                else: break
            return c
        return count_s('Foreign_Investor'), count_s('Investment_Trust')
    except: return 0, 0

def calculate_indicators(df):
    close = df['Close']
    low_min = df['Low'].rolling(window=9).min()
    high_max = df['High'].rolling(window=9).max()
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = 100 - (100 / (1 + (gain / loss)))
    
    rsv = (close - low_min) / (high_max - low_min) * 100
    k = rsv.ewm(com=2).mean() 
    d = k.ewm(com=2).mean()
    
    return rsi, k, d

def analyze_v14(ticker, name):
    """核心篩選邏輯：雙軌制 + 深度指標 + AI 診斷 (修正欄位對齊)"""
    try:
        s = yf.Ticker(ticker)
        i = s.info
        m = i.get('grossMargins', 0) or 0
        e = i.get('trailingEps', 0) or 0
        if m < 0.10 or e <= 0: return None, None, None

        df = s.history(period="1y")
        if len(df) < 60: return None, None, None
        
        cp = df.iloc[-1]['Close']
        ma5 = df['Close'].rolling(5).mean().iloc[-1]
        ma60 = df['Close'].rolling(60).mean().iloc[-1]
        
        rsi_series, k_series, d_series = calculate_indicators(df)
        rsi_val = rsi_series.iloc[-1]
        k_val = k_series.iloc[-1]
        
        vol_today = df.iloc[-1]['Volume']
        vol_avg = df['Volume'].iloc[-11:-1].mean()
        vol_ratio = vol_today / vol_avg if vol_avg > 0 else 0
        
        # 量能狀態 (文字)
        if vol_ratio > 1.8: vol_str = f"🔥爆量({vol_ratio:.1f}x)"
        elif vol_ratio > 1.0: vol_str = f"📈溫和({vol_ratio:.1f}x)"
        elif vol_ratio < 0.7: vol_str = f"⚠️縮量({vol_ratio:.1f}x)"
        else: vol_str = f"☁️量平({vol_ratio:.1f}x)"
        
        # 乖離率
        bias_60 = ((cp - ma60) / ma60) * 100
        bias_5 = ((cp - ma5) / ma5) * 100
        
        kd_status = "高檔" if k_val > 80 else ("低檔" if k_val < 20 else "穩定")
        
        status_label = "✅安全"
        if bias_5 > 7 or rsi_val > 75 or k_val > 85:
            status_label = "⚠️過熱"
        
        status_msg = f"{status_label}(乖離{bias_60:.1f}%|RSI:{rsi_val:.0f}|K:{k_val:.0f})"

        pure_id = ticker.split('.')[0]
        fs, ss = get_streak_only(pure_id)
        
        # --- 基礎報表生成 (符合基本面+技術面) ---
        if (fs >= 2 or ss >= 1) and cp > ma60 and vol_ratio > 1.1:
            type_tag = "🌟投信認養" if ss >= 2 else "🔍法人掃貨"
            
            # 準備數據給 AI 進行診斷
            stock_info = {
                'id': pure_id, 'name': name, 'cp': round(cp, 2), 
                'status': status_label, 'bias': f"{bias_60:+.1f}%", 
                'vol_str': vol_str, 'fs': fs, 'ss': ss, 'rsi': round(rsi_val, 1)
            }
            ai_advice = get_gemini_advice(stock_info)
            
            # LINE 訊息
            line_txt = (f"📍{ticker} {name} ({type_tag})\n"
                        f"量能：{vol_str}\n"
                        f"狀態：{status_msg}\n"
                        f"現價：{cp:.2f}\n"
                        f"💡AI策略：{ai_advice[:20]}...\n"
                        f"-----------------------------------")
            
            # Sheet 數據格式 (確保符合 A-N 欄位)
            sheet_data = [
                str(datetime.date.today()),  # A: 日期
                pure_id,                     # B: 代碼
                name,                        # C: 名稱
                type_tag,                    # D: 標籤
                f"{bias_60:+.1f}%",          # E: 乖離%
                vol_str,                     # F: 量能狀態
                fs,                          # G: 外資連買
                ss,                          # H: 投信連買
                round(vol_ratio, 2),         # I: 量比 (新增，讓後面順延)
                status_label,                # J: 狀態
                round(rsi_val, 1),           # K: RSI
                round(k_val, 1),             # L: K值
                cp,                          # M: 現價 (現在對齊了)
                ai_advice                    # N: AI 投資策略
            ]

            # --- 進階 AI 雙軌推薦邏輯 (匯入 WATCH_LIST) ---
            recommendation = None
            is_stable = ((ss >= 2 or fs >= 3) and (vol_ratio > 1.2) and (50 <= rsi_val <= 75) and (k_val <= 80))
            is_aggressive = ((ss >= 1 or fs >= 2) and (vol_ratio > 2.5) and (rsi_val > 60) and (cp > ma5))

            if is_stable:
                reason = f"🛡️AI穩健: {ai_advice}"
                recommendation = {'id': pure_id, 'reason': reason}
            elif is_aggressive:
                reason = f"🚀AI飆股: {ai_advice}"
                recommendation = {'id': pure_id, 'reason': reason}

            return line_txt, sheet_data, recommendation

    except: return None, None, None
    return None, None, None

def main():
    dl = DataLoader()
    stock_df = dl.taiwan_stock_info()
    m_col = 'market_type' if 'market_type' in stock_df.columns else 'type'
    
    targets = stock_df[stock_df['stock_id'].str.len() == 4].head(1000) 
    
    line_results = []
    sheet_results = []
    watch_list_candidates = []

    seen_ids = set()
    print(f"啟動雙軌策略+AI智能診斷掃描 (1000檔)...")
    
    for _, row in targets.iterrows():
        sid = row['stock_id']
        if sid in seen_ids: continue
        seen_ids.add(sid)
        
        if m_col and m_col in row:
            suffix = ".TWO" if '上櫃' in str(row[m_col]) or 'OTC' in str(row[m_col]) else ".TW"
        else:
            suffix = ".TWO" if int(sid) >= 8000 else ".TW"
            
        t = f"{sid}{suffix}"
        l_res, s_res, rec_obj = analyze_v14(t, row['stock_name'])
        
        if l_res:
            line_results.append(l_res)
            sheet_results.append(s_res)
        
        if rec_obj:
            watch_list_candidates.append(rec_obj)

        time.sleep(0.4)

    if sheet_results:
        sync_to_sheets(sheet_results)

    if watch_list_candidates:
        update_watch_list_sheet(watch_list_candidates)

    if line_results:
        msg = f"🔍 【{datetime.date.today()} 法人精選+AI診斷】\n\n" + "\n".join(line_results)
        send_line(msg)
    else:
        print("今日無符合標的。")

if __name__ == "__main__":
    main()
