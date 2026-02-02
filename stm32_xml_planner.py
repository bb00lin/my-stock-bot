import os
import json
import gspread
import xml.etree.ElementTree as ET
from oauth2client.service_account import ServiceAccountCredentials
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ================= 設定區 =================
XML_FILENAME = "STM32MP133CAFx.xml"
SPREADSHEET_NAME = 'STM32_GPIO_Planner'  # 指定您現有的檔案名稱
WORKSHEET_CONFIG = 'Config_Panel'        # 設定頁面名稱
WORKSHEET_RESULT = 'Pinout_View'         # 結果頁面名稱

# 請確認您的 JSON 金鑰檔名
GOOGLE_CREDENTIALS_FILE = "e-caldron-484313-m4-001936cf040b.json"

# ================= XML 解析器 (維持不變) =================
class STM32XMLParser:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.pin_map = defaultdict(list)

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
                    if sig_name == "GPIO" or sig_name.startswith("GPIO_"): continue
                    self.pin_map[pin_name].append(sig_name)
            
            for p in self.pin_map: self.pin_map[p].sort()
            print(f"✅ XML 解析完成，可用 I/O 數: {len(self.pin_map)}")
        except Exception as e:
            print(f"❌ XML 解析失敗: {e}")

# ================= 規劃核心 (維持不變) =================
class GPIOPlanner:
    def __init__(self, pin_map):
        self.pin_map = pin_map
        self.assignments = {} 
        self.logs = []

    def is_pin_free(self, pin):
        return pin not in self.assignments

    def allocate(self, peripheral_name, count, fixed_pin=None):
        if count == 0: return "Skipped"
        
        # 1. 手動鎖定 (Fixed Pin)
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

        # 2. 自動分配 (Auto)
        allocated_count = 0
        search_key = peripheral_name
        if "PWM" in peripheral_name: search_key = "TIM"
        if "LED" in peripheral_name or "Key" in peripheral_name: search_key = "GPIO"

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

# ================= Google Sheet 控制器 (新增初始化功能) =================
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
            # 這裡開啟現有的表單
            self.sheet = self.client.open(SPREADSHEET_NAME)
            return True
        except Exception as e:
            print(f"❌ 連線失敗: {e}")
            print(f"請確認表單 '{SPREADSHEET_NAME}' 存在，且已共用給 Service Account")
            return False

    def init_config_sheet(self):
        """✨ 核心功能：檢查並自動建立 Config_Panel"""
        try:
            # 檢查分頁是否存在
            existing_titles = [ws.title for ws in self.sheet.worksheets()]
            
            if WORKSHEET_CONFIG in existing_titles:
                print(f"ℹ️ 分頁 '{WORKSHEET_CONFIG}' 已存在，準備讀取...")
                return

            print(f"✨ 分頁 '{WORKSHEET_CONFIG}' 不存在，正在插入新工作表...")
            ws = self.sheet.add_worksheet(title=WORKSHEET_CONFIG, rows="50", cols="10")
            
            # 初始化標題與範例資料
            headers = ["Category", "Peripheral", "Quantity / Enable", "Fixed Pin (Optional)", "Status (Result)"]
            
            # 這是預設的範例設定，您可以隨意修改
            default_data = [
                ["System", "LED_Status", 1, "PE10", ""],
                ["System", "Power_Key", 1, "", ""],
                ["Connectivity", "I2C", 2, "", ""],
                ["Connectivity", "SPI", 1, "", ""],
                ["Connectivity", "UART", 0, "", ""],
                ["Analog", "ADC", 2, "", ""],
                ["Timers", "PWM", 4, "", ""]
            ]
            
            ws.append_row(headers)
            ws.append_rows(default_data)
            
            # 美化標題列 (黃色背景，粗體)
            ws.format('A1:E1', {
                'textFormat': {'bold': True}, 
                'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.6}
            })
            print(f"✅ 已成功建立 '{WORKSHEET_CONFIG}' 並填入範例資料！")

        except Exception as e:
            print(f"❌ 初始化分頁失敗: {e}")

    def read_config(self):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            return ws.get_all_records()
        except Exception as e:
            print(f"❌ 讀取設定失敗: {e}")
            return []

    def write_status_back(self, status_list):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            cell_list = [[s] for s in status_list]
            range_str = f"E2:E{1 + len(status_list)}"
            ws.update(range_name=range_str, values=cell_list)
            print("📊 狀態欄位已更新。")
        except Exception as e:
            print(f"❌ 寫回失敗: {e}")

    def generate_pinout_view(self, assignments):
        """產生詳細結果頁面 (如果沒有會自動建立)"""
        try:
            # 檢查或建立 Pinout_View
            try:
                ws = self.sheet.worksheet(WORKSHEET_RESULT)
            except:
                ws = self.sheet.add_worksheet(title=WORKSHEET_RESULT, rows="100", cols="20")
            
            ws.clear()
            headers = ["Pin Name", "Assigned Function", "Mode", "Status"]
            rows = [headers]
            
            sorted_pins = sorted(assignments.keys())
            for pin in sorted_pins:
                usage = assignments[pin]
                mode = "Manual" if "[Manual]" in usage else "Auto"
                rows.append([pin, usage, mode, "Active"])
                
            ws.update(rows)
            ws.format('A1:D1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
            print(f"✅ 詳細結果已寫入 '{WORKSHEET_RESULT}' 分頁。")
            
        except Exception as e:
            print(f"❌ 生成結果失敗: {e}")

# ================= 主程式 =================
if __name__ == "__main__":
    # 1. 解析 XML
    parser = STM32XMLParser(XML_FILENAME)
    parser.parse()
    
    # 2. 連線 Google Sheet
    dashboard = DashboardController(GOOGLE_CREDENTIALS_FILE)
    
    if dashboard.connect():
        # 3. 【關鍵步驟】初始化 Config_Panel (如果沒有會自動插入)
        dashboard.init_config_sheet()
        
        # 4. 讀取設定並執行規劃
        config_data = dashboard.read_config()
        planner = GPIOPlanner(parser.pin_map)
        status_results = []
        
        print("\n⚙️ 正在根據 Config_Panel 執行運算...")
        
        for row in config_data:
            peri = str(row.get('Peripheral', '')).strip()
            qty_str = str(row.get('Quantity / Enable', '0'))
            fixed = str(row.get('Fixed Pin (Optional)', '')).strip()
            
            try: qty = int(qty_str)
            except: qty = 0
            
            if not peri: 
                status_results.append("")
                continue

            result = planner.allocate(peri, qty, fixed if fixed else None)
            status_results.append(result)
            print(f"   🔹 {peri}: {result}")

        # 5. 回寫結果
        dashboard.write_status_back(status_results)
        dashboard.generate_pinout_view(planner.assignments)
        
        print("\n🎉 全部完成！請查看 Google Sheet。")
