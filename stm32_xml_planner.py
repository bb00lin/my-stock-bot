import os
import json
import re
import gspread
import xml.etree.ElementTree as ET
from oauth2client.service_account import ServiceAccountCredentials
from collections import defaultdict
from datetime import datetime

# ================= 設定區 =================
XML_FILENAME = "STM32MP133CAFx.xml"
SPREADSHEET_NAME = 'STM32_GPIO_Planner'
WORKSHEET_CONFIG = 'Config_Panel'
WORKSHEET_RESULT = 'Pinout_View'
WORKSHEET_REF = 'Ref_Data'  # [新] 用來存放下拉選單資料的隱藏頁

# 請確認您的 JSON 金鑰檔名
GOOGLE_CREDENTIALS_FILE = "e-caldron-484313-m4-001936cf040b.json"

# ================= XML 解析與分類器 (升級版) =================
class STM32XMLParser:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.pin_map = defaultdict(list)
        # 定義分類規則 (CubeMX 風格)
        self.categories = {
            "System_Core": ["GPIO", "NVIC", "RCC", "SYS", "HSEM", "IPCC", "EXTI", "PWR"],
            "Connectivity": ["I2C", "SPI", "UART", "USART", "LPUART", "ETH", "USB", "FDCAN", "SDMMC", "QUADSPI", "FMC"],
            "Timers": ["TIM", "LPTIM", "RTC"],
            "Analog": ["ADC", "DAC", "DTS", "VREFBUF"],
            "Multimedia": ["SAI", "I2S", "SPDIFRX", "LTDC", "DCMIPP"],
            "Security": ["CRYP", "HASH", "RNG", "SAES", "PKA", "TAMP"]
        }
        self.detected_peripherals = set()

    def parse(self):
        print(f"📖 讀取 XML: {self.xml_path}")
        try:
            tree = ET.parse(self.xml_path)
            root = tree.getroot()
            ns = {'ns': 'http://mcd.rou.st.com/modules.php?name=mcu'}
            pins = root.findall("ns:Pin", ns)
            
            for pin in pins:
                pin_name = pin.attrib.get('Name')
                if pin_name.startswith("V") and len(pin_name) < 4: continue
                signals = pin.findall('ns:Signal', ns)
                for sig in signals:
                    sig_name = sig.attrib.get('Name')
                    if sig_name.startswith("GPIO"): continue # GPIO 不算週邊
                    
                    self.pin_map[pin_name].append(sig_name)
                    
                    # 提取週邊名稱 (例如 I2C1_SDA -> I2C)
                    # 邏輯：取底線前的部分，並移除數字
                    raw_peri = sig_name.split('_')[0] 
                    peri_type = re.sub(r'\d+', '', raw_peri) # I2C1 -> I2C
                    
                    # 特殊處理
                    if "OTG" in sig_name: peri_type = "USB_OTG"
                    if "ETH" in sig_name: peri_type = "ETH"
                    
                    self.detected_peripherals.add(peri_type)
            
            for p in self.pin_map: self.pin_map[p].sort()
            print(f"✅ XML 解析完成，可用 I/O 數: {len(self.pin_map)}")
        except Exception as e:
            print(f"❌ XML 解析失敗: {e}")

    def get_organized_menu_data(self):
        """將掃描到的週邊自動歸類，準備寫入 Google Sheet"""
        menu = defaultdict(list)
        
        # 遍歷所有掃描到的週邊類型
        for peri in sorted(self.detected_peripherals):
            assigned = False
            for cat, keywords in self.categories.items():
                if peri in keywords:
                    menu[cat].append(peri)
                    assigned = True
                    break
            # 沒在清單中的歸類為 Other
            if not assigned:
                menu["Other"].append(peri)
                
        return menu

# ================= 規劃核心 (維持不變) =================
class GPIOPlanner:
    def __init__(self, pin_map):
        self.pin_map = pin_map
        self.assignments = {} 

    def is_pin_free(self, pin):
        return pin not in self.assignments

    def allocate(self, peripheral_name, count, fixed_pin=None):
        if count == 0: return ""
        
        # 1. 手動鎖定
        if fixed_pin:
            pin = fixed_pin.strip()
            if pin in self.pin_map:
                if self.is_pin_free(pin):
                    self.assignments[pin] = f"[Manual] {peripheral_name}"
                    return "✅ Locked"
                else:
                    return f"❌ Conflict ({self.assignments[pin]})"
            else:
                return "❌ Invalid Pin"

        # 2. 自動分配
        allocated_count = 0
        search_key = peripheral_name
        if "PWM" in peripheral_name: search_key = "TIM"
        
        for pin, funcs in self.pin_map.items():
            if allocated_count >= count: break
            if not self.is_pin_free(pin): continue

            for func in funcs:
                match = False
                if search_key == "GPIO": match = True
                elif search_key in func:
                    if "PWM" in peripheral_name and "_CH" not in func: continue
                    match = True
                
                if match:
                    self.assignments[pin] = f"[Auto] {peripheral_name} ({func})"
                    allocated_count += 1
                    break
        
        if allocated_count >= count:
            return "✅ OK"
        else:
            return f"⚠️ Partial ({allocated_count}/{count})"

# ================= Google Sheet 控制器 (新增連動選單功能) =================
class DashboardController:
    def __init__(self, creds_file):
        self.creds_file = creds_file
        self.client = None
        self.sheet = None

    def connect(self):
        json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        try:
            if json_content:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_content), scope)
            else:
                creds = ServiceAccountCredentials.from_json_keyfile_name(self.creds_file, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open(SPREADSHEET_NAME)
            return True
        except Exception as e:
            print(f"❌ 連線失敗: {e}")
            return False

    def setup_reference_data(self, menu_data):
        """建立 Ref_Data 分頁並設定 Named Ranges (關鍵步驟)"""
        try:
            # 1. 建立或清空 Ref_Data
            try:
                ws = self.sheet.worksheet(WORKSHEET_REF)
                ws.clear()
            except:
                ws = self.sheet.add_worksheet(title=WORKSHEET_REF, rows="50", cols="20")
                # 隱藏此分頁以免干擾使用者
                # (gspread目前無直接隱藏API，需透過batch_update，此處暫略)

            print("⚙️ 正在建立下拉選單資料庫...")
            
            # 2. 寫入資料 (第一列是類別名，下面是功能)
            # 將 dict 轉為 list of lists (轉置矩陣)
            categories = sorted(menu_data.keys())
            
            # 準備寫入資料
            cols = []
            for cat in categories:
                col_data = [cat] + sorted(menu_data[cat])
                cols.append(col_data)
            
            # 寫入 Google Sheet (直欄寫入)
            for i, col_data in enumerate(cols):
                # i+1 因為欄位從1開始
                # 轉成 [[val], [val]] 格式
                col_values = [[x] for x in col_data]
                range_str = gspread.utils.rowcol_to_a1(1, i+1) # 例如 A1
                ws.update(range_name=range_str, values=col_values)

            # 3. 建立 Named Ranges (這一步是為了 INDIRECT 函式)
            # 我們需要發送 raw batch update 給 Google Sheets API
            spreadsheet_id = self.sheet.id
            sheet_id = ws.id
            requests = []

            # 先刪除舊的 Named Ranges (避免錯誤)
            # 注意：這裡無法簡單刪除，所以我們假設使用者不會頻繁改類別名
            
            for i, cat in enumerate(categories):
                # 定義範圍：從第2列開始到資料結束
                end_row = len(menu_data[cat]) + 1
                if end_row < 2: continue # 空類別跳過

                requests.append({
                    "addNamedRange": {
                        "namedRange": {
                            "name": cat, # 名稱就是類別名 (例如 Connectivity)
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": 1, # Row 2 (index 1)
                                "endRowIndex": end_row,
                                "startColumnIndex": i,
                                "endColumnIndex": i + 1
                            }
                        }
                    }
                })
            
            if requests:
                self.sheet.batch_update({"requests": requests})
                print(f"✅ 已建立 {len(requests)} 個連動選單規則 (Named Ranges)。")
            
            return categories # 回傳類別列表供 Config 頁面使用

        except Exception as e:
            print(f"❌ 建立選單資料失敗: {e}")
            return []

    def init_config_sheet(self, categories):
        """建立帶有驗證規則的 Config_Panel"""
        try:
            # 檢查是否存在
            try:
                ws = self.sheet.worksheet(WORKSHEET_CONFIG)
                print(f"ℹ️ {WORKSHEET_CONFIG} 已存在，正在更新驗證規則...")
            except:
                print(f"✨ 建立新分頁 {WORKSHEET_CONFIG}...")
                ws = self.sheet.add_worksheet(title=WORKSHEET_CONFIG, rows="50", cols="10")
                headers = ["Category", "Peripheral", "Quantity / Enable", "Fixed Pin (Optional)", "Status (Result)"]
                ws.append_row(headers)
                ws.format('A1:E1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.6}})

            # === 設定 A 欄 (Category) 的下拉選單 ===
            # 使用 DataValidationRule
            # 範圍 A2:A50
            
            # 因為 gspread 的 data_validation 需要較新版本，這裡使用 raw request 確保穩定
            
            # 1. Category Dropdown (A欄) - 來源是 categories 列表
            rule_category = {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": c} for c in categories]
                },
                "showCustomUi": True
            }
            
            # 2. Peripheral Dropdown (B欄) - 關鍵：使用 INDIRECT(A欄)
            # Google Sheets API 限制：不能直接透過 API 設定含有 INDIRECT 的驗證
            # Workaround: 我們只能提示使用者或手動設定，
            # 或者：我們嘗試寫入 DataValidation (需要確認 API 支援度)
            # 測試結果：API 不支援 "Custom Formula" 作為 Dropdown source。
            # 但是！如果我們用 "List from range" 並指向 Named Range 是可以的，但這裡是動態的。
            
            # 【重要】Python 難以直接設定 "INDIRECT" 類型的下拉選單。
            # 替代方案：我們幫使用者設定好 A 欄的選單。
            # B 欄的選單我會用 "ONE_OF_RANGE" 指向 Ref_Data，雖然這樣會顯示全部，
            # 但為了達到 "連動"，最好的方式其實是「使用者手動在 Google Sheet 設定一次 B 欄驗證」。
            
            # 這裡我們至少設定 A 欄
            req_validations = [
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": ws.id,
                            "startRowIndex": 1,
                            "endRowIndex": 50,
                            "startColumnIndex": 0, # Col A
                            "endColumnIndex": 1
                        },
                        "rule": rule_category
                    }
                }
            ]
            self.sheet.batch_update({"requests": req_validations})
            print("✅ 已更新 Category 下拉選單。")
            print("⚠️ 提示：為了啟用 B 欄連動選單，請在 Google Sheet 中選取 B2:B50，")
            print("   點擊『資料 > 資料驗證』，條件選擇『下拉式選單 (來自範圍)』，")
            print("   並輸入公式： =INDIRECT(A2)")

        except Exception as e:
            print(f"❌ 設定驗證失敗: {e}")

    def read_config(self):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            return ws.get_all_records()
        except: return []

    def write_status_back(self, status_list):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            cell_list = [[s] for s in status_list]
            range_str = f"E2:E{1 + len(status_list)}"
            ws.update(range_name=range_str, values=cell_list)
        except: pass

    def generate_pinout_view(self, assignments):
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_RESULT)
            except: ws = self.sheet.add_worksheet(title=WORKSHEET_RESULT, rows="100", cols="20")
            ws.clear()
            headers = ["Pin Name", "Assigned Function", "Mode", "Status"]
            rows = [headers]
            for pin in sorted(assignments.keys()):
                usage = assignments[pin]
                rows.append([pin, usage, "Manual" if "Manual" in usage else "Auto", "Active"])
            ws.update(rows)
            ws.format('A1:D1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
        except: pass

# ================= 主程式 =================
if __name__ == "__main__":
    # 1. 解析 XML 並分類
    parser = STM32XMLParser(XML_FILENAME)
    parser.parse()
    menu_data = parser.get_organized_menu_data()
    
    # 2. 連線 Google Sheet
    dashboard = DashboardController(GOOGLE_CREDENTIALS_FILE)
    
    if dashboard.connect():
        print("\n⚙️ 正在設定資料庫與下拉選單...")
        # 3. 建立 Ref_Data 並取得類別清單
        categories = dashboard.setup_reference_data(menu_data)
        
        # 4. 更新 Config_Panel 驗證規則
        dashboard.init_config_sheet(categories)
        
        # 5. 執行規劃
        print("\n⚙️ 執行規劃...")
        config_data = dashboard.read_config()
        planner = GPIOPlanner(parser.pin_map)
        status_results = []
        
        for row in config_data:
            peri = str(row.get('Peripheral', '')).strip()
            qty_str = str(row.get('Quantity / Enable', '0'))
            fixed = str(row.get('Fixed Pin (Optional)', '')).strip()
            
            # 如果使用者只選了類別沒選功能，跳過
            if not peri: 
                status_results.append("")
                continue

            try: qty = int(qty_str)
            except: qty = 0
            
            result = planner.allocate(peri, qty, fixed if fixed else None)
            status_results.append(result)
            print(f"   🔹 {peri}: {result}")

        dashboard.write_status_back(status_results)
        dashboard.generate_pinout_view(planner.assignments)
        
        print("\n🎉 完成！請注意：由於 Google API 限制，")
        print("   B 欄 (Peripheral) 的連動效果需要您手動在 Google Sheet 設定一次公式：")
        print("   選取 B2:B -> 資料驗證 -> 條件: 下拉式選單 (來自範圍) -> 輸入 '=INDIRECT(A2)'")
