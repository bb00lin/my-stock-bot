import os
import sys
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
WORKSHEET_REF = 'Ref_Data'

# STM32 Timer Metadata (供顯示用)
TIMER_METADATA = {
    "TIM1": "16-bit, Advanced", "TIM8": "16-bit, Advanced",
    "TIM2": "32-bit, General",  "TIM5": "32-bit, General",
    "TIM3": "16-bit, General",  "TIM4": "16-bit, General",
    "TIM12": "16-bit, General", "TIM13": "16-bit, General", "TIM14": "16-bit, General",
    "TIM6": "16-bit, Basic",    "TIM7": "16-bit, Basic"
}

def log(msg):
    """印出帶有時間戳記的 Log，強制刷新緩衝區以免 GitHub Actions 卡住"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ================= XML 解析器 =================
class STM32XMLParser:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.pin_map = defaultdict(list)
        self.detected_peripherals = set()

    def parse(self):
        log(f"📖 正在讀取 XML: {self.xml_path}")
        if not os.path.exists(self.xml_path):
            log(f"❌ 嚴重錯誤：找不到 XML 檔案 '{self.xml_path}'！")
            log(f"   目前目錄下的檔案: {os.listdir('.')}")
            sys.exit(1)

        try:
            tree = ET.parse(self.xml_path)
            root = tree.getroot()
            # 處理 XML Namespace
            ns = {'ns': 'http://mcd.rou.st.com/modules.php?name=mcu'}
            pins = root.findall("ns:Pin", ns)
            
            for pin in pins:
                pin_name = pin.attrib.get('Name')
                if pin_name.startswith("V") and len(pin_name) < 4: continue
                
                signals = pin.findall('ns:Signal', ns)
                for sig in signals:
                    sig_name = sig.attrib.get('Name')
                    if sig_name.startswith("GPIO"): continue

                    self.pin_map[pin_name].append(sig_name)
                    
                    # 提取週邊類型 (例如 I2C1_SDA -> I2C)
                    raw_peri = sig_name.split('_')[0]
                    peri_type = re.sub(r'\d+', '', raw_peri)
                    if "OTG" in sig_name: peri_type = "USB_OTG"
                    self.detected_peripherals.add(peri_type)
            
            for p in self.pin_map: self.pin_map[p].sort()
            log(f"✅ XML 解析完成，可用 I/O 數: {len(self.pin_map)}")
        except Exception as e:
            log(f"❌ XML 解析失敗: {e}")
            sys.exit(1)

    def get_organized_menu_data(self):
        categories = {
            "System_Core": ["GPIO", "NVIC", "RCC", "SYS", "PWR"],
            "Connectivity": ["I2C", "SPI", "UART", "USART", "ETH", "USB", "FDCAN", "SDMMC"],
            "Timers": ["TIM", "LPTIM", "RTC"],
            "Analog": ["ADC", "DAC"],
            "Multimedia": ["SAI", "I2S", "LTDC"],
            "Security": ["CRYP", "HASH"]
        }
        menu = defaultdict(list)
        for peri in sorted(self.detected_peripherals):
            assigned = False
            for cat, keywords in categories.items():
                if peri in keywords:
                    menu[cat].append(peri)
                    assigned = True
                    break
            if not assigned: menu["Other"].append(peri)
        return menu

# ================= 規劃核心演算法 =================
class GPIOPlanner:
    def __init__(self, pin_map):
        self.pin_map = pin_map
        self.assignments = {} 

    def is_pin_free(self, pin):
        return pin not in self.assignments

    def find_pin_for_signal(self, signal_regex, exclude_pins=[]):
        """尋找符合 Regex 訊號名稱的空閒腳位"""
        for pin, funcs in self.pin_map.items():
            if not self.is_pin_free(pin) or pin in exclude_pins: continue
            for func in funcs:
                if re.match(signal_regex, func):
                    return pin, func
        return None, None

    def allocate_group(self, peri_type, count, option_str=""):
        """分配整組週邊 (例如 I2C=SCL+SDA)"""
        if count == 0: return ""
        results = []
        success_groups = 0
        
        # 進階選項解析
        needs_rts_cts = "RTS_CTS" in str(option_str).upper()
        needs_nss = "NSS" in str(option_str).upper()
        
        # 嘗試 Instance 1~12 (涵蓋大部分 STM32)
        for i in range(1, 13):
            if success_groups >= count: break
            inst_name = f"{peri_type}{i}"
            required_signals = {}
            
            # 定義各週邊需要的訊號組合
            if "I2C" in peri_type:
                required_signals = {"SCL": f"{inst_name}_SCL", "SDA": f"{inst_name}_SDA"}
            elif "SPI" in peri_type:
                required_signals = {"SCK": f"{inst_name}_SCK", "MISO": f"{inst_name}_MISO", "MOSI": f"{inst_name}_MOSI"}
                if needs_nss: required_signals["NSS"] = f"{inst_name}_NSS"
            elif "UART" in peri_type or "USART" in peri_type:
                required_signals = {"TX": f"{inst_name}_TX", "RX": f"{inst_name}_RX"}
                if needs_rts_cts:
                    required_signals["RTS"] = f"{inst_name}_RTS"
                    required_signals["CTS"] = f"{inst_name}_CTS"
            elif "TIM" in peri_type or "PWM" in peri_type:
                inst_name = "PWM" # PWM 不綁定特定 Timer
                pass 

            temp_assignment = {}
            possible = True
            
            if "PWM" in peri_type:
                # PWM 邏輯：找任意 TIMx_CHx
                pin, func = self.find_pin_for_signal(r"TIM\d+_CH\d+")
                if pin:
                    tim_inst = func.split('_')[0]
                    meta = TIMER_METADATA.get(tim_inst, "Unknown")
                    full_desc = f"{func} [{meta}]"
                    temp_assignment[pin] = full_desc
                else: possible = False
            else:
                # 一般週邊邏輯：找齊所有訊號
                for role, sig_name in required_signals.items():
                    pin, func = self.find_pin_for_signal(f"^{sig_name}$", exclude_pins=temp_assignment.keys())
                    if pin: temp_assignment[pin] = func
                    else: possible = False; break
            
            if possible:
                # 確定分配
                for p, f in temp_assignment.items():
                    desc = f"[Auto] {inst_name} ({f})"
                    self.assignments[p] = desc
                success_groups += 1
                results.append(f"✅ {inst_name}")

        if success_groups >= count: return f"✅ OK ({success_groups}/{count})"
        else: return f"❌ Insufficient ({success_groups}/{count})"
        
    def allocate_manual(self, peri_name, pin):
        """手動鎖定特定腳位"""
        pin = pin.strip()
        if pin in self.pin_map:
            if self.is_pin_free(pin):
                self.assignments[pin] = f"[Manual] {peri_name}"
                return "✅ Locked"
            else: return f"❌ Conflict ({self.assignments[pin]})"
        else: return "❌ Invalid Pin"

# ================= Google Sheet 控制器 =================
class DashboardController:
    def __init__(self):
        self.client = None
        self.sheet = None

    def connect(self):
        log("🔌 正在連線 Google Sheet...")
        json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        if not json_content:
            log("❌ 嚴重錯誤：GitHub Secret 'GOOGLE_SHEETS_JSON' 未設定！")
            return False
            
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_content), scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open(SPREADSHEET_NAME)
            log(f"✅ 成功連線至表單: {SPREADSHEET_NAME}")
            return True
        except gspread.exceptions.SpreadsheetNotFound:
            log(f"❌ 錯誤：找不到表單 '{SPREADSHEET_NAME}'。請確認名稱完全一致。")
            return False
        except Exception as e:
            log(f"❌ 連線失敗: {e}")
            return False

    def setup_reference_data(self, menu_data):
        """建立下拉選單資料庫"""
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_REF)
            except: ws = self.sheet.add_worksheet(title=WORKSHEET_REF, rows="50", cols="20")
            ws.clear()
            
            categories = sorted(menu_data.keys())
            cols = []
            for cat in categories: cols.append([cat] + sorted(menu_data[cat]))
            for i, col_data in enumerate(cols):
                col_values = [[x] for x in col_data]
                range_str = gspread.utils.rowcol_to_a1(1, i+1)
                ws.update(range_name=range_str, values=col_values)
            return categories
        except: return []

    def init_config_sheet(self, categories):
        """初始化設定頁面 (若不存在則建立)"""
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            except:
                ws = self.sheet.add_worksheet(title=WORKSHEET_CONFIG, rows="50", cols="10")
                # 寫入正確的 V4 版標題
                ws.append_row(["Category", "Peripheral", "Quantity (Groups)", "Option / Fixed Pin", "Status (Result)"])
                ws.format('A1:E1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.6}})
            
            # 設定 Category A 欄下拉選單
            rule_category = {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": c} for c in categories]},
                "showCustomUi": True
            }
            req_validations = [{"setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 50, "startColumnIndex": 0, "endColumnIndex": 1},
                "rule": rule_category
            }}]
            self.sheet.batch_update({"requests": req_validations})
        except: pass

    def read_config(self):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            data = ws.get_all_records()
            log(f"📊 讀取到 {len(data)} 筆設定資料。")
            
            # 防呆檢查：確認欄位名稱是否為新版
            if len(data) > 0:
                required = 'Quantity (Groups)'
                if required not in data[0]:
                    log(f"❌ 錯誤：表單欄位不符！請刪除 '{WORKSHEET_CONFIG}' 分頁讓程式重建。")
                    log(f"   預期: {required}, 實際: {list(data[0].keys())}")
                    return []
            return data
        except Exception as e:
            log(f"❌ 讀取設定失敗: {e}")
            return []

    def write_status_back(self, status_list):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            cell_list = [[s] for s in status_list]
            range_str = f"E2:E{1 + len(status_list)}"
            ws.update(range_name=range_str, values=cell_list)
        except: pass

    def generate_pinout_view(self, assignments, total_pins):
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_RESULT)
            except: ws = self.sheet.add_worksheet(title=WORKSHEET_RESULT, rows="100", cols="20")
            ws.clear()
            
            used_count = len(assignments)
            free_count = total_pins - used_count
            ws.update('A1:B4', [['Resource Summary', ''], ['Total GPIO', total_pins], ['Used GPIO', used_count], ['Free GPIO', free_count]])
            ws.format('A1:B4', {'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}})

            headers = ["Pin Name", "Assigned Function", "Detail Spec", "Mode"]
            rows = [headers]
            for pin in sorted(assignments.keys()):
                usage = assignments[pin]
                spec = "-"
                if "TIM" in usage:
                    match = re.search(r'(TIM\d+)', usage)
                    if match: spec = TIMER_METADATA.get(match.group(1), "")
                rows.append([pin, usage, spec, "Manual" if "Manual" in usage else "Auto"])
                
            ws.update('A6', rows)
            ws.format('A6:D6', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
        except: pass

# ================= 主程式執行點 =================
if __name__ == "__main__":
    log("🚀 程式啟動 (stm32_dashboard.py V4)...")
    
    # 1. XML 解析
    parser = STM32XMLParser(XML_FILENAME)
    parser.parse()
    menu_data = parser.get_organized_menu_data()
    
    # 2. 連線
    dashboard = DashboardController()
    if not dashboard.connect(): sys.exit(1)

    # 3. 初始化 (建立 Ref_Data 與 Config_Panel)
    log("⚙️ 初始化表單結構...")
    categories = dashboard.setup_reference_data(menu_data)
    dashboard.init_config_sheet(categories)
    
    # 4. 讀取設定
    log("⚙️ 讀取使用者設定...")
    config_data = dashboard.read_config()
    if not config_data:
        log("⚠️ 設定資料為空或格式錯誤，程式中止。")
        sys.exit(0)

    # 5. 執行規劃
    log("⚙️ 開始執行演算法...")
    planner = GPIOPlanner(parser.pin_map)
    status_results = []
    
    for row in config_data:
        peri = str(row.get('Peripheral', '')).strip()
        qty_str = str(row.get('Quantity (Groups)', '0'))
        option = str(row.get('Option / Fixed Pin', '')).strip()
        
        if not peri: 
            status_results.append("")
            continue

        try: qty = int(qty_str)
        except: qty = 0
        
        # 判斷手動鎖定或自動分配
        is_fixed_pin = re.match(r'^P[A-K]\d+$', option)
        if is_fixed_pin:
            result = planner.allocate_manual(peri, option)
        else:
            result = planner.allocate_group(peri, qty, option)
        
        status_results.append(result)
        log(f"   🔹 {peri} (x{qty}): {result}")

    # 6. 回寫結果
    log("📝 寫回結果至 Google Sheet...")
    dashboard.write_status_back(status_results)
    dashboard.generate_pinout_view(planner.assignments, len(parser.pin_map))
    
    log("🎉 執行成功！")
