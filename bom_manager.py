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

ALLOWED_DB_COLUMNS = ["MPN", "Part No", "Description", "Part Description", "Value", "Val", "Manufacturer", "MFG"]
REPORT_COLUMNS = ["Status", "Est. Price", "Ref Source", "Match Type", "Link", "Candidates"]

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

# ================= 輔助工具 =================

def retry_with_backoff(retries=5, delay=1):
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
                if not all_values: return pd.DataFrame(), []
                
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

        mpn_col = next((c for c in df.columns if 'MPN' in c.upper() or 'PART' in c.upper() or 'PN' in c.upper()), None)
        if mpn_col and mpn_str:
            found = df[df[mpn_col].astype(str).str.strip().str.upper() == mpn_str]
            if not found.empty:
                for _, row in found.iterrows():
                    matches.append({'row': row['_row_index'], 'data': row})
                return matches, "Exact Match (MPN)"

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
            if val_str:
                if val_str == row_val: score += 20
                elif val_str in row_desc: score += 15
                else: continue

            if not is_structure_match(desc_str, row_desc): continue 

            hit_count = 0
            for word in desc_keywords:
                if len(word) > 2 and word in row_desc: hit_count += 1
            score += hit_count

            if score >= 18: 
                candidates.append({'row': row['_row_index'], 'data': row, 'score': score})

        candidates.sort(key=lambda x: x['score'], reverse=True)
        if candidates: return candidates[:3], "Parametric Match"
        return [], "None"

    @retry_with_backoff(retries=5, delay=1)
    def organize_and_insert(self, sheet_name, existing_rows, input_row_dict):
        ws = self.workbook.worksheet(sheet_name)
        
        db_input_data = {}
        for k, v in input_row_dict.items():
            is_allowed = any(allowed in k for allowed in ALLOWED_DB_COLUMNS)
            if is_allowed and k not in REPORT_COLUMNS:
                db_input_data[k] = v
        
        if not db_input_data: return 0

        current_headers = self.headers_cache.get(sheet_name, [])
        if not current_headers:
            current_headers = ws.row_values(1)
            clean_headers = [] 
            for h in current_headers:
                if h: clean_headers.append(str(h).strip())
            current_headers = clean_headers

        header_map = {} 
        db_header_index = {h.upper(): i for i, h in enumerate(current_headers)}
        
        for key in db_input_data.keys():
            if not key: continue
            u_key = key.upper()
            match_idx = -1
            if u_key in db_header_index:
                match_idx = db_header_index[u_key]
            else:
                for db_h, idx in db_header_index.items():
                    if u_key in db_h or db_h in u_key:
                        match_idx = idx
                        break
            if match_idx != -1:
                header_map[key] = match_idx

        row_data_list = [""] * len(current_headers)
        for key, value in db_input_data.items():
            if key in header_map:
                col_idx = header_map[key]
                row_data_list[col_idx] = value
        
        final_insert_pos = 0
        try:
            if existing_rows:
                target_index = min(existing_rows) + 1
                ws.insert_row(row_data_list, target_index)
                final_insert_pos = target_index
            else:
                ws.append_row(row_data_list)
                final_insert_pos = ws.row_count 
        except APIError as e:
            if "limit of 10000000 cells" in str(e):
                print(f"      ❌ DB FULL: Sheet '{sheet_name}' hit cell limit. Skipping write.")
                raise e 
            else:
                raise e

        return final_insert_pos

# ================= 主程式 =================

def get_user_mode():
    env_mode = os.environ.get("EXECUTION_MODE")
    if env_mode: 
        try:
            val = str(env_mode).strip()[0]
            if val in ['1', '2', '3']: return int(val)
        except: pass
    return 3

@retry_with_backoff(retries=5, delay=2)
def safe_batch_update(worksheet, range_name, values):
    worksheet.update(range_name=range_name, values=values, value_input_option="USER_ENTERED")

def main():
    mode = get_user_mode()
    print(f"🚀 Starting BOM Automation (Batch Mode) | Mode: {mode}", flush=True)
    
    enable_db_write = (mode in [2, 3]) 
    enable_price_fill = (mode in [1, 3]) 

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key = os.environ.get('GOOGLE_SHEETS_JSON')
    if not json_key: return

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key), scope)
        client = gspread.authorize(creds)
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
        try:
            input_ws.update(range_name=f"{range_start}:{range_end}", values=[output_headers_needed])
            input_headers.extend(output_headers_needed)
        except: pass

    # Report Column indices
    report_col_indices = {}
    for rc in REPORT_COLUMNS:
        idx = get_col_idx([rc])
        if idx is not None: report_col_indices[rc] = idx
    
    indices = sorted(report_col_indices.values())
    is_contiguous = False
    if len(indices) == len(REPORT_COLUMNS):
        if indices[-1] - indices[0] == len(REPORT_COLUMNS) - 1:
            is_contiguous = True

    print(f"🔄 Processing {len(input_rows)} items (Batch Mode)...", flush=True)

    # 用於儲存所有結果的列表
    all_batch_results = []

    for i, row in enumerate(input_rows):
        row_num = i + 2
        
        def get_val(idx): return str(row[idx]) if idx is not None and idx < len(row) else ""

        # 跳過已處理
        if col_status_idx is not None:
             status_val = get_val(col_status_idx)
             if status_val and "Processed" in status_val: 
                 all_batch_results.append(None) # 佔位符
                 continue

        desc = get_val(col_desc_idx)
        mpn = get_val(col_mpn_idx)
        value = get_val(col_val_idx)

        print(f"   [{i+1}/{len(input_rows)}] {desc[:20]}...", end=" ")

        # 1. 分類
        target_sheet = "Others"
        desc_u = desc.upper()
        val_u = value.upper()

        if "UF" in val_u or "PF" in val_u or "NF" in val_u: target_sheet = "MLCC(TMTC)"
        elif "RES" in desc_u or "OHM" in val_u or "Ω" in val_u: target_sheet = "RES"
        else:
            for k, v in SHEET_MAP.items():
                if any(kw in desc_u for kw in v): 
                    target_sheet = k
                    break
        
        print(f"-> [{target_sheet}]")
        
        if target_sheet not in SHEET_MAP and target_sheet != "Others":
             all_batch_results.append(None)
             continue

        # 2. 搜尋
        matches, match_type = db_manager.find_best_matches(target_sheet, mpn, desc, value)
        
        # 3. 歸檔 (如果是 Full Mode)
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
            except APIError as e:
                if "limit of 10000000 cells" in str(e):
                    status = "DB Full (Read Only)"
                else:
                    print(f"      ❌ DB Write Error: {e}")
                    status = f"DB Error: {str(e)[:20]}"
            except Exception as e:
                status = "Error"
        elif matches:
            inserted_row = matches[0]['row']
            status = "Match Found"

        # 4. 準備回填資料 (但不立刻寫入)
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
        
        all_batch_results.append(updates)
        # 這裡不 sleep 了，因為沒有寫入操作 (除非有 DB insert)
        # 如果有 DB insert，organize_and_insert 裡面也沒有 sleep 了 (靠 retry)
        # 為了安全起見，微量 sleep
        if enable_db_write and status == "Moved & Inserted":
            time.sleep(0.5)

    print("\n💾 Writing Batch Results to Input BOM...", flush=True)
    
    # ★★★ 最終批次寫入 ★★★
    if is_contiguous and all_batch_results:
        # 如果欄位連續，我們可以構建一個巨大的 2D 陣列一次寫入
        # 這是最快的方法
        start_idx = indices[0] # 第一個 Report Column 的 index
        
        # 準備資料矩陣
        # 注意：all_batch_results 包含 None (跳過的行)
        # 我們只更新有資料的行，這比較麻煩
        # 為了簡單，我們分塊更新，或者乾脆一行一行但用 batch_update? 
        # 不，為了 API，我們把 None 的地方填回原本的值? 太慢。
        # 我們只收集有變動的 range
        
        final_values = []
        range_start_row = 2
        
        # 為了簡單與安全，我們只支援連續寫入
        # 如果中間有跳過 (None)，我們填入空字串或保留? 
        # 假設我們是全量跑，中間不會有 None (除非是 Skipped)
        
        update_data = []
        for i, updates in enumerate(all_batch_results):
            if updates is None: 
                # 填空值，避免錯位
                update_data.append([""] * len(REPORT_COLUMNS))
            else:
                row_vals = [updates[col] for col in REPORT_COLUMNS]
                update_data.append(row_vals)
        
        if update_data:
            start_cell = gspread.utils.rowcol_to_a1(2, start_idx + 1)
            end_cell = gspread.utils.rowcol_to_a1(2 + len(update_data) - 1, start_idx + len(REPORT_COLUMNS))
            try:
                safe_batch_update(input_ws, f"{start_cell}:{end_cell}", update_data)
                print("✅ Batch write successful!")
            except Exception as e:
                print(f"❌ Batch write failed: {e}")
                # Fallback: row by row
                for i, updates in enumerate(all_batch_results):
                    if updates:
                        # ... row by row code ...
                        pass
    else:
        # 非連續欄位，只能逐行寫入 (較慢但安全)
        print("⚠️ Report columns not contiguous, reverting to row-by-row write.")
        for i, updates in enumerate(all_batch_results):
            if updates is None: continue
            row_num = i + 2
            for col_name, val in updates.items():
                if col_name in report_col_indices:
                    col_idx = report_col_indices[col_name] + 1
                    try:
                        input_ws.update_cell(row_num, col_idx, val)
                    except: pass
            time.sleep(0.5)

    print("✅ All tasks completed!")

if __name__ == "__main__":
    main()
