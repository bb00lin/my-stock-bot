import os
import re
import time
import json
import random
import pandas as pd
import gspread
import google.generativeai as genai
from oauth2client.service_account import ServiceAccountCredentials
from google.auth.transport.requests import Request
from gspread_formatting import *

# ================= 設定區 =================
DB_FILE_NAME = "EE BOM Cost V0.6"
INPUT_SHEET_NAME = "Input_BOM"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 分頁關鍵字映射 (根據您的檔案結構)
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

# 顏色庫 (淺色系，用於分組標示)
PASTEL_COLORS = [
    {"red": 1.0, "green": 1.0, "blue": 0.8}, # 淺黃
    {"red": 0.8, "green": 1.0, "blue": 0.8}, # 淺綠
    {"red": 0.8, "green": 0.9, "blue": 1.0}, # 淺藍
    {"red": 1.0, "green": 0.8, "blue": 0.8}, # 淺紅
    {"red": 0.9, "green": 0.8, "blue": 1.0}, # 淺紫
]

# ================= 類別定義 =================

class GeminiBrain:
    def __init__(self, api_key):
        if api_key:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel('gemini-pro')
        else:
            self.model = None
            print("⚠️ Warning: No Gemini API Key found. AI features disabled.")

    def classify_component_fallback(self, description, value):
        """當規則判斷失敗時，詢問 AI 該去哪個分頁"""
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

    def is_similar(self, item_a_desc, item_b_desc):
        """判斷兩個描述是否為同一類零件"""
        if not self.model: return False
        prompt = f"""
        Compare these two electronic components (ignore manufacturer):
        A: {item_a_desc}
        B: {item_b_desc}
        
        Are they functionally interchangeable or highly similar variants (e.g. same value/package but different tolerance)?
        Answer YES or NO only.
        """
        try:
            response = self.model.generate_content(prompt)
            return "YES" in response.text.upper()
        except:
            return False

class DatabaseManager:
    def __init__(self, client):
        self.client = client
        self.workbook = self.client.open(DB_FILE_NAME)
        self.sheet_cache = {} # Cache for DataFrames

    def get_sheet_df(self, sheet_name):
        if sheet_name not in self.sheet_cache:
            worksheet = self.workbook.worksheet(sheet_name)
            data = worksheet.get_all_records()
            self.sheet_cache[sheet_name] = pd.DataFrame(data)
            self.sheet_cache[sheet_name]['_row_index'] = range(2, len(data) + 2) # Keep track of original rows
        return self.sheet_cache[sheet_name]

    def find_best_matches(self, sheet_name, mpn, description, value):
        """
        回傳: (matches_list, match_type)
        matches_list = [{'row': 10, 'data': series}, ...]
        """
        df = self.get_sheet_df(sheet_name)
        matches = []
        match_type = "None"

        # 1. 嘗試 QSI_PN (如果有這個欄位)
        # (略，因為新 BOM 可能沒有 QSI_PN)

        # 2. 嘗試 MPN 精確比對
        if 'MPN' in df.columns and mpn:
            mpn_clean = str(mpn).strip().upper()
            found = df[df['MPN'].astype(str).str.strip().str.upper() == mpn_clean]
            if not found.empty:
                match_type = "Exact Match (MPN)"
                for idx, row in found.iterrows():
                    matches.append({'row': row['_row_index'], 'data': row})
                return matches, match_type

        # 3. 嘗試 數值+規格 模糊比對 (Regex)
        # 簡易邏輯：如果 Value 和 Description 關鍵字高度重疊
        candidates = []
        desc_keywords = set(re.split(r'[\s,\-_]+', str(description).upper()))
        val_str = str(value).upper().strip()
        
        for idx, row in df.iterrows():
            row_desc = str(row.get('Description', '')).upper()
            row_mpn = str(row.get('MPN', '')).upper()
            
            score = 0
            # 數值比對 (最重要)
            if val_str and val_str in row_desc:
                score += 5
            elif val_str and val_str in str(row.get('Value', '')).upper():
                score += 5

            # 關鍵字重疊
            common = 0
            for word in desc_keywords:
                if len(word) > 2 and word in row_desc:
                    common += 1
            score += common

            if score >= 5: # 門檻
                candidates.append({'row': row['_row_index'], 'data': row, 'score': score})

        # 排序取前幾名
        candidates.sort(key=lambda x: x['score'], reverse=True)
        if candidates:
            match_type = "Parametric Match"
            return candidates[:3], match_type

        return [], "None"

    def organize_and_insert(self, sheet_name, existing_rows, new_item_data):
        """
        核心功能：大挪移 + 插入 + 上色
        existing_rows: List of row indices (e.g., [2, 100])
        new_item_data: List of values for the new row
        """
        ws = self.workbook.worksheet(sheet_name)
        
        # 1. 決定目標位置 (Target Index)
        # 如果有現有零件，插在最上面的那個零件下面；如果沒有，插在最後面
        if existing_rows:
            target_index = min(existing_rows) # e.g. 2
            # 排序：從下面開始處理，避免 index 跑掉
            rows_to_move = sorted([r for r in existing_rows if r != target_index], reverse=True)
        else:
            # 插在最後一行
            target_index = len(ws.col_values(1)) + 1 
            rows_to_move = []

        # 2. 移動舊零件 (Move)
        # 這裡用 "Get -> Delete -> Insert" 策略
        # 為了保持安全性，我們從 index 大的開始搬
        insert_ptr = target_index + 1 # 插入點初始位置
        
        moved_rows_count = 0
        for r_idx in rows_to_move:
            # 讀取
            row_values = ws.row_values(r_idx)
            # 刪除 (注意：刪除後，比它下面的 index 會減 1，但因為我們是 reverse 處理，不影響上面的)
            ws.delete_rows(r_idx)
            # 插入到 target_index 的下方
            ws.insert_row(row_values, insert_ptr)
            moved_rows_count += 1
            insert_ptr += 1
            time.sleep(1) # API Rate limit protection

        # 3. 插入新零件 (Insert New)
        # 插入位置 = 目標行 + 已搬過來的數量 + 1
        final_insert_pos = target_index + moved_rows_count + (1 if existing_rows else 0)
        # 如果是完全新零件(existing_rows為空)，final_insert_pos 就是 target_index
        if not existing_rows: final_insert_pos = target_index

        ws.insert_row(new_item_data, final_insert_pos)
        
        # 4. 上色 (Coloring)
        # 範圍：從 target_index 到 final_insert_pos
        start_row = target_index
        end_row = final_insert_pos
        
        color = random.choice(PASTEL_COLORS)
        fmt = cellFormat(backgroundColor=color)
        
        # 建立 range 字串 (例如 A2:Z4)
        # 這裡假設最大到 Z 欄，可調整
        range_str = f"A{start_row}:Z{end_row}" 
        format_cell_range(ws, range_str, fmt)
        
        return final_insert_pos # 回傳新零件所在的行數，方便生成連結

def get_sheet_by_rules(description, value):
    desc_u = str(description).upper()
    val_u = str(value).upper()
    
    # 規則 1: 根據 Unit
    if "UF" in val_u or "PF" in val_u or "NF" in val_u:
        return "MLCC(TMTC)"
    if "OHM" in val_u or "Ω" in val_u or "K" in val_u or "M" in val_u:
         # 簡單判斷：如果 K/M 前面是數字 (e.g. 10K) 且描述沒有 IC 關鍵字
         if re.search(r'\d+[KM]', val_u) and "IC" not in desc_u:
             return "RES"

    # 規則 2: 根據關鍵字
    for sheet, keywords in SHEET_MAP.items():
        for kw in keywords:
            if kw in desc_u:
                return sheet
                
    return None # 交給 AI 或 Default

def main():
    # 1. 初始化
    print("🚀 Starting BOM Automation...", flush=True)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(os.environ['GOOGLE_SHEETS_JSON'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    db_manager = DatabaseManager(client)
    gemini = GeminiBrain(GEMINI_API_KEY)
    
    # 2. 讀取 Input BOM
    workbook = client.open(DB_FILE_NAME)
    try:
        input_sheet = workbook.worksheet(INPUT_SHEET_NAME)
    except:
        print(f"❌ Cannot find '{INPUT_SHEET_NAME}' sheet.")
        return

    # 讀取所有資料
    input_data = input_sheet.get_all_records()
    if not input_data:
        print("ℹ️ Input BOM is empty.")
        return

    # 3. 處理每一行
    updates = [] # 儲存要回填到 Input BOM 的資料
    
    # 為了批次更新，我們先計算好要寫入的欄位 index (假設在最後面)
    headers = input_sheet.row_values(1)
    # 找出或新增 Output Columns
    output_cols = ["Status", "Est. Price", "Ref Source", "Match Type", "Link", "Candidates"]
    start_col_idx = len(headers) + 1
    
    # 寫入標題 (如果還沒有)
    if "Status" not in headers:
        input_sheet.update(range_name=f"{chr(64+start_col_idx)}1:{chr(64+start_col_idx+5)}1", values=[output_cols])
    
    print(f"🔄 Processing {len(input_data)} items...", flush=True)

    for i, row in enumerate(input_data):
        row_idx = i + 2 # Google Sheet 1-based, header is 1
        
        # 取得關鍵資訊
        # 這裡需要根據您的 CSV 欄位名稱做動態對應，這裡先用常見名稱嘗試
        desc = row.get('Description') or row.get('Part Description') or ""
        value = row.get('Value') or ""
        mpn = row.get('MPN') or row.get('Part No') or ""
        
        # A. 分類 (Classify)
        target_sheet = get_sheet_by_rules(desc, value)
        if not target_sheet:
            target_sheet = gemini.classify_component_fallback(desc, value)
        
        print(f"   Row {row_idx}: {desc[:20]}... -> [{target_sheet}]")
        
        if target_sheet == "Others" or target_sheet not in SHEET_MAP:
             input_sheet.update_cell(row_idx, start_col_idx, "Skipped (Unknown Type)")
             continue

        # B. 搜尋 (Search)
        matches, match_type = db_manager.find_best_matches(target_sheet, mpn, desc, value)
        
        # C. 決策與歸檔 (Action)
        existing_indices = [m['row'] for m in matches]
        
        # 準備要插入的資料 (這部分要看目標分頁的格式，這裡簡化為直接把 Input BOM 的某些欄位塞進去)
        # *重要*: 實際運作時，您可能需要一個 Mapper 把 Input 欄位轉成 DB 欄位順序
        # 這裡先假設我們把 Input 的 Raw string 串接後塞入 Description 欄位做為暫存
        new_row_data = [""] * 10 # 假設 DB 有 10 欄
        new_row_data[3] = f"{desc} {value} [NEW]" # 塞入第 4 欄 Description (假設)
        new_row_data[2] = mpn # 塞入 MPN
        
        try:
            inserted_row_num = db_manager.organize_and_insert(target_sheet, existing_indices, new_row_data)
            status = "Moved & Inserted"
        except Exception as e:
            print(f"Error inserting: {e}")
            status = "Error"
            inserted_row_num = 0

        # D. 準備回填結果
        best_price = matches[0]['data'].get('Price', 'N/A') if matches else 'N/A'
        best_source = matches[0]['data'].get('Description', '') if matches else ''
        
        # 生成連結
        sheet_id = workbook.worksheet(target_sheet).id
        link_url = f"https://docs.google.com/spreadsheets/d/{workbook.id}/edit#gid={sheet_id}&range=A{inserted_row_num}"
        link_formula = f'=HYPERLINK("{link_url}", "Go to {target_sheet}")'
        
        # 候選清單字串
        candidates_str = "\n".join([f"{m['data'].get('MPN')} (${m['data'].get('Price')})" for m in matches[1:]])
        
        # 寫入 Input BOM (逐行寫入較慢但較安全，可改為 batch)
        # Columns: Status, Price, Ref Source, Match Type, Link, Candidates
        result_values = [status, best_price, best_source, match_type, link_formula, candidates_str]
        
        # 使用 update (注意欄位位置)
        col_char_start = chr(64 + start_col_idx)
        col_char_end = chr(64 + start_col_idx + 5)
        input_sheet.update(range_name=f"{col_char_start}{row_idx}:{col_char_end}{row_idx}", values=[result_values], value_input_option="USER_ENTERED")

    print("✅ All done!")

if __name__ == "__main__":
    main()
