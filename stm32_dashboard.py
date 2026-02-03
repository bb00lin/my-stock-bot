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
                    # 記錄所有功能
                    self.pin_map[pin_name].append(sig_name)
                    
                    if sig_name.startswith("GPIO"): continue

                    raw_peri = sig_name.split('_')[0]
                    peri_type = re.sub(r'\d+', '', raw_peri)
                    if "OTG" in sig_name: peri_type = "USB_OTG"
                    self.detected_peripherals.add(peri_type)
            
            # 手動補充系統關鍵字
            self.detected_peripherals.add("DDR")
            self.detected_peripherals.add("FMC")
            self.detected_peripherals.add("SDMMC")
            self.detected_peripherals.add("QUADSPI")
            
            for p in self.pin_map: self.pin_map[p].sort()
            log(f"✅ XML 解析完成，可用 I/O 數: {len(self.pin_map)}")
        except Exception as e:
            log(f"❌ XML 解析失敗: {e}")
            sys.exit(1)

    def get_organized_menu_data(self):
        categories = {
            "System_Critical": ["DDR", "FMC", "SDMMC", "QUADSPI"],
            "System_Core": ["GPIO", "NVIC", "RCC", "SYS", "PWR"],
            "Connectivity": ["I2C", "SPI", "UART", "USART", "ETH", "USB", "FDCAN"],
            "Timers": ["TIM", "LPTIM", "RTC"],
            "Analog": ["ADC", "DAC"],
            "Multimedia": ["SAI", "I2S", "LTDC"],
            "Security": ["CRYP", "HASH"]
        }
        menu = defaultdict(list)
        all_peris = sorted(list(self.detected_peripherals))
        
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

    def normalize_option(self, text):
        if not text: return ""
        return re.sub(r'[\s_\-,/]+', '', str(text).upper())

    def find_pin_for_signal(self, signal_regex, exclude_pins=[], preferred_instances=None):
        # 1. 優先搜尋
        if preferred_instances:
            for pin, funcs in self.pin_map.items():
                if not self.is_pin_free(pin) or pin in exclude_pins: continue
                for func in funcs:
                    if re.match(signal_regex, func):
                        for pref in preferred_instances:
                            if func.startswith(pref): return pin, func
            return None, None

        # 2. 一般搜尋
        for pin, funcs in self.pin_map.items():
            if not self.is_pin_free(pin) or pin in exclude_pins: continue
            for func in funcs:
                if re.match(signal_regex, func):
                    return pin, func
        return None, None

    def allocate_system_critical(self, peri_type, row_idx):
        """鎖定系統關鍵腳位"""
        locked_count = 0
        if "DDR" in peri_type:
            for pin, funcs in self.pin_map.items():
                if not self.is_pin_free(pin): continue
                for func in funcs:
                    if func.startswith("DDR_") or func.startswith("DDRPHYC_"):
                        self.assignments[pin] = {'desc': f"[System] {peri_type} ({func})", 'row': row_idx, 'mode': 'Critical'}
                        locked_count += 1
                        break
        else:
            target_peri = peri_type 
            for pin, funcs in self.pin_map.items():
                if not self.is_pin_free(pin): continue
                for func in funcs:
                    if func.startswith(target_peri):
                         self.assignments[pin] = {'desc': f"[System] {peri_type} ({func})", 'row': row_idx, 'mode': 'Critical'}
                         locked_count += 1
                         break
        if locked_count > 0: return f"✅ Reserved {locked_count} pins"
        else: return "⚠️ No pins found/locked"

    def allocate_group(self, peri_type, count, option_str="", row_idx=0):
        if count == 0: return ""
        
        # 攔截系統關鍵字
        if peri_type in ["DDR", "FMC", "SDMMC", "QUADSPI"]:
            return self.allocate_system_critical(peri_type, row_idx)

        results = []
        success_groups = 0
        opt_clean = self.normalize_option(option_str)
        needs_rts_cts = ("RTS" in opt_clean and "CTS" in opt_clean)
        needs_nss = "NSS" in opt_clean
        force_32bit = "32BIT" in opt_clean
        force_16bit = "16BIT" in opt_clean
        
        search_range = range(1, 15)
        target_timers = None
        if "PWM" in peri_type:
            if force_32bit: target_timers = ["TIM2", "TIM5"]
            elif force_16bit: target_timers = ["TIM1", "TIM3", "TIM4", "TIM8", "TIM12", "TIM13", "TIM14", "TIM6", "TIM7"]

        for i in search_range:
            if success_groups >= count: break
            inst_name = "PWM" if "PWM" in peri_type else f"{peri_type}{i}"
            
            required_signals = {}
            if "I2C" in peri_type:
                required_signals = {"SCL": f"{inst_name}_SCL", "SDA": f"{inst_name}_SDA"}
            elif "SPI" in peri_type:
                required_signals = {"SCK": f"{inst_name}_SCK", "MISO": f"{inst_name}_MISO", "MOSI": f"{inst_name}_MOSI"}
                if needs_nss: required_signals["NSS"] = f"{inst_name}_NSS"
            elif "UART" in peri_type or "USART" in peri_type:
                required_signals = {"TX": f"{inst_name}_TX", "RX": f"{inst_name}_RX"}
                if needs_rts_cts:
                    # 修正處：將賦值分開寫
                    required_signals["RTS"] = f"{inst_name}_RTS"
                    required_signals["CTS"] = f"{inst_name}_CTS"

            temp_assignment = {}
            possible = True
            
            if "PWM" in peri_type:
                pin, func = self.find_pin_for_signal(r"TIM\d+_CH\d+", preferred_instances=target_timers)
                if pin:
                    tim_inst = func.split('_')[0]
                    meta = TIMER_METADATA.get(tim_inst, "Unknown")
                    temp_assignment[pin] = f"{func} [{meta}]"
                else: possible = False
            else:
                for role, sig_name in required_signals.items():
                    pin, func = self.find_pin_for_signal(f"^{sig_name}$", exclude_pins=temp_assignment.keys())
                    if pin: temp_assignment[pin] = func
                    else: possible = False; break
            
            if possible:
                for p, f in temp_assignment.items():
                    self.assignments[p] = {'desc': f"[Auto] {inst_name} ({f})", 'row': row_idx, 'mode': 'Auto'}
                success_groups += 1
                results.append(f"✅ {inst_name}")

        if success_groups >= count: return f"✅ OK ({success_groups}/{count})"
        else: return f"❌ Insufficient ({success_groups}/{count})"
        
    def allocate_manual(self, peri_name, pin, row_idx=0):
        pin = pin.strip()
        if pin in self.pin_map:
            if self.is_pin_free(pin):
                self.assignments[pin] = {'desc': f"[Manual] {peri_name}", 'row': row_idx, 'mode': 'Manual'}
                return "✅ Locked"
            else: 
                conflict_desc = self.assignments[pin]['desc']
                return f"❌ Conflict ({conflict_desc})"
        else: return "❌ Invalid Pin"

# ================= Google Sheet 控制器 =================
class DashboardController:
    def __init__(self):
        self.client = None; self.sheet = None
    def connect(self):
        log("🔌 連線 Google Sheet..."); json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        if not json_content: return False
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_content), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
            self.client = gspread.authorize(creds); self.sheet = self.client.open(SPREADSHEET_NAME)
            return True
        except: return False
    def setup_reference_data(self, menu_data):
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_REF)
            except: ws = self.sheet.add_worksheet(title=WORKSHEET_REF, rows="50", cols="20")
            ws.clear(); categories = sorted(menu_data.keys()); cols = []
            for cat in categories: cols.append([cat] + sorted(menu_data[cat]))
            for i, col_data in enumerate(cols):
                col_values = [[x] for x in col_data]
                range_str = gspread.utils.rowcol_to_a1(1, i+1)
                ws.update(range_name=range_str, values=col_values)
            return categories
        except: return []
    def init_config_sheet(self, categories, all_peris):
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            except:
                ws = self.sheet.add_worksheet(title=WORKSHEET_CONFIG, rows="50", cols="10")
                ws.append_row(["Category", "Peripheral", "Quantity (Groups)", "Option / Fixed Pin", "Status (Result)"])
                ws.format('A1:E1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.6}})
            rule_cat = {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": c} for c in categories]}, "showCustomUi": True}
            rule_peri = {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": p} for p in all_peris]}, "showCustomUi": True}
            reqs = [{"setDataValidation": {"range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 50, "startColumnIndex": 0, "endColumnIndex": 1}, "rule": rule_cat}},
                    {"setDataValidation": {"range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 50, "startColumnIndex": 1, "endColumnIndex": 2}, "rule": rule_peri}}]
            self.sheet.batch_update({"requests": reqs})
        except: pass
    def read_config(self):
        try: return self.sheet.worksheet(WORKSHEET_CONFIG).get_all_records()
        except: return []
    def write_status_back(self, status_list):
        try: ws = self.sheet.worksheet(WORKSHEET_CONFIG); cell_list = [[s] for s in status_list]; range_str = f"E2:E{1 + len(status_list)}"; ws.update(range_name=range_str, values=cell_list)
        except: pass
    def generate_pinout_view(self, assignments, total_pins):
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_RESULT)
            except: ws = self.sheet.add_worksheet(title=WORKSHEET_RESULT, rows="100", cols="20")
            ws.clear()
            used_count = len(assignments); free_count = total_pins - used_count
            ws.update('A1:B4', [['Resource Summary', ''], ['Total GPIO', total_pins], ['Used GPIO', used_count], ['Free GPIO', free_count]])
            ws.format('A1:B4', {'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}})
            headers = ["Pin Name", "Assigned Function", "Detail Spec", "Mode"]
            rows = [headers]
            sorted_pins = sorted(assignments.keys(), key=lambda p: (assignments[p]['row'], p))
            for pin in sorted_pins:
                data = assignments[pin]; usage = data['desc']; mode = data['mode']; spec = "-"
                if "TIM" in usage:
                    match = re.search(r'(TIM\d+)', usage)
                    if match: spec = TIMER_METADATA.get(match.group(1), "")
                rows.append([pin, usage, spec, mode])
            ws.update('A6', rows)
            ws.format('A6:D6', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
        except: pass

if __name__ == "__main__":
    log("🚀 程式啟動 (V7.1 - 修正版)...")
    parser = STM32XMLParser(XML_FILENAME); parser.parse()
    menu_data, all_peris = parser.get_organized_menu_data()
    dashboard = DashboardController()
    if not dashboard.connect(): sys.exit(1)
    log("⚙️ 初始化表單...")
    categories = dashboard.setup_reference_data(menu_data)
    dashboard.init_config_sheet(categories, all_peris)
    log("⚙️ 讀取設定..."); config_data = dashboard.read_config()
    log("⚙️ 執行規劃..."); planner = GPIOPlanner(parser.pin_map); status_results = []
    
    for row_idx, row in enumerate(config_data):
        peri = str(row.get('Peripheral', '')).strip()
        qty_str = str(row.get('Quantity (Groups)', '0'))
        option = str(row.get('Option / Fixed Pin', '')).strip()
        if not peri: status_results.append(""); continue
        try: qty = int(qty_str)
        except: qty = 0
        is_fixed_pin = re.match(r'^P[A-K]\d+$', option)
        if is_fixed_pin: result = planner.allocate_manual(peri, option, row_idx)
        else: result = planner.allocate_group(peri, qty, option, row_idx)
        status_results.append(result); log(f"   🔹 Row {row_idx+2}: {peri} (x{qty}) -> {result}")

    log("📝 寫回結果..."); dashboard.write_status_back(status_results); dashboard.generate_pinout_view(planner.assignments, len(parser.pin_map))
    log("🎉 執行成功！")
