# bom_manager.py
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

# ================= 設定區 =================

# 【重要】請將下方的 URL 換成您 "EE BOM Cost V0.6" 檔案的真實網址
DB_SHEET_URL = "https://docs.google.com/spreadsheets/d/https://docs.google.com/spreadsheets/d/1QkYn0px-EAlUs91e5smW0gAKq202lPQn/edit?gid=889936666#gid=889936666/edit"

# Input 分頁名稱 (必須完全一致)
INPUT_SHEET_NAME = "Input_BOM"

# Gemini API Key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 分頁關鍵字映射 (規則庫)
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
        """讀取分頁並轉為 DataFrame (含快取)"""
        if sheet_name not in self.sheet_cache:
            try:
                worksheet = self.workbook.worksheet(sheet_name)
                data = worksheet.get_all_records()
                if not data:
                    return pd.DataFrame()
                df = pd.DataFrame(data)
                # 紀錄原始行號 (Row 1 is header, data starts at 2)
                # get_all_records return list of dicts. Index 0 is Row 2.
                df['_row_index'] = range(2, len(data) + 2) 
                self.sheet_cache[sheet_name] = df
            except gspread.exceptions.WorksheetNotFound:
                print(f"⚠️ Sheet '{sheet_name}' not found in DB.")
                return pd.DataFrame()
        return self.sheet_cache[sheet_name]

    def find_best_matches(self, sheet_name, mpn, description, value):
        """
        在指定分頁搜尋相似零件
        回傳: (matches_list, match_type)
        """
        df = self.get_sheet_df(sheet_name)
        if df.empty:
            return [], "None"

        matches = []
        match_type = "None"
        
        # 正規化字串
        mpn_clean = str(mpn).strip().upper()
        desc_clean = str(description).strip().upper()
        val_clean = str(value).strip().upper()

        # 1. MPN 精確比對 (最高優先級)
        # 嘗試尋找名為 MPN, Part No, QSI_PN 等欄位
        mpn_col = next((col for col in df.columns if 'MPN' in col.upper() or 'PART' in col.upper() or 'PN' in col.upper()), None)
        
        if mpn_col and mpn_clean:
            found = df[df[mpn_col].astype(str).str.strip().str.upper() == mpn_clean]
            if not found.empty:
                match_type = "Exact Match (MPN)"
                for _, row in found.iterrows():
                    matches.append({'row': row['_row_index'], 'data': row})
                return matches, match_type

        # 2. 模糊比對 (Parametric Fuzzy Search)
        candidates = []
        desc_keywords = set(re.split(r'[\s,\-_]+', desc_clean))
        
        # 尋找描述欄位
        desc_col = next((col for col in df.columns if 'DESC' in col.upper()), None)
        value_col = next((col for col in df.columns if 'VAL' in col.upper()), None)
        
        if not desc_col: return [], "None" # 沒有描述欄位無法比對

        for _, row in df.iterrows():
            row_desc = str(row[desc_col]).upper()
            row_val = str(row[value_col]).upper() if value_col and pd.notna(row[value_col]) else ""
            
            score = 0
            # 規則 A: 數值完全吻合 (例如 10uF)
            if val_clean and val_clean == row_val:
                score += 10
            elif val_clean and val_clean in row_desc:
                score += 8
            
            # 規則 B: 關鍵字重疊
            common_words = 0
            for word in desc_keywords:
                if len(word) > 2 and word in row_desc:
                    common_words += 1
            score += common_words

            if score >= 8: # 設定一個門檻
                candidates.append({'row': row['_row_index'], 'data': row, 'score': score})

        # 排序取前 3 名
        candidates.sort(key=lambda x: x['score'], reverse=True)
        if candidates:
            match_type = "Parametric Match"
            return candidates[:3], match_type

        return [], "None"

    def organize_and_insert(self, sheet_name, existing_rows, new_item_data):
        """
        執行：移動現有零件 -> 插入新零件 -> 上色
        """
        ws = self.workbook.worksheet(sheet_name)
        
        # 1. 決定目標位置 (Target Index)
        # 放在現有最上面的那一個的下面。如果沒有現有的，就插在最後面。
        if existing_rows:
            target_index = min(existing_rows)
            # 要被搬移的行 (除了 target_index 以外的其他 match)
            # 從下面開始搬，以免影響 index
            rows_to_move = sorted([r for r in existing_rows if r != target_index], reverse=True)
        else:
            # 插在最後一行
            all_vals = ws.col_values(1) # 假設第一欄有值
            target_index = len(all_vals) + 1
            rows_to_move = []

        # 2. 移動舊零件 (Move)
        insert_ptr = target_index + 1 # 插入點初始位置
        
        moved_count = 0
        for r_idx in rows_to_move:
            print(f"      Moving row {r_idx} to {insert_ptr}...")
            # 讀取 -> 刪除 -> 插入
            row_values = ws.row_values(r_idx)
            ws.delete_rows(r_idx)
            ws.insert_row(row_values, insert_ptr)
            
            insert_ptr += 1
            moved_count += 1
            time.sleep(1) # 防止 API 超速

        # 3. 插入新零件 (Insert New)
        # 最終插入位置
        final_insert_pos = target_index + moved_count + (1 if existing_rows else 0)
        # 如果是全新品 (無 existing)，final_insert_pos 就是 target_index，但 insert_row 會插在該行之上...
        # 修正：gspread insert_row(idx) 會把原本 idx 的擠下去。
        # 如果是 append (全新品)，用 append_row 最安全；如果是插入中間，用 insert_row
        
        if not existing_rows:
             ws.append_row(new_item_data)
             final_insert_pos = ws.row_count # 近似值
        else:
             ws.insert_row(new_item_data, final_insert_pos)

        # 4. 上色 (Coloring)
        start_row = target_index
        end_row = final_insert_pos
        
        # 隨機選一個顏色
        color = random.choice(PASTEL_COLORS)
        fmt = cellFormat(backgroundColor=color)
        
        # 設定格式範圍 (假設資料寬度到 Z)
        range_str = f"A{start_row}:Z{end_row}"
        format_cell_range(ws, range_str, fmt)
        
        return final_insert_pos

# ================= 輔助函式 =================

def get_sheet_by_rules(description, value):
    desc_u = str(description).upper()
    val_u = str(value).upper()
    
    # 規則 1: 根據 Unit
    if "UF" in val_u or "PF" in val_u or "NF" in val_u:
        return "MLCC(TMTC)"
    if re.search(r'\d+[KM]', val_u) or "OHM" in val_u or "Ω" in val_u:
         if "IC" not in desc_u and "CHIP" not in desc_u:
             return "RES"

    # 規則 2: 根據描述關鍵字
    for sheet, keywords in SHEET_MAP.items():
        for kw in keywords:
            if kw in desc_u:
                return sheet
    return None

def find_column_index(headers, keywords):
    """在 headers 尋找包含關鍵字的欄位 index (1-based)"""
    for i, h in enumerate(headers):
        for kw in keywords:
            if kw.upper() in str(h).upper():
                return i + 1
    return None

# ================= 主程式 =================

def main():
    print("🚀 Starting BOM Automation Logic...", flush=True)
    
    # 1. 連線 Google Sheets
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key:
        print("❌ Error: GOOGLE_SHEETS_JSON secret is missing.")
        return

    creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key), scope)
    client = gspread.authorize(creds)
    
    # 初始化管理者
    try:
        db_manager = DatabaseManager(client, DB_SHEET_URL)
        gemini = GeminiBrain(GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ Initialization Error: {e}")
        return

    # 2. 讀取 Input BOM
    try:
        input_ws = db_manager.workbook.worksheet(INPUT_SHEET_NAME)
        print(f"✅ Found Input Sheet: {INPUT_SHEET_NAME}")
    except gspread.exceptions.WorksheetNotFound:
        print(f"❌ Critical Error: Sheet '{INPUT_SHEET_NAME}' not found.")
        print(f"ℹ️  Available sheets: {[s.title for s in db_manager.workbook.worksheets()]}")
        print("Please rename your new BOM sheet to 'Input_BOM' exactly.")
        return

    input_data = input_ws.get_all_records()
    if not input_data:
        print("ℹ️ Input BOM is empty.")
        return

    headers = input_ws.row_values(1)
    
    # 自動偵測 Input 欄位位置
    col_desc_idx = find_column_index(headers, ["Description", "Part Description"])
    col_mpn_idx = find_column_index(headers, ["MPN", "Part No", "P/N"])
    col_val_idx = find_column_index(headers, ["Value", "Val"])
    
    # 準備輸出欄位 (寫在最後面)
    output_headers = ["Status", "Est. Price", "Ref Source", "Match Type", "Link", "Candidates"]
    start_output_col = len(headers) + 1
    
    # 如果標題列還沒這些欄位，補上去
    if "Status" not in headers:
        # 轉換 column index to letter (簡單處理 A-Z, AA-ZZ)
        # 這裡直接用 update cells
        input_ws.update(range_name=gspread.utils.rowcol_to_a1(1, start_output_col), values=[output_headers])

    print(f"🔄 Processing {len(input_data)} items...", flush=True)

    # 3. 逐行處理
    for i, row in enumerate(input_data):
        row_num = i + 2 # Google Sheet Row Number
        
        # 取得資料
        desc = str(row.get(headers[col_desc_idx-1])) if col_desc_idx else ""
        mpn = str(row.get(headers[col_mpn_idx-1])) if col_mpn_idx else ""
        value = str(row.get(headers[col_val_idx-1])) if col_val_idx else ""
        
        # 跳過已處理的 (假設 Status 有值就跳過)
        if len(row) >= start_output_col and row.get("Status"): 
            continue

        print(f"   [{i+1}/{len(input_data)}] Processing: {desc[:20]}...", end=" ")
        
        # A. 分類 (Classify)
        target_sheet = get_sheet_by_rules(desc, value)
        if not target_sheet:
            target_sheet = gemini.classify_component_fallback(desc, value)
        
        print(f"-> [{target_sheet}]")
        
        if target_sheet == "Others" or target_sheet not in SHEET_MAP:
             input_ws.update_cell(row_num, start_output_col, "Skipped (Unknown)")
             continue

        # B. 搜尋 (Search)
        matches, match_type = db_manager.find_best_matches(target_sheet, mpn, desc, value)
        
        # C. 歸檔 (Organize)
        existing_indices = [m['row'] for m in matches]
        
        # 建構新的一行資料 (這裡簡化處理：將 Input 資訊整合填入)
        # 實際應用建議建立一個 Column Mapper
        new_row_data = [""] * 10 
        new_row_data[0] = f"{desc} [NEW]" # 填入第一個欄位
        new_row_data[1] = mpn             # 填入第二個欄位
        new_row_data[2] = value           # 填入第三個欄位
        
        status = "Processed"
        inserted_row = 0
        try:
            inserted_row = db_manager.organize_and_insert(target_sheet, existing_indices, new_row_data)
            status = "Moved & Inserted"
        except Exception as e:
            print(f"      Error inserting: {e}")
            status = f"Error: {e}"

        # D. 回寫結果 (Write Back)
        best_price = matches[0]['data'].get('Price', 'N/A') if matches else 'N/A'
        ref_source = matches[0]['data'].get('Description', '') if matches else ''
        
        # 建立連結
        try:
            sheet_id = db_manager.workbook.worksheet(target_sheet).id
            link_url = f"https://docs.google.com/spreadsheets/d/{db_manager.workbook.id}/edit#gid={sheet_id}&range=A{inserted_row}"
            link_formula = f'=HYPERLINK("{link_url}", "Go to {target_sheet}")'
        except:
            link_formula = ""

        # 候選清單
        candidates_str = "\n".join([f"{m['data'].get('MPN')} ${m['data'].get('Price',0)}" for m in matches[1:]])
        
        # 寫入
        out_values = [status, best_price, ref_source, match_type, link_formula, candidates_str]
        
        # update range
        start_cell = gspread.utils.rowcol_to_a1(row_num, start_output_col)
        end_cell = gspread.utils.rowcol_to_a1(row_num, start_output_col + 5)
        input_ws.update(range_name=f"{start_cell}:{end_cell}", values=[out_values], value_input_option="USER_ENTERED")

    print("✅ All tasks completed successfully!")

if __name__ == "__main__":
    main()
