import os, yfinance as yf, pandas as pd, requests, time, datetime
import numpy as np
import gspread
import json
from oauth2client.service_account import ServiceAccountCredentials
from FinMind.data import DataLoader

# ==========================================
# 設定與環境變數
# ==========================================
LINE_ACCESS_TOKEN = os.getenv("LINE_ACCESS_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID") or "U2e9b79c2f71cb2a3db62e5d75254270c"

def send_line(msg):
    if not LINE_ACCESS_TOKEN: return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": LINE_USER_ID, "messages": [{"type": "text", "text": msg}]}
    try: requests.post(url, headers=headers, json=payload)
    except: pass

def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 環境變數！")
        return None

    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"❌ 解析金鑰或連線失敗: {e}")
        return None

def sync_to_sheets(data_list):
    """將結果寫入 '法人精選監測' Google Sheets"""
    try:
        client = get_gspread_client()
        if not client: return 
        sheet = client.open("法人精選監測").get_worksheet(0)
        sheet.append_rows(data_list)
        print(f"✅ 成功同步 {len(data_list)} 筆數據至 '法人精選監測'")
    except Exception as e:
        print(f"⚠️ '法人精選監測' 同步失敗: {e}")

def update_watch_list_sheet(recommended_stocks, name_map):
    """將推薦標的匯入 'WATCH_LIST' 並檢查所有現有持股名稱"""
    try:
        client = get_gspread_client()
        if not client: return

        try:
            sheet = client.open("WATCH_LIST").worksheet("WATCH_LIST")
        except:
            sheet = client.open("WATCH_LIST").get_worksheet(0)

        # 1. 讀取現有資料並檢查名稱
        all_values = sheet.get_all_values()
        existing_ids = set()
        
        print(f"🔍 正在檢查 {len(all_values)-1} 筆現有庫存名稱...")
        
        for idx, row in enumerate(all_values):
            if idx == 0: continue # 跳過標題
            if not row: continue
            
            sid = str(row[0]).strip()
            current_name = str(row[1]).strip() if len(row) > 1 else ""
            
            existing_ids.add(sid)
            
            if sid in name_map:
                correct_name = name_map[sid]
                if not current_name or current_name != correct_name:
                    try:
                        sheet.update_cell(idx + 1, 2, correct_name)
                        print(f"🔄 更新股票名稱 ({sid}): '{current_name}' -> '{correct_name}'")
                    except Exception as e:
                        print(f"⚠️ 更新名稱失敗 ({sid}): {e}")

        # 2. 新增推薦股
        if recommended_stocks:
            new_rows = []
            tw_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
            now_str = tw_time.strftime('%Y-%m-%d %H:%M')

            print(f"📋 準備將 {len(recommended_stocks)} 檔潛力股匯入 WATCH_LIST...")

            for stock in recommended_stocks:
                sid = str(stock['id']).strip()
                name = name_map.get(sid, stock['name']) 
                reason = stock['reason']
                
                if sid not in existing_ids:
                    # 寫入格式: A:代號, B:名稱, C-E:空, F:理由, G:時間
                    new_rows.append([sid, name, "", "", "", reason, now_str])
                    existing_ids.add(sid) # 避免同批次重複

            if new_rows:
                sheet.append_rows(new_rows, value_input_option='RAW')
                print(f"✅ 已將 {len(new_rows)} 檔新標的加入 'WATCH_LIST'")
            else:
                print("ℹ️ 推薦標的已存在於 WATCH_LIST，無新增項目。")
        else:
            print("ℹ️ 今日無新推薦標的。")

    except Exception as e:
        print(f"⚠️ 更新 WATCH_LIST 失敗: {e}")

def get_streak_only(sid_clean):
    """獲取法人連買天數"""
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
    """計算 RSI 與 KD 指標"""
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
    """核心篩選邏輯：雙軌制 (穩健型 vs 飆股型)"""
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
        
        bias_5 = ((cp - ma5) / ma5) * 100
        status_label = "✅安全"
        if bias_5 > 7 or rsi_val > 75 or k_val > 85:
            status_label = "⚠️過熱"
        
        pure_id = ticker.split('.')[0]
        fs, ss = get_streak_only(pure_id)
        
        # 基礎報表生成門檻
        if (fs >= 2 or ss >= 1) and cp > ma60 and vol_ratio > 1.1:
            type_tag = "🌟投信認養" if ss >= 2 else "🔍法人掃貨"
            tw_today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')
            
            sheet_data = [
                tw_today, pure_id, name, type_tag, 
                fs, ss, round(vol_ratio, 2), status_label, 
                round(rsi_val, 1), round(k_val, 1), cp
            ]

            # 進階雙軌推薦邏輯
            recommendation = None
            is_stable = ((ss >= 2 or fs >= 3) and (vol_ratio > 1.2) and (50 <= rsi_val <= 75) and (k_val <= 80))
            is_aggressive = ((ss >= 1 or fs >= 2) and (vol_ratio > 2.5) and (rsi_val > 60) and (cp > ma5))

            if is_stable:
                reason = f"🛡️AI穩健: {type_tag} (量{vol_ratio:.1f}x/RSI{rsi_val:.0f})"
                recommendation = {'id': pure_id, 'name': name, 'reason': reason}
            elif is_aggressive:
                reason = f"🚀AI飆股: 爆量攻擊 (量{vol_ratio:.1f}x/外{fs}投{ss})"
                recommendation = {'id': pure_id, 'name': name, 'reason': reason}

            # 注意：這裡的 line_txt 因為不再推播詳細清單，故傳回 None 即可
            return None, sheet_data, recommendation

    except: return None, None, None
    return None, None, None

def main():
    dl = DataLoader()
    stock_df = None
    max_retries = 3
    print("📥 正在下載台股清單 (FinMind)...")
    
    for attempt in range(max_retries):
        try:
            stock_df = dl.taiwan_stock_info()
            if stock_df is not None and not stock_df.empty:
                print("✅ 台股清單下載成功")
                break
        except Exception as e:
            print(f"⚠️ FinMind 連線失敗 (第 {attempt+1}/{max_retries} 次): {e}")
            if attempt < max_retries - 1:
                print("⏳ 等待 5 秒後重試...")
                time.sleep(5)
            else:
                print("❌ 無法獲取台股清單。程式終止。")
                return

    if stock_df is None: return

    name_map = dict(zip(stock_df['stock_id'], stock_df['stock_name']))
    m_col = 'market_type' if 'market_type' in stock_df.columns else 'type'
    targets = stock_df[stock_df['stock_id'].str.len() == 4].head(1000) 
    
    sheet_results = []
    watch_list_candidates = []
    seen_ids = set()
    
    print(f"啟動雙軌策略掃描 (1000檔)...")
    
    for _, row in targets.iterrows():
        sid = row['stock_id']
        if sid in seen_ids: continue
        seen_ids.add(sid)
        
        if m_col and m_col in row:
            suffix = ".TWO" if '上櫃' in str(row[m_col]) or 'OTC' in str(row[m_col]) else ".TW"
        else:
            suffix = ".TWO" if int(sid) >= 8000 else ".TW"
            
        t = f"{sid}{suffix}"
        _, s_res, rec_obj = analyze_v14(t, row['stock_name'])
        
        if s_res:
            sheet_results.append(s_res)
        if rec_obj:
            watch_list_candidates.append(rec_obj)

        time.sleep(0.4)

    # 1. 同步至法人精選監測 Raw 報表
    if sheet_results:
        sync_to_sheets(sheet_results)

    # 2. 無條件執行 Watch List 的檢查與推薦股更新
    update_watch_list_sheet(watch_list_candidates, name_map)

    # 3. [修改重點] 簡化 LINE 推播：只推播精簡總結與雲端報表連結
    tw_date = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')
    
    # 📌 請將此處網址替換為您的實際 Google Sheets 共用連結
    monitor_sheet_url = "https://docs.google.com/spreadsheets/d/您的法人精選監測報表ID/edit"
    watch_list_url = "https://docs.google.com/spreadsheets/d/您的WATCH_LIST報表ID/edit"

    msg = (f"🔍 【{tw_date} 法人雙軌策略掃描完成】\n\n"
           f"今日 1000 檔股票篩選已順利結束！\n"
           f"📈 共篩選出 {len(sheet_results)} 檔符合法人多頭標的，並已自動過濾更新潛力股至您的雲端觀察名單。\n\n"
           f"🔗 點擊查看法人精選監測：\n{monitor_sheet_url}\n\n"
           f"📋 點擊查看最新 WATCH_LIST：\n{watch_list_url}")
    
    send_line(msg)
    print("✅ 簡化版 LINE 策略通知已發送")

if __name__ == "__main__":
    main()
