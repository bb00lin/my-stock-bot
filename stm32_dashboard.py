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

# STM32 Timer Metadata
# 根據 STM32MP133 參考手冊: TIM2, TIM5 為 32-bit
TIMER_METADATA = {
    "TIM1": "16-bit, Advanced", "TIM8": "16-bit, Advanced",
    "TIM2": "32-bit, General",  "TIM5": "32-bit, General",
    "TIM3": "16-bit, General",  "TIM4": "16-bit, General",
    "TIM12": "16-bit, General", "TIM13": "16-bit, General", "TIM14": "16-bit, General",
    "TIM6": "16-bit, Basic",    "TIM7": "16-bit, Basic"
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ================= XML 解析器 =================
class STM32XMLParser:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.pin_map = defaultdict(list)
        self.detected_peripherals = set()

    def parse(self):
        log(f"📖 讀取 XML: {self.xml_path}")
        if not os.path.exists(self.xml_path):
            log(f"❌ 找不到 XML: {self.xml_path}")
            sys.exit(1)

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

                    self.pin_map[pin_name].append(sig_name)
                    
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
        all_peris = sorted(list(self.detected_peripherals)) # 扁平化列表供 B 欄使用
        
        for peri in all_peris:
            assigned = False
            for cat, keywords in categories.items():
                if peri in keywords:
                    menu[cat].append(peri)
                    assigned = True
                    break
            if not assigned: menu["Other"].append(peri)
            
        return menu, all_peris

# ================= 規劃核心 =================
class GPIOPlanner:
    def __init__(self, pin_map):
        self.pin_map = pin_map
        self.assignments = {} 

    def is_pin_free(self, pin):
        return pin not in self.assignments

    def find_pin_for_signal(self, signal_regex, exclude_pins=[], preferred_instances=None):
        """
        preferred_instances: list of strings, e.g. ['TIM2', 'TIM5']
        """
        # 第一次掃描：優先尋找 preferred_instances
        if preferred_instances:
            for pin, funcs in self.pin_map.items():
                if not self.is_pin_free(pin) or pin in exclude_pins: continue
                for func in funcs:
                    if re.match(signal_regex, func):
                        # 檢查是否屬於偏好的 Instance
                        for pref in preferred_instances:
                            if func.startswith(pref):
                                return pin, func
        
        # 如果沒指定偏好，或偏好的找不到，則進行一般搜尋 (除非強制要求)
        # 這裡的邏輯是：如果有指定 32-bit 但找不到，就會回傳 None (嚴格模式)
        if preferred_instances:
            return None, None

        # 第二次掃描：任意匹配
        for pin, funcs in self.pin_map.items():
            if not self.is_pin_free(pin) or pin in exclude_pins: continue
            for func in funcs:
                if re.match(signal_regex, func):
                    return pin, func
        return None, None

    def allocate_group(self, peri_type, count, option_str=""):
        if count == 0: return ""
        results = []
        success_groups = 0
        
        # 解析選項
        opt_upper = str(option_str).upper()
        needs_rts_cts = "RTS_CTS" in opt_upper
        needs_nss = "NSS" in opt_upper
        force_32bit = "32-BIT" in opt_upper or "32BIT" in opt_upper
        
        # 決定搜尋範圍
        search_range = range(1, 15) # Default
        
        # 如果是 PWM 且要求 32-bit，我們不遍歷 Instance，而是直接找 TIM2/TIM5
        if "PWM" in peri_type and force_32bit:
            target_timers = ["TIM2", "TIM5"]
        else:
            target_timers = None # 任意 Timer

        # 針對 UART/I2C/SPI 等的一般邏輯
        for i in search_range:
            if success_groups >= count: break
            
            # 決定 Instance 名稱
            if "PWM" in peri_type:
                inst_name = "PWM_32bit" if force_32bit else "PWM"
            else:
                inst_name = f"{peri_type}{i}"
            
            required_signals = {}
            # ... (信號定義與之前相同) ...
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

            temp_assignment = {}
            possible = True
            
            if "PWM" in peri_type:
                # 這裡傳入 target_timers (['TIM2', 'TIM5'])
                pin, func = self.find_pin_for_signal(r"TIM\d+_CH\d+", preferred_instances=target_timers)
                if pin:
                    tim_inst = func.split('_')[0]
                    meta = TIMER_METADATA.get(tim_inst, "Unknown")
                    full_desc = f"{func} [{meta}]"
                    temp_assignment[pin] = full_desc
                else: possible = False
            else:
                for role, sig_name in required_signals.items():
                    pin, func = self.find_pin_for_signal(f"^{sig_name}$", exclude_pins=temp_assignment.keys())
                    if pin: temp_assignment[pin] = func
                    else: possible = False; break
            
            if possible:
                for p, f in temp_assignment.items():
                    # 對於 PWM，如果分配到了，我們通常只算成功分配了一組
                    # 但為了讓 PWM 可以分配多次 (例如 TIM2_CH1, TIM2_CH2)，我們不需要切換 Loop i
                    # 這裡簡化處理：如果 PWM 成功，直接當作成功一組
                    desc = f"[Auto] {inst_name} ({f})"
                    self.assignments[p] = desc
                success_groups += 1
                results.append(f"✅ {inst_name}")
            
            # 對於 PWM，不要因為一次成功就跳過迴圈，因為 TIM2 有多個通道
            # 但一般的 Instance (I2C1) 用完就沒了
            if "PWM" not in peri_type and possible:
                pass # Continue to next instance i

        if success_groups >= count: return f"✅ OK ({success_groups}/{count})"
        else: return f"❌ Insufficient ({success_groups}/{count})"
        
    def allocate_manual(self, peri_name, pin):
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
        # ... (連線邏輯不變) ...
        log("🔌 正在連線 Google Sheet...")
        json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        if not json_content: return False
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_content), scope)
            self.client = gspread.authorize(creds)
            self.sheet = self.client.open(SPREADSHEET_NAME)
            log(f"✅ 成功連線: {SPREADSHEET_NAME}")
            return True
        except: return False

    def setup_reference_data(self, menu_data):
        # ... (略) ...
        pass

    def init_config_sheet(self, categories, all_peris):
        """修正版：同時設定 A 欄與 B 欄的驗證"""
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            except:
                ws = self.sheet.add_worksheet(title=WORKSHEET_CONFIG, rows="50", cols="10")
                ws.append_row(["Category", "Peripheral", "Quantity (Groups)", "Option / Fixed Pin", "Status (Result)"])
                ws.format('A1:E1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.6}})
            
            # A 欄 (Category) 下拉選單
            rule_cat = {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": c} for c in categories]},
                "showCustomUi": True
            }
            
            # B 欄 (Peripheral) 下拉選單 - 直接給所有功能的大清單
            rule_peri = {
                "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": p} for p in all_peris]},
                "showCustomUi": True
            }

            reqs = [
                {"setDataValidation": {
                    "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 50, "startColumnIndex": 0, "endColumnIndex": 1},
                    "rule": rule_cat
                }},
                {"setDataValidation": {
                    "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 50, "startColumnIndex": 1, "endColumnIndex": 2},
                    "rule": rule_peri
                }}
            ]
            self.sheet.batch_update({"requests": reqs})
            log("✅ 下拉選單 (A欄, B欄) 已更新。")
        except Exception as e:
            log(f"⚠️ 設定選單失敗: {e}")

    def read_config(self):
        try:
            return self.sheet.worksheet(WORKSHEET_CONFIG).get_all_records()
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
            
            used_count = len(assignments)
            free_count = total_pins - used_count
            ws.update('A1:B4', [['Resource Summary', ''], ['Total GPIO', total_pins], ['Used GPIO', used_count], ['Free GPIO', free_count]])
            ws.format('A1:B4', {'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}})

            headers = ["Pin Name", "Assigned Function", "Detail Spec", "Mode"]
            rows = [headers]
            for pin in sorted(assignments.keys()):
                usage = assignments[pin]
                spec = "-"
                # 解析 Timer 規格
                if "TIM" in usage:
                    match = re.search(r'(TIM\d+)', usage)
                    if match: spec = TIMER_METADATA.get(match.group(1), "")
                
                rows.append([pin, usage, spec, "Manual" if "Manual" in usage else "Auto"])
                
            ws.update('A6', rows)
            ws.format('A6:D6', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
        except: pass

# ================= 主程式執行點 =================
if __name__ == "__main__":
    log("🚀 程式啟動 (V5)...")
    
    parser = STM32XMLParser(XML_FILENAME)
    parser.parse()
    # 這裡現在回傳兩個值：分類字典, 所有功能列表
    menu_data, all_peris = parser.get_organized_menu_data()
    
    dashboard = DashboardController()
    if not dashboard.connect(): sys.exit(1)

    log("⚙️ 初始化表單選單...")
    categories = dashboard.setup_reference_data(menu_data)
    # 將所有功能列表傳入，設定 B 欄選單
    dashboard.init_config_sheet(categories, all_peris)
    
    log("⚙️ 讀取設定...")
    config_data = dashboard.read_config()
    
    log("⚙️ 執行規劃...")
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
        
        is_fixed_pin = re.match(r'^P[A-K]\d+$', option)
        if is_fixed_pin:
            result = planner.allocate_manual(peri, option)
        else:
            result = planner.allocate_group(peri, qty, option)
        
        status_results.append(result)
        log(f"   🔹 {peri} (x{qty}): {result}")

    log("📝 寫回結果...")
    dashboard.write_status_back(status_results)
    dashboard.generate_pinout_view(planner.assignments, len(parser.pin_map))
    
    log("🎉 執行成功！")
