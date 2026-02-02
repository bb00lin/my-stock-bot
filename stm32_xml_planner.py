import os
import json
import re
import gspread
import xml.etree.ElementTree as ET
from oauth2client.service_account import ServiceAccountCredentials
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ================= 設定區 =================
XML_FILENAME = "STM32MP133CAFx.xml"
SPREADSHEET_NAME = 'STM32_GPIO_Planner'
WORKSHEET_CONFIG = 'Config_Panel'
WORKSHEET_RESULT = 'Pinout_View'
WORKSHEET_REF = 'Ref_Data'
GOOGLE_CREDENTIALS_FILE = "e-caldron-484313-m4-001936cf040b.json"

# ================= 資料庫增強 =================
# STM32MP133 的 Timer 規格 (Hard-coded metadata)
TIMER_METADATA = {
    "TIM1": "16-bit, Advanced", "TIM8": "16-bit, Advanced",
    "TIM2": "32-bit, General",  "TIM5": "32-bit, General",
    "TIM3": "16-bit, General",  "TIM4": "16-bit, General",
    "TIM12": "16-bit, General", "TIM13": "16-bit, General", "TIM14": "16-bit, General",
    "TIM6": "16-bit, Basic",    "TIM7": "16-bit, Basic"
}

# ================= XML 解析器 =================
class STM32XMLParser:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.pin_map = defaultdict(list)
        self.detected_peripherals = set()
        self.af_map = {} # { 'PA5_TIM2_CH1': 'AF1' }

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
                    if sig_name.startswith("GPIO"): continue

                    # 紀錄 AF 編號 (通常 XML 屬性會有，若無則模擬)
                    # STM32 XML 通常在 Signal 的 Parameter 裡有 AF 設定
                    # 這裡為了簡化，我們先儲存訊號名稱
                    self.pin_map[pin_name].append(sig_name)
                    
                    # 提取週邊類型
                    raw_peri = sig_name.split('_')[0]
                    peri_type = re.sub(r'\d+', '', raw_peri)
                    if "OTG" in sig_name: peri_type = "USB_OTG"
                    self.detected_peripherals.add(peri_type)
            
            for p in self.pin_map: self.pin_map[p].sort()
            print(f"✅ XML 解析完成，可用 I/O 數: {len(self.pin_map)}")
        except Exception as e:
            print(f"❌ XML 解析失敗: {e}")

    def get_organized_menu_data(self):
        # ... (維持之前的分類邏輯) ...
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

# ================= 規劃核心 (邏輯大幅升級) =================
class GPIOPlanner:
    def __init__(self, pin_map):
        self.pin_map = pin_map
        self.assignments = {} 
        self.peripheral_usage = defaultdict(int) # 紀錄例如 I2C1 用了幾次

    def is_pin_free(self, pin):
        return pin not in self.assignments

    def find_pin_for_signal(self, signal_regex, exclude_pins=[]):
        """尋找支援特定訊號的空閒腳位"""
        for pin, funcs in self.pin_map.items():
            if not self.is_pin_free(pin) or pin in exclude_pins: continue
            for func in funcs:
                if re.match(signal_regex, func):
                    return pin, func
        return None, None

    def allocate_group(self, peri_type, count, option_str=""):
        """分配一整組功能 (例如 I2C x 1 = SCL + SDA)"""
        if count == 0: return ""
        
        results = []
        success_groups = 0
        
        # 解析選項
        needs_rts_cts = "RTS_CTS" in str(option_str).upper()
        needs_nss = "NSS" in str(option_str).upper()
        
        # 尋找可用的 Instance (例如 I2C1, I2C2...)
        # 這裡用簡單的掃描法：遍歷所有可能的 Instance 編號 (1~8)
        for i in range(1, 9):
            if success_groups >= count: break
            
            inst_name = f"{peri_type}{i}" # e.g., I2C1
            
            # 定義該週邊需要的訊號列表
            required_signals = {}
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
                # PWM 特殊處理：只需要一個通道
                # 這裡假設 "1組 PWM" = "1個 Timer Channel"
                # 我們不鎖定 Instance，而是尋找任意可用的 TIMx_CHy
                inst_name = "PWM" # 標記用
                pass 

            # 開始嘗試分配這組的所有腳位
            temp_assignment = {}
            possible = True
            
            if "PWM" in peri_type:
                # PWM 單獨邏輯
                pin, func = self.find_pin_for_signal(r"TIM\d+_CH\d+")
                if pin:
                    # 取得 Timer 詳細資訊
                    tim_inst = func.split('_')[0]
                    meta = TIMER_METADATA.get(tim_inst, "Unknown")
                    full_desc = f"{func} [{meta}]"
                    temp_assignment[pin] = full_desc
                else:
                    possible = False
            else:
                # 一般週邊邏輯
                for role, sig_name in required_signals.items():
                    # 嚴格匹配訊號名稱
                    pin, func = self.find_pin_for_signal(f"^{sig_name}$", exclude_pins=temp_assignment.keys())
                    if pin:
                        temp_assignment[pin] = func
                    else:
                        possible = False
                        break # 這一組失敗，換下一個 Instance
            
            if possible:
                # 確認分配
                for p, f in temp_assignment.items():
                    desc = f"[Auto] {inst_name} ({f})"
                    self.assignments[p] = desc
                
                success_groups += 1
                results.append(f"✅ {inst_name}")
            # else:
            #     results.append(f"⚠️ {inst_name} Failed")

        if success_groups >= count:
            return f"✅ OK ({success_groups}/{count})"
        else:
            return f"❌ Insufficient ({success_groups}/{count})"

# ================= Google Sheet 控制器 =================
class DashboardController:
    def __init__(self, creds_file):
        self.creds_file = creds_file
        self.client = None
        self.sheet = None

    def connect(self):
        json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        try:
            if json_content: creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_content), scope)
            else: creds = ServiceAccountCredentials.from_json_keyfile_name(self.creds_file, scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open(SPREADSHEET_NAME)
            return True
        except: return False

    def setup_reference_data(self, menu_data):
        # ... (與 v3 相同，略過以節省篇幅) ...
        pass

    def init_config_sheet(self, categories):
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            except:
                ws = self.sheet.add_worksheet(title=WORKSHEET_CONFIG, rows="50", cols="10")
                ws.append_row(["Category", "Peripheral", "Quantity (Groups)", "Option / Fixed Pin", "Status (Result)"])
                ws.format('A1:E1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.6}})
            
            # 設定 A 欄選單 (與 v3 相同)
            # ...
        except: pass

    def read_config(self):
        try: return self.sheet.worksheet(WORKSHEET_CONFIG).get_all_records()
        except: return []

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
            
            # 統計
            used_count = len(assignments)
            free_count = total_pins - used_count
            
            # Summary Header
            ws.update('A1:B1', [['Resource Summary', '']])
            ws.update('A2:B2', [['Total GPIO', total_pins]])
            ws.update('A3:B3', [['Used GPIO', used_count]])
            ws.update('A4:B4', [['Free GPIO', free_count]])
            ws.format('A1:B4', {'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}})

            # Detail Table
            headers = ["Pin Name", "Assigned Function", "Detail Spec", "Mode"]
            rows = [headers]
            
            for pin in sorted(assignments.keys()):
                usage = assignments[pin]
                # 解析詳細規格 (例如把括號裡的 TIM2 拿出來查表)
                spec = "-"
                if "TIM" in usage:
                    # 簡易提取 TIMx
                    match = re.search(r'(TIM\d+)', usage)
                    if match:
                        tim_name = match.group(1)
                        spec = TIMER_METADATA.get(tim_name, "")
                
                mode = "Manual" if "Manual" in usage else "Auto"
                rows.append([pin, usage, spec, mode])
                
            ws.update('A6', rows)
            ws.format('A6:D6', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
            
        except: pass

# ================= 主程式 =================
if __name__ == "__main__":
    parser = STM32XMLParser(XML_FILENAME)
    parser.parse()
    # ... (省略中間選單設定，與 v3 相同) ...
    
    dashboard = DashboardController(GOOGLE_CREDENTIALS_FILE)
    if dashboard.connect():
        # ...
        
        print("\n⚙️ 執行進階規劃...")
        config_data = dashboard.read_config()
        planner = GPIOPlanner(parser.pin_map)
        status_results = []
        
        for row in config_data:
            peri = str(row.get('Peripheral', '')).strip()
            qty_str = str(row.get('Quantity (Groups)', '0')) # 注意欄位名稱變更
            option = str(row.get('Option / Fixed Pin', '')).strip()
            
            if not peri: 
                status_results.append("")
                continue

            try: qty = int(qty_str)
            except: qty = 0
            
            # 判斷是手動鎖定還是自動分配
            # 如果 option 看起來像腳位 (P開頭且短)，當作手動鎖定
            # 否則當作進階選項 (RTS_CTS, NSS)
            is_fixed_pin = re.match(r'^P[A-K]\d+$', option)
            
            if is_fixed_pin:
                result = planner.allocate(peri, qty, fixed_pin=option) # 舊的手動邏輯
            else:
                result = planner.allocate_group(peri, qty, option_str=option) # 新的整組分配邏輯
            
            status_results.append(result)
            print(f"   🔹 {peri} (x{qty}): {result}")

        dashboard.write_status_back(status_results)
        dashboard.generate_pinout_view(planner.assignments, len(parser.pin_map))
        print("🎉 執行完畢！")
