import os
import re
import time
import json
import random
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread_formatting import *
from gspread.exceptions import APIError

# ================= 設定區 =================

DB_SHEET_URL = "https://docs.google.com/spreadsheets/d/1ovCEzxlz-383PLl4Dmtu8GybxfxI7tCKOl-6-oXNRa0/edit?usp=sharing"
INPUT_SHEET_NAME = "Input_BOM"

# 報表專用欄位 (不會被寫入資料庫)
REPORT_COLUMNS = ["Status", "Est. Price", "Ref Source", "Match Type", "Link", "Candidates"]

# 分頁關鍵字映射 (規則庫)
SHEET_MAP = {
    "RES": ["RES", "OHM", "Ω", "RESISTOR"],
    "MLCC(TMTC)": ["CAP", "UF", "NF", "PF", "CERAMIC", "MLCC"],
    "E-CAP": ["ELECTROLYTIC", "ALUMINUM", "TANTALUM"],
    "bead and inductor": ["INDUCTOR", "BEAD", "COIL", "UH", "MH", "NH"],
    "diode and transistor": ["DIODE", "TRANSISTOR", "MOSFET", "RECTIFIER"],
    "IC": ["IC", "MCU", "CPU", "CHIP"],
    "Connectors": ["CONN", "HEADER", "JACK", "USB", "SOCKET"],
    "switch and fuse": ["SWITCH", "FUSE", "BUTTON", "PTC", "POLY"],
    "Led_Xtal": ["LED", "CRYSTAL", "XTAL", "OSCILLATOR"]
}

PASTEL_COLORS = [
    {"red": 1.0, "green": 1.0, "blue": 0.8},
    {"red": 0.8, "green": 1.0, "blue": 0.8},
    {"red": 0.8, "green": 0.9, "blue": 1.0},
    {"red": 1.0, "green": 0.8, "blue": 0.8},
    {"red": 0.9, "green": 0.8, "blue": 1.0},
]

# ================= 輔助工具 =================

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
                # 修正 range 長度問題
                df['_row_index'] = range(2, len(all_values) + 1)
                
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
                if has_space1 != has_space2: return False 
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
            # 數值比對
            if val_str:
                if val_str == row_val: score += 20
                elif val_str in row_desc: score += 15
                else: continue # 數值不同直接跳過

            # 結構比對
            if not is_structure_match(desc_str, row_desc): continue 

            # 關鍵字比對
            hit_count = 0
            for word in desc_keywords:
                if len(word) > 2 and word in row_desc: hit_count += 1
            score += hit_count

            if score >= 18: 
                candidates.append({'row': row['_row_index'], 'data': row, 'score': score})

        candidates.sort(key=lambda x: x['score'], reverse=True)
        if candidates: return candidates[:3], "Parametric Match"
        return [], "None"

    @retry_with_backoff(retries=5, delay=2)
    def organize_and_insert(self, sheet_name, existing_rows, input_row_dict):
        ws = self.workbook.worksheet(sheet_name)
        
        # 1. 過濾掉報表專用欄位
        db_input_data = {k: v for k, v in input_row_dict.items() if k not in REPORT_COLUMNS}

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
        
        for key in db_input_data.keys():
            if not key: continue
            u_key = key.upper()
            if u_key in db_header_index:
                header_map[key] = db_header_index[u_key]
            else:
                missing_cols.append(key)

        # 2. 自動擴充欄位
        if missing_cols:
            print(f"      🆕 Creating new columns in '{sheet_name}': {missing_cols}")
            needed_cols = len(current_headers) + len(missing_cols)
            if needed_cols > ws.col_count:
                ws.resize(cols=needed_cols + 5)
                time.sleep(1)

            start_col_idx = len(current_headers) + 1
            range_start = gspread.utils.rowcol_to_a1(1, start_col_idx)
            range_end = gspread.utils.rowcol_to_a1(1, start_col_idx + len(missing_cols) - 1)
            ws.update(range_name=f"{range_start}:{range_end}", values=[missing_cols])
            
            for i, col_name in enumerate(missing_cols):
                new_idx = len(current_headers) + i
                current_headers.append(col_name)
                header_map[col_name] = new_idx
        
        # 3. 準備資料 List
        max_idx = max(header_map.values()) if header_map else 0
        list_size = max(len(current_headers), max_idx + 1)
        row_data_list = [""] * list_size

        for key, value in db_input_data.items():
            if key in header_map:
                col_idx = header_map[key]
                row_data_list[col_idx] = value
        
        # 4. 插入位置邏輯
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

        # 5. 上色
        if target_index <= final_insert_pos:
            color = random.choice(PASTEL_COLORS)
            fmt = cellFormat(backgroundColor=color)
            format_cell_range(ws, f"A{target_index}:Z{final_insert_pos}", fmt)
        
        return final_insert_pos

# ================= 顏色與資料重置 =================

@retry_with_backoff(retries=3, delay=5)
def reset_database_colors(client, sheet_url):
    print("🧹 Cleaning up colors...", flush=True)
    try:
        workbook = client.open_by_url(sheet_url)
        target_sheets = [INPUT_SHEET_NAME] + list(SHEET_MAP.keys())
        white_bg = cellFormat(backgroundColor={"red": 1.0, "green": 1.0, "blue": 1.0})
        
        for sheet_name in target_sheets:
            try:
                ws = workbook.worksheet(sheet_name)
                format_cell_range(ws, "A2:Z3000", white_bg)
            except: continue
    except: pass

def clear_input_report_columns(ws, headers):
    """清除 Input_BOM 中報表欄位的舊資料，保留標題"""
    print("🧹 Clearing old report data in Input_BOM...", flush=True)
    
    cols_to_clear = []
    for i, h in enumerate(headers):
        if str(h).strip() in REPORT_COLUMNS:
            cols_to_clear.append(i + 1)
    
    if not cols_to_clear: return

    for col_idx in cols_to_clear:
        col_letter = gspread.utils.rowcol_to_a1(1, col_idx)[0] 
        range_name = f"{col_letter}2:{col_letter}2000"
        try:
            ws.batch_clear([range_name])
        except: pass
    time.sleep(1)

# ================= 主程式 =================

def get_user_mode():
    print("\n==========================================")
    print("📋 請選擇執行模式 (Select Execution Mode):")
    print("1️⃣  僅詢價 (Price Check Only)")
    print("2️⃣  僅歸檔 (Filing Only)")
    print("3️⃣  完整模式 (Full Mode) [推薦/預設]")
    print("==========================================\n")
    
    env_mode = os.environ.get("EXECUTION_MODE")
    if env_mode: 
        # 解析 GitHub UI 傳入的字串 "3. 完整模式" -> 3
        try:
            val = str(env_mode).strip()[0]
            if val in ['1', '2', '3']: return int(val)
        except: pass

    try:
        choice = input("👉 請輸入 1, 2 或 3: ").strip()
        if choice in ['1', '2', '3']: return int(choice)
    except: pass
    return 3

def main():
    mode = get_user_mode()
    print(f"🚀 Starting BOM Automation (No AI) | Mode: {mode}", flush=True)
    
    enable_db_write = (mode in [2, 3]) 
    enable_price_fill = (mode in [1, 3]) 

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key = os.environ.get('GOOGLE_SHEETS_JSON')
    if not json_key: return

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key), scope)
        client = gspread.authorize(creds)
        
        if enable_db_write: 
            reset_database_colors(client, DB_SHEET_URL)
            
        db_manager = DatabaseManager(client, DB_SHEET_URL)
    except Exception as e:
        print(f"❌ Init Error: {e}")
        return

    try:
        input_ws = db_manager.workbook.worksheet(INPUT_SHEET_NAME)
        all_input_values = input_ws.get_all_values()
        if not all_input_values: return
        
        input_headers = all_input_values[0] 
        input_rows = all_input_values[1:]   
        
        clear_input_report_columns(input_ws, input_headers)
        
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

    # 確保 Output Headers 存在
    output_headers_needed = [h for h in REPORT_COLUMNS if h not in input_headers]
    if output_headers_needed:
        start_col_idx = len(input_headers) + 1
        range_start = gspread.utils.rowcol_to_a1(1, start_col_idx)
        range_end = gspread.utils.rowcol_to_a1(1, start_col_idx + len(output_headers_needed) - 1)
        input_ws.update(range_name=f"{range_start}:{range_end}", values=[output_headers_needed])
        input_headers.extend(output_headers_needed)
        col_status_idx = get_col_idx(["Status"])

    header_map = {h: i for i, h in enumerate(input_headers)}

    print(f"🔄 Processing {len(input_rows)} items...", flush=True)

    for i, row in enumerate(input_rows):
        row_num = i + 2
        
        def get_val(idx): return str(row[idx]) if idx is not None and idx < len(row) else ""

        if col_status_idx is not None:
             status_val = get_val(col_status_idx)
             if status_val and "Processed" in status_val: continue

        desc = get_val(col_desc_idx)
        mpn = get_val(col_mpn_idx)
        value = get_val(col_val_idx)

        print(f"   [{i+1}/{len(input_rows)}] {desc[:20]}...", end=" ")

        # 1. 分類 (純規則)
        target_sheet = "Others"
        desc_u = desc.upper()
        val_u = value.upper()

        # 規則 A: 數值單位判斷
        if "UF" in val_u or "PF" in val_u or "NF" in val_u: target_sheet = "MLCC(TMTC)"
        elif "RES" in desc_u or "OHM" in val_u or "Ω" in val_u: target_sheet = "RES"
        else:
            # 規則 B: 關鍵字映射
            for k, v in SHEET_MAP.items():
                if any(kw in desc_u for kw in v): 
                    target_sheet = k
                    break
        
        print(f"-> [{target_sheet}]")
        
        if target_sheet not in SHEET_MAP and target_sheet != "Others":
             continue

        # 2. 搜尋
        matches, match_type = db_manager.find_best_matches(target_sheet, mpn, desc, value)
        
        # 3. 歸檔
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
            inserted_row = matches[0]['row']
            status = "Match Found"

        # 4. 回填
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
        
        updates = {
            "Status": status,
            "Est. Price": best_price,
            "Ref Source": ref_source,
            "Match Type": match_type,
            "Link": link_formula,
            "Candidates": cand_str
        }
        
        for col_name, val in updates.items():
            if col_name in header_map:
                col_idx = header_map[col_name] + 1
                try:
                    input_ws.update_cell(row_num, col_idx, val)
                except: pass
        
        time.sleep(1) 

    print("✅ Done!")

if __name__ == "__main__":
    main()
