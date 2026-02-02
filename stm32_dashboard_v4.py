import os
import json
import re
import gspread
import xml.etree.ElementTree as ET
from oauth2client.service_account import ServiceAccountCredentials
from collections import defaultdict

# ================= 設定區 =================
XML_FILENAME = "STM32MP133CAFx.xml"
SPREADSHEET_NAME = 'STM32_GPIO_Planner'
WORKSHEET_CONFIG = 'Config_Panel'
GOOGLE_CREDENTIALS_FILE = "e-caldron-484313-m4-001936cf040b.json"

def diagnose():
    print("🕵️‍♂️ 開始診斷...")

    # 1. 檢查 XML
    if not os.path.exists(XML_FILENAME):
        print(f"❌ 錯誤：找不到 XML 檔案 '{XML_FILENAME}'")
        return
    
    try:
        tree = ET.parse(XML_FILENAME)
        root = tree.getroot()
        ns = {'ns': 'http://mcd.rou.st.com/modules.php?name=mcu'}
        pins = root.findall("ns:Pin", ns)
        print(f"✅ XML 讀取成功，找到 {len(pins)} 個腳位定義。")
    except Exception as e:
        print(f"❌ XML 解析失敗: {e}")
        return

    # 2. 檢查 Google Sheet 連線
    print("🔌 正在連線 Google Sheet...")
    if not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        # 嘗試讀取環境變數
        json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        if not json_content:
            print(f"❌ 錯誤：找不到憑證檔案 '{GOOGLE_CREDENTIALS_FILE}' 且無環境變數。")
            return
        else:
            print("✅ 使用環境變數憑證。")
    else:
        print("✅ 使用本地憑證檔案。")

    try:
        # 連線
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if os.path.exists(GOOGLE_CREDENTIALS_FILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, scope)
        else:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(os.environ.get('GOOGLE_SHEETS_JSON')), scope)
            
        client = gspread.authorize(creds)
        sheet = client.open(SPREADSHEET_NAME)
        print(f"✅ 成功開啟試算表: {SPREADSHEET_NAME}")
        
        # 3. 檢查 Config Panel
        try:
            ws = sheet.worksheet(WORKSHEET_CONFIG)
            print(f"✅ 找到分頁: {WORKSHEET_CONFIG}")
            
            # 讀取並印出前幾筆資料
            data = ws.get_all_records()
            print(f"📊 讀取到 {len(data)} 筆設定資料。")
            
            if len(data) > 0:
                print("   [第一筆資料內容]:")
                print(f"   {data[0]}")
                
                # 檢查關鍵欄位是否存在
                keys = data[0].keys()
                print(f"   [欄位檢查]: {list(keys)}")
                
                required_col = 'Quantity (Groups)' # 這是新版代碼要求的名稱
                if required_col not in keys:
                     print(f"⚠️ 警告：找不到欄位 '{required_col}'。您的表單可能是舊版。")
                     print("👉 建議：刪除 Config_Panel 分頁，讓程式重新建立。")
                else:
                    qty = data[0].get(required_col)
                    print(f"   第一筆數量值: {qty} (類型: {type(qty)})")
            else:
                print("⚠️ Config_Panel 是空的，請填寫資料。")

        except gspread.exceptions.WorksheetNotFound:
            print(f"❌ 錯誤：找不到分頁 '{WORKSHEET_CONFIG}'")

    except Exception as e:
        print(f"❌ 連線或讀取失敗: {e}")

if __name__ == "__main__":
    diagnose()
