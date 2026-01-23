import os
import re
import time
import json
import random
import pandas as pd
import gspread
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from gspread_formatting import *
from gspread.exceptions import APIError

# ================= 設定區 =================

# 您的真實檔案連結
DB_SHEET_URL = "https://docs.google.com/spreadsheets/d/1ovCEzxlz-383PLl4Dmtu8GybxfxI7tCKOl-6-oXNRa0/edit?usp=sharing"

# Input 分頁名稱
INPUT_SHEET_NAME = "Input_BOM"

# Gemini API Key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 分頁關鍵字映射
SHEET_MAP = {
    "RES": ["RES", "OHM", "Ω", "RESISTOR"],
    "MLCC(TMTC)": ["CAP", "UF", "NF", "PF", "CERAMIC", "MLCC"],
    "E-CAP": ["ELECTROLYTIC", "ALUMINUM", "TANTALUM"],
    "bead and inductor": ["INDUCTOR", "BEAD", "COIL", "UH", "MH", "NH"],
    "diode and transistor": ["DIODE", "TRANSISTOR", "MOSFET", "RECTIFIER"],
    "IC": ["IC", "MCU", "CPU", "CHIP"],
    "Connectors": ["CONN", "HEADER", "JACK", "USB", "SOCKET"],
    "switch and fuse": ["SWITCH", "FUSE", "BUTTON"],
    "Led_Xtal": ["LED", "CRYSTAL", "XTAL", "OSCILLATOR"]
}

# 顏色庫
PASTEL_COLORS = [
    {"red": 1.0, "green": 1.0, "blue": 0.8}, # 淺黃
    {"red": 0.8, "green": 1.0, "blue": 0.8}, # 淺綠
    {"red": 0.8, "green": 0.9, "blue": 1.0}, # 淺藍
    {"red": 1.0, "green": 0.8, "blue": 0.8}, # 淺紅
    {"red": 0.9, "green": 0.8, "blue": 1.0}, # 淺紫
]

# ================= 輔助工具：自動重試機制 =================

def retry_with_backoff(retries=5, delay=2):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for i in range(retries):
                try:
                    return func(*args, **kwargs)
                except APIError as e:
                    if "429" in str(e) or "Quota exceeded" in str(e):
                        wait_time = delay * (2 ** i) + random.uniform(0, 1)
                        print(f"⏳ Quota hit. Sleeping {wait_time:.1f}s...", flush=True)
                        time.sleep(wait_time)
                    else:
                        raise e
            raise Exception("Max retries exceeded.")
        return wrapper
    return decorator

# ================= 類別定義 =================

class GeminiBrain:
    def __init__(self, api_key):
        if api_key:
            try:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel('gemini-pro')
                print("✅ Gemini AI Connected.")
            except Exception as e:
                print(f"⚠️ Gemini Init Failed: {e}")
                self.model = None
        else:
            self.model = None
            print("⚠️ Warning: No Gemini API Key found.")

    def classify_component_fallback(self, description, value):
        if not self.model: return "Others"
        prompt = f"""
        Act as an electronic component expert.
        Database sheets: {list(SHEET_MAP.keys())}.
        Item: {description} (Value: {value})
        Which sheet does this belong to? Return ONLY the sheet name. If unknown, return 'Others'.
        """
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except:
            return "Others"

class DatabaseManager:
    def __init__(self, client, sheet_url):
        self.client = client
        self.workbook = self.client.open_by_url(sheet_url)
        self.sheet_cache = {} 
        self.headers_cache = {}

    def get_sheet_df(self, sheet_name):
        if sheet_name not in self.sheet_cache:
            try:
                worksheet = self.workbook.worksheet(sheet_name)
                all_values = worksheet.get_all_values()
                if not all_values:
                    return pd.DataFrame(), []
                
                headers = all_values[0]
                # 簡單處理重複標題問題
                unique_headers = []
                seen = {}
                for h in headers:
                    clean_h = str(h).strip()
                    if clean_h in seen:
                        seen[clean_h] += 1
                        unique_headers.append(f"{clean_h}_{seen[clean_h]}")
                    else:
                        seen[clean_h] = 0
                        unique_headers.append(clean_h)
                
                df = pd.DataFrame(all_values[1:], columns=unique_headers)
                df['_row_index'] = range(2, len(all_values) + 2)
                
                self.sheet_cache[sheet_name] = df
                self.headers_cache[sheet_name] = unique_headers 
            except gspread.exceptions.WorksheetNotFound:
                return pd.DataFrame(), []
        return self.sheet_cache[sheet_name], self.headers_cache[sheet_name]

    def find_best_matches(self, sheet_name, mpn, description, value):
        df, headers = self.get_sheet_df(sheet_name)
        if df.empty: return [], "None"

        matches = []
        mpn_str = str(mpn).strip().upper()
        desc_str = str(description).strip().upper()
        val_str = str(value).strip().upper()

        # 1. MPN 精確比對
        mpn_col = next((c for c in df.columns if 'MPN' in c.upper() or 'PART' in c.upper() or 'PN' in c.upper()), None)
        if mpn_col and mpn_str:
            found = df[df[mpn_col].astype(str).str.strip().str.upper() == mpn_str]
            if not found.empty:
                for _, row in found.iterrows():
                    matches.append({'row': row['_row_index'], 'data': row})
                return matches, "Exact Match (MPN)"

        # 2. 嚴格規格比對
        def is_structure_match(str1, str2):
            if not str1 or not str2: return False
            has_space1 = ' ' in str1
            has_space2 = ' ' in str2
            if len(str1) > 8 and len(str2) > 8:
                if has_space1 != has_space2:
                    return False 
            return True

        candidates = []
        desc_keywords = set(re.split(r'[\s,\-_/]+', desc_str))
        desc_col = next((c for c in df.columns if 'DESC' in c.upper()), None)
        val_col = next((c for c in df.columns if 'VAL' in c.upper()), None)

        if not desc_col: return [], "None"

        for _, row in df.iterrows():
            row_desc = str(row[desc_col]).upper()
            row_val = str(row[val_col]).upper() if val_col else ""
            
            score = 0
            if val_str:
                if val_str == row_val: score += 20
                elif val_str in row_desc: score += 15
                else: continue

            if not is_structure_match(desc_str, row_desc):
                continue 

            hit_count = 0
            for word in desc_keywords:
                if len(word) > 2 and word in row_desc:
                    hit_count += 1
            score += hit_count

            if score >= 18: 
                candidates.append({'row': row['_row_index'], 'data': row, 'score': score})

        candidates.sort(key=lambda x: x['score'], reverse=True)
        if candidates:
            return candidates[:3], "Parametric Match"
        
        return [], "None"

    @retry_with_backoff(retries=5, delay=2)
    def organize_and_insert(self, sheet_name, existing_rows, input_row_dict):
        ws = self.workbook.worksheet(sheet_name)
        
        # 動態欄位檢查
        current_headers = self.headers_cache.get(sheet_name, [])
        if not current_headers:
            current_headers = ws.row_values(1)
            clean_headers = [] 
            for h in current_headers:
                if h: clean_headers.append(str(h).strip())
            current_headers = clean_headers

        missing_cols = []
        header_map = {} 
        db_header_index = {h.upper(): i for i, h in enumerate(current_headers)}
        
        for key in input_row_dict.keys():
            if not key: continue
            u_key = key.upper()
            if u_key in db_header_index:
                header_map[key] = db_header_index[u_key]
            else:
                missing_cols.append(key)

        if missing_cols:
            print(f"      🆕 Creating new columns: {missing_cols}")
            start_col_idx = len(current_headers) + 1
            range_start = gspread.utils.rowcol_to_a1(1, start_col_idx)
            range_end = gspread.utils.rowcol_to_a1(1, start_col_idx + len(missing_cols) - 1)
            ws.update(range_name=f"{range_start}:{range_end}", values=[missing_cols])
            
            for i, col_name in enumerate(missing_cols):
                new_idx = len(current_headers) + i
                current_headers.append(col_name)
                header_map[col_name] = new_idx
                
        row_data_list = [""] * len(current_headers)
        for key, value in input_row_dict.items():
            if key in header_map:
                col_idx = header_map[key]
                row_data_list[col_idx] = value
        
        if existing_rows:
            target_index = min(existing_rows)
            rows_to_move = sorted([r for r in existing_rows if r != target_index], reverse=True)
        else:
            all_vals = ws.col_values(1)
            target_index = len(all_vals) + 1
            rows_to_move = []

        insert_ptr = target_index + 1
        moved_count = 0
        for r_idx in rows_to_move:
            row_vals = ws.row_values(r_idx)
            ws.delete_rows(r_idx)
            ws.insert_row(row_vals, insert_ptr)
            insert_ptr += 1
            moved_count += 1
            time.sleep(1.5) 

        final_insert_pos = target_index + moved_count + (1 if existing_rows else 0)
        
        if not existing_rows:
            ws.append_row(row_data_list)
            final_insert_pos = len(ws.col_values(1))
        else:
            ws.insert_row(row_data_list, final_insert_pos)

        color = random.choice(PASTEL_COLORS)
        fmt = cellFormat(backgroundColor=color)
        format_cell_range(ws, f"A{target_index}:Z{final_insert_pos}", fmt)
        
        return final_insert_pos

# ================= 主程式 =================

def get_user_mode():
    """獲取用戶希望執行的模式"""
    print("\n==========================================")
    print("請選擇執行模式 (Select Execution Mode):")
    print("1. 僅詢價 (Price Check Only)")
    print("   - 僅搜尋 DB 並回填價格到 BOM")
    print("   - ❌ 不會寫入或修改資料庫")
    print("2. 僅歸檔 (Filing Only)")
    print("   - 將零件分類並插入資料庫")
    print("   - ❌ 不回填價格 (但會回填 Status 讓你知道它去哪了)")
    print("3. 完整模式 (Full Mode) [預設]")
    print("   - ✅ 歸檔到資料庫 + ✅ 回填價格到 BOM")
    print("==========================================\n")
    
    # 支援 GitHub Actions 環境變數
    env_mode = os.environ.get("EXECUTION_MODE")
    if env_mode in ['1', '2', '3']:
        print(f"🤖 Detected Env Var: Mode {env_mode}")
        return int(env_mode)

    # 本地端互動
    try:
        choice = input("👉 請輸入 1, 2 或 3 (Enter default 3): ").strip()
        if choice in ['1', '2', '3']:
            return int(choice)
    except:
        pass
    
    print("Using Default: Mode 3")
    return 3

def main():
    mode = get_user_mode()
    print(f"🚀 Starting BOM Automation (Mode {mode})...", flush=True)
    
    enable_db_write = (mode in [2, 3]) # 模式 2,3 允許寫入 DB
    enable_price_fill = (mode in [1, 3]) # 模式 1,3 允許回填價格

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key: return

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key), scope)
        client = gspread.authorize(creds)
        db_manager = DatabaseManager(client, DB_SHEET_URL)
        gemini = GeminiBrain(GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Init Error: {e}")
        return

    try:
        input_ws = db_manager.workbook.worksheet(INPUT_SHEET_NAME)
        all_input_values = input_ws.get_all_values()
        if not all_input_values: return
        
        input_headers = all_input_values[0] 
        input_rows = all_input_values[1:]   
    except Exception as e:
        print(f"❌ Read Input Error: {e}")
        return

    def get_col_idx(names):
        for n in names:
            for i, h in enumerate(input_headers):
                if n.upper() in str(h).upper(): return i
        return None

    col_desc_idx = get_col_idx(["Description", "Part Description"])
    col_mpn_idx = get_col_idx(["MPN", "Part No", "P/N"])
    col_val_idx = get_col_idx(["Value", "Val"])
    col_status_idx = get_col_idx(["Status"])

    output_headers = ["Status", "Est. Price", "Ref Source", "Match Type", "Link", "Candidates"]
    start_output_col = len(input_headers) + 1
    
    if col_status_idx is None:
        input_ws.update(range_name=gspread.utils.rowcol_to_a1(1, start_output_col), values=[output_headers])

    print(f"🔄 Processing {len(input_rows)} items...", flush=True)

    for i, row in enumerate(input_rows):
        row_num = i + 2
        
        def get_val(idx): return str(row[idx]) if idx is not None and idx < len(row) else ""

        if col_status_idx is not None and col_status_idx < len(row) and row[col_status_idx]:
            continue

        desc = get_val(col_desc_idx)
        mpn = get_val(col_mpn_idx)
        value = get_val(col_val_idx)

        print(f"   [{i+1}/{len(input_rows)}] {desc[:20]}...", end=" ")

        # 1. 分類 (Classify)
        target_sheet = None
        if not value:
             target_sheet = gemini.classify_component_fallback(desc, value)
        
        if not target_sheet or target_sheet == "Others":
             desc_u = desc.upper()
             val_u = value.upper()
             if "UF" in val_u or "PF" in val_u: target_sheet = "MLCC(TMTC)"
             elif "RES" in desc_u or "OHM" in val_u: target_sheet = "RES"
             else:
                 for k, v in SHEET_MAP.items():
                     if any(kw in desc_u for kw in v): 
                         target_sheet = k
                         break
        
        if not target_sheet: target_sheet = "Others"
        print(f"-> [{target_sheet}]")
        
        if target_sheet not in SHEET_MAP and target_sheet != "Others":
             try: input_ws.update_cell(row_num, start_output_col, "Skipped")
             except: pass
             continue

        # 2. 搜尋 (Search)
        matches, match_type = db_manager.find_best_matches(target_sheet, mpn, desc, value)
        
        # 3. 歸檔 (Filing) - 僅在 Mode 2,3 執行
        status = "Checked Only"
        inserted_row = 0
        
        if enable_db_write:
            input_row_dict = {}
            for h_idx, h_name in enumerate(input_headers):
                if h_idx < len(row):
                    input_row_dict[h_name] = row[h_idx]
            
            try:
                existing_indices = [m['row'] for m in matches]
                inserted_row = db_manager.organize_and_insert(target_sheet, existing_indices, input_row_dict)
                status = "Moved & Inserted"
            except Exception as e:
                print(f"      ❌ DB Write Error: {e}")
                status = f"DB Error: {str(e)[:20]}"
        elif matches:
            # 如果是 Mode 1 (Read Only)，我們還是需要知道 match 到哪一行才能給連結
            inserted_row = matches[0]['row']
            status = "Match Found (Read Only)"

        # 4. 準備回填資料
        # 根據模式決定要不要填價格
        best_price = "Skipped"
        if enable_price_fill:
            best_price = matches[0]['data'].get('Price', 'N/A') if matches else 'N/A'
        
        ref_source = ""
        if matches:
            d_keys = [k for k in matches[0]['data'].keys() if 'DESC' in k.upper()]
            if d_keys: ref_source = matches[0]['data'][d_keys[0]]

        link_formula = ""
        if inserted_row > 0:
            try:
                sid = db_manager.workbook.worksheet(target_sheet).id
                link_url = f"https://docs.google.com/spreadsheets/d/{db_manager.workbook.id}/edit#gid={sid}&range=A{inserted_row}"
                link_formula = f'=HYPERLINK("{link_url}", "Go")'
            except: pass

        cand_str = "\n".join([f"{m['data'].get('MPN','')} ${m['data'].get('Price',0)}" for m in matches[1:]])
        
        out_values = [status, best_price, ref_source, match_type, link_formula, cand_str]
        
        # 5. 更新 Input BOM (所有模式都會更新狀態，確保使用者知道進度)
        try:
            start_cell = gspread.utils.rowcol_to_a1(row_num, start_output_col)
            end_cell = gspread.utils.rowcol_to_a1(row_num, start_output_col + 5)
            input_ws.update(range_name=f"{start_cell}:{end_cell}", values=[out_values], value_input_option="USER_ENTERED")
        except:
            time.sleep(5)
            try:
                input_ws.update(range_name=f"{start_cell}:{end_cell}", values=[out_values], value_input_option="USER_ENTERED")
            except: pass

        time.sleep(1) 

    print("✅ Done!")

if __name__ == "__main__":
    main()
