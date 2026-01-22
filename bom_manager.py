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

def retry_with_backoff(retries=5, delay=5):
    """
    裝飾器：當遇到 Google API 429 (Quota exceeded) 錯誤時，
    自動暫停並重試，而不是直接讓程式崩潰。
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            for i in range(retries):
                try:
                    return func(*args, **kwargs)
                except APIError as e:
                    # 檢查是否為 Quota exceeded (429)
                    if "429" in str(e) or "Quota exceeded" in str(e):
                        wait_time = delay * (2 ** i) + random.uniform(0, 1) # 指數退避
                        print(f"⏳ Quota limit hit. Sleeping for {wait_time:.1f}s before retry {i+1}/{retries}...", flush=True)
                        time.sleep(wait_time)
                    else:
                        raise e # 其他錯誤直接拋出
            raise Exception("Max retries exceeded for API limit.")
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
            print("⚠️ Warning: No Gemini API Key found. AI features disabled.")

    def classify_component_fallback(self, description, value):
        if not self.model: return "Others"
        
        prompt = f"""
        You are an electronic component expert. 
        I have a database with these sheets: {list(SHEET_MAP.keys())}.
        
        Component Info:
        Description: {description}
        Value: {value}
        
        Which sheet does this component belong to? 
        Return ONLY the sheet name. If unsure, return 'Others'.
        """
        try:
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except:
            return "Others"

class DatabaseManager:
    def __init__(self, client, sheet_url):
        self.client = client
        try:
            self.workbook = self.client.open_by_url(sheet_url)
            print(f"📂 Successfully connected to Database: {self.workbook.title}")
        except Exception as e:
            print(f"❌ Failed to open spreadsheet by URL. Error: {e}")
            raise e
            
        self.sheet_cache = {} 

    def get_sheet_df(self, sheet_name):
        if sheet_name not in self.sheet_cache:
            try:
                worksheet = self.workbook.worksheet(sheet_name)
                data = worksheet.get_all_records()
                if not data:
                    return pd.DataFrame()
                df = pd.DataFrame(data)
                df['_row_index'] = range(2, len(data) + 2) 
                self.sheet_cache[sheet_name] = df
            except gspread.exceptions.WorksheetNotFound:
                print(f"⚠️ Sheet '{sheet_name}' not found in DB.")
                return pd.DataFrame()
        return self.sheet_cache[sheet_name]

    def find_best_matches(self, sheet_name, mpn, description, value):
        df = self.get_sheet_df(sheet_name)
        if df.empty:
            return [], "None"

        matches = []
        match_type = "None"
        
        mpn_clean = str(mpn).strip().upper()
        desc_clean = str(description).strip().upper()
        val_clean = str(value).strip().upper()

        # 1. MPN 精確比對
        mpn_col = next((col for col in df.columns if 'MPN' in col.upper() or 'PART' in col.upper() or 'PN' in col.upper()), None)
        
        if mpn_col and mpn_clean:
            found = df[df[mpn_col].astype(str).str.strip().str.upper() == mpn_clean]
            if not found.empty:
                match_type = "Exact Match (MPN)"
                for _, row in found.iterrows():
                    matches.append({'row': row['_row_index'], 'data': row})
                return matches, match_type

        # 2. 模糊比對
        candidates = []
        desc_keywords = set(re.split(r'[\s,\-_]+', desc_clean))
        desc_col = next((col for col in df.columns if 'DESC' in col.upper()), None)
        value_col = next((col for col in df.columns if 'VAL' in col.upper()), None)
        
        if not desc_col: return [], "None"

        for _, row in df.iterrows():
            row_desc = str(row[desc_col]).upper()
            row_val = str(row[value_col]).upper() if value_col and pd.notna(row[value_col]) else ""
            
            score = 0
            if val_clean and val_clean == row_val: score += 10
            elif val_clean and val_clean in row_desc: score += 8
            
            common_words = 0
            for word in desc_keywords:
                if len(word) > 2 and word in row_desc: common_words += 1
            score += common_words

            if score >= 8:
                candidates.append({'row': row['_row_index'], 'data': row, 'score': score})

        candidates.sort(key=lambda x: x['score'], reverse=True)
        if candidates:
            match_type = "Parametric Match"
            return candidates[:3], match_type

        return [], "None"

    # ★★★ 套用重試機制 ★★★
    @retry_with_backoff(retries=5, delay=10) 
    def organize_and_insert(self, sheet_name, existing_rows, new_item_data):
        ws = self.workbook.worksheet(sheet_name)
        
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
            print(f"      Moving row {r_idx} to {insert_ptr}...")
            row_values = ws.row_values(r_idx)
            ws.delete_rows(r_idx)
            ws.insert_row(row_values, insert_ptr)
            
            insert_ptr += 1
            moved_count += 1
            time.sleep(2) # 增加延遲，保護配額

        final_insert_pos = target_index + moved_count + (1 if existing_rows else 0)
        
        if not existing_rows:
             ws.append_row(new_item_data)
             final_insert_pos = len(ws.col_values(1))
        else:
             ws.insert_row(new_item_data, final_insert_pos)

        # Coloring
        start_row = target_index
        end_row = final_insert_pos
        color = random.choice(PASTEL_COLORS)
        fmt = cellFormat(backgroundColor=color)
        range_str = f"A{start_row}:Z{end_row}"
        format_cell_range(ws, range_str, fmt)
        
        return final_insert_pos

# ================= 輔助函式 =================

def get_sheet_by_rules(description, value):
    desc_u = str(description).upper()
    val_u = str(value).upper()
    
    if "UF" in val_u or "PF" in val_u or "NF" in val_u: return "MLCC(TMTC)"
    if re.search(r'\d+[KM]', val_u) or "OHM" in val_u or "Ω" in val_u:
         if "IC" not in desc_u and "CHIP" not in desc_u: return "RES"

    for sheet, keywords in SHEET_MAP.items():
        for kw in keywords:
            if kw in desc_u: return sheet
    return None

def find_column_index(headers, keywords):
    for i, h in enumerate(headers):
        for kw in keywords:
            if kw.upper() in str(h).upper(): return i + 1
    return None

# ★★★ 建立一個帶有重試機制的更新函式 ★★★
@retry_with_backoff(retries=5, delay=10)
def safe_update_sheet(worksheet, range_name, values):
    worksheet.update(range_name=range_name, values=values, value_input_option="USER_ENTERED")

# ================= 主程式 =================

def main():
    print("🚀 Starting BOM Automation Logic...", flush=True)
    
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key:
        print("❌ Error: GOOGLE_SHEETS_JSON secret is missing.")
        return

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key), scope)
        client = gspread.authorize(creds)
        print(f"🤖 Service Account Email: {creds.service_account_email}", flush=True)
    except Exception as e:
        print(f"❌ Auth Error: {e}")
        return
    
    try:
        db_manager = DatabaseManager(client, DB_SHEET_URL)
        gemini = GeminiBrain(GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Initialization Error: {e}")
        return

    try:
        input_ws = db_manager.workbook.worksheet(INPUT_SHEET_NAME)
        print(f"✅ Found Input Sheet: {INPUT_SHEET_NAME}")
    except gspread.exceptions.WorksheetNotFound:
        print(f"❌ Critical Error: Sheet '{INPUT_SHEET_NAME}' not found.")
        return

    input_data = input_ws.get_all_records()
    if not input_data:
        print("ℹ️ Input BOM is empty.")
        return

    headers = input_ws.row_values(1)
    col_desc_idx = find_column_index(headers, ["Description", "Part Description"])
    col_mpn_idx = find_column_index(headers, ["MPN", "Part No", "P/N"])
    col_val_idx = find_column_index(headers, ["Value", "Val"])
    
    output_headers = ["Status", "Est. Price", "Ref Source", "Match Type", "Link", "Candidates"]
    start_output_col = len(headers) + 1
    
    if "Status" not in headers:
        input_ws.update(range_name=gspread.utils.rowcol_to_a1(1, start_output_col), values=[output_headers])

    print(f"🔄 Processing {len(input_data)} items...", flush=True)

    # 3. 逐行處理
    for i, row in enumerate(input_data):
        row_num = i + 2 
        
        desc = str(row.get(headers[col_desc_idx-1])) if col_desc_idx else ""
        mpn = str(row.get(headers[col_mpn_idx-1])) if col_mpn_idx else ""
        value = str(row.get(headers[col_val_idx-1])) if col_val_idx else ""
        
        status_key = next((h for h in row.keys() if "Status" in h), None)
        if status_key and row.get(status_key): 
            continue

        print(f"   [{i+1}/{len(input_data)}] Processing: {desc[:20]}...", end=" ")
        
        target_sheet = get_sheet_by_rules(desc, value)
        if not target_sheet:
            target_sheet = gemini.classify_component_fallback(desc, value)
        
        print(f"-> [{target_sheet}]")
        
        if target_sheet == "Others" or target_sheet not in SHEET_MAP:
             try:
                 safe_update_sheet(input_ws, gspread.utils.rowcol_to_a1(row_num, start_output_col), [["Skipped (Unknown)"]])
             except: pass
             continue

        matches, match_type = db_manager.find_best_matches(target_sheet, mpn, desc, value)
        existing_indices = [m['row'] for m in matches]
        
        new_row_data = [""] * 10 
        new_row_data[0] = f"{desc} [NEW]" 
        new_row_data[1] = mpn             
        new_row_data[2] = value           
        
        status = "Processed"
        inserted_row = 0
        
        # 呼叫帶有重試機制的插入功能
        try:
            inserted_row = db_manager.organize_and_insert(target_sheet, existing_indices, new_row_data)
            status = "Moved & Inserted"
        except Exception as e:
            print(f"      ❌ Error inserting: {e}")
            status = f"Error: {str(e)[:50]}" # 縮短錯誤訊息避免太長

        best_price = matches[0]['data'].get('Price', 'N/A') if matches else 'N/A'
        ref_source = matches[0]['data'].get('Description', '') if matches else ''
        
        try:
            sheet_id = db_manager.workbook.worksheet(target_sheet).id
            link_url = f"https://docs.google.com/spreadsheets/d/{db_manager.workbook.id}/edit#gid={sheet_id}&range=A{inserted_row}"
            link_formula = f'=HYPERLINK("{link_url}", "Go to {target_sheet}")'
        except:
            link_formula = ""

        candidates_str = "\n".join([f"{m['data'].get('MPN')} ${m['data'].get('Price',0)}" for m in matches[1:]])
        
        out_values = [status, best_price, ref_source, match_type, link_formula, candidates_str]
        
        start_cell = gspread.utils.rowcol_to_a1(row_num, start_output_col)
        end_cell = gspread.utils.rowcol_to_a1(row_num, start_output_col + 5)
        
        # 呼叫帶有重試機制的更新功能
        safe_update_sheet(input_ws, f"{start_cell}:{end_cell}", [out_values])
        
        # ★★★ 每一行處理完後，強制休息 2 秒，避免連續轟炸 API ★★★
        time.sleep(2)

    print("✅ All tasks completed successfully!")

if __name__ == "__main__":
    main()
