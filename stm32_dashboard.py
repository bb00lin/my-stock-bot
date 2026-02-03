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
                    self.pin_map[pin_name].append(sig_name)
                    if sig_name.startswith("GPIO"): continue

                    raw_peri = sig_name.split('_')[0]
                    peri_type = re.sub(r'\d+', '', raw_peri)
                    if "OTG" in sig_name: peri_type = "USB_OTG"
                    self.detected_peripherals.add(peri_type)
            
            # 手動補充關鍵字
            for p in ["DDR", "FMC", "SDMMC", "QUADSPI", "ADC", "ETH"]:
                self.detected_peripherals.add(p)
            
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
                if peri in keywords: menu[cat].append(peri); assigned = True; break
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
        if preferred_instances:
            for pin, funcs in self.pin_map.items():
                if not self.is_pin_free(pin) or pin in exclude_pins: continue
                for func in funcs:
                    if re.match(signal_regex, func):
                        for pref in preferred_instances:
                            if func.startswith(pref): return pin, func
            return None, None

        for pin, funcs in self.pin_map.items():
            if not self.is_pin_free(pin) or pin in exclude_pins: continue
            for func in funcs:
                if re.match(signal_regex, func):
                    return pin, func
        return None, None

    def allocate_system_critical(self, peri_type, row_idx, option_str=""):
        """✨ V9 修正版：支援指定 SDMMC1/SDMMC2"""
        locked_count = 0
        target_prefixes = []
        opt_clean = self.normalize_option(option_str)
        
        # 1. DDR: 全鎖
        if "DDR" in peri_type: 
            target_prefixes = ["DDR_", "DDRPHYC_"]
        
        # 2. SDMMC: 判斷 Option
        elif "SDMMC" in peri_type:
            if "SDMMC2" in opt_clean: target_prefixes = ["SDMMC2"]
            elif "SDMMC3" in opt_clean: target_prefixes = ["SDMMC3"]
            else: target_prefixes = ["SDMMC1"] # 預設只鎖 SDMMC1 (eMMC)
            
        # 3. QUADSPI: 預設全鎖 (因為腳位複雜)
        elif "QUADSPI" in peri_type:
             target_prefixes = ["QUADSPI"]
             
        # 4. FMC: 預設全鎖
        elif "FMC" in peri_type:
             target_prefixes = ["FMC"]

        for pin, funcs in self.pin_map.items():
            if not self.is_pin_free(pin): continue
            for func in funcs:
                match = False
                for t in target_prefixes:
                    if func.startswith(t): match = True; break
                if match:
                    self.assignments[pin] = {'desc': f"[System] {peri_type} ({func})", 'row': row_idx, 'mode': 'Critical'}
                    locked_count += 1
                    break
        if locked_count > 0: return f"✅ Reserved {locked_count} pins"
        else: return "⚠️ No pins found/locked"

    def allocate_group(self, peri_type, count, option_str="", row_idx=0):
        if count == 0: return ""
        
        # 修正系統關鍵字進入點，傳入 option_str
        if peri_type in ["DDR", "FMC", "SDMMC", "QUADSPI"]:
            return self.allocate_system_critical(peri_type, row_idx, option_str)

        results = []
        success_groups = 0
        opt_clean = self.normalize_option(option_str)
        
        # 通用選項
        needs_rts_cts = ("RTS" in opt_clean and "CTS" in opt_clean)
        needs_nss = "NSS" in opt_clean
        force_32bit = "32BIT" in opt_clean
        force_16bit = "16BIT" in opt_clean
        
        # ETH 選項
        is_rgmii = "RGMII" in opt_clean
        is_rmii = "RMII" in opt_clean
        
        # 搜尋範圍
        search_range = range(1, 15)
        target_instances = None 
        
        if "PWM" in peri_type:
            if force_32bit: target_instances = ["TIM2", "TIM5"]
            elif force_16bit: target_instances = ["TIM1", "TIM3", "TIM4", "TIM8", "TIM12", "TIM13", "TIM14", "TIM6", "TIM7"]
            
        # ✨ V9 修正: 讓 "RGMII" 或 "ETH" 都能觸發 Ethernet 邏輯
        elif "ETH" in peri_type or "RGMII" in peri_type or "RMII" in peri_type:
            if "ETH1" in opt_clean: target_instances = ["ETH1"]
            elif "ETH2" in opt_clean: target_instances = ["ETH2"]
            else: target_instances = ["ETH1", "ETH2"]
            search_range = range(1, 3) 

        for i in search_range:
            if success_groups >= count: break
            
            # 定義 Instance 名稱
            if "PWM" in peri_type: inst_name = "PWM"
            elif "ADC" in peri_type: inst_name = "ADC"
            elif "ETH" in peri_type or "RGMII" in peri_type or "RMII" in peri_type:
                # 如果使用者在 Peripheral 填 RGMII，我們要把它正規化成 ETHx
                # 這裡假設如果填 RGMII，i=1 就是 ETH1
                inst_name = f"ETH{i}"
            else: 
                inst_name = f"{peri_type}{i}"
            
            # 檢查 Target Instance
            if target_instances and ("ETH" in peri_type or "RGMII" in peri_type):
                if inst_name not in target_instances: continue

            required_signals = {}
            
            # === 定義各種週邊的訊號需求 ===
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
            
            # ✨ V9 修正: 包含 "RGMII" 關鍵字
            elif "ETH" in peri_type or "RGMII" in peri_type or "RMII" in peri_type:
                # 優先使用 Option 判斷，如果沒有，再看 Peripheral Name
                use_rmii = is_rmii or ("RMII" in peri_type)
                use_rgmii = is_rgmii or ("RGMII" in peri_type)
                
                # 如果都沒指定，預設 RMII
                if not use_rmii and not use_rgmii: use_rmii = True
                
                if use_rmii:
                    required_signals = {
                        "REF_CLK": f"{inst_name}_RMII_REF_CLK",
                        "CRS_DV": f"{inst_name}_RMII_CRS_DV",
                        "RXD0": f"{inst_name}_RMII_RXD0",
                        "RXD1": f"{inst_name}_RMII_RXD1",
                        "TX_EN": f"{inst_name}_RMII_TX_EN",
                        "TXD0": f"{inst_name}_RMII_TXD0",
                        "TXD1": f"{inst_name}_RMII_TXD1",
                        "MDC": f"{inst_name}_MDC",   
                        "MDIO": f"{inst_name}_MDIO"
                    }
                elif use_rgmii:
                    required_signals = {
                        "GTX_CLK": f"{inst_name}_RGMII_GTX_CLK",
                        "RX_CLK": f"{inst_name}_RGMII_RX_CLK",
                        "RX_CTL": f"{inst_name}_RGMII_RX_CTL",
                        "RXD0": f"{inst_name}_RGMII_RXD0",
                        "RXD1": f"{inst_name}_RGMII_RXD1",
                        "RXD2": f"{inst_name}_RGMII_RXD2",
                        "RXD3": f"{inst_name}_RGMII_RXD3",
                        "TX_CTL": f"{inst_name}_RGMII_TX_CTL",
                        "TXD0": f"{inst_name}_RGMII_TXD0",
                        "TXD1": f"{inst_name}_RGMII_TXD1",
                        "TXD2": f"{inst_name}_RGMII_TXD2",
                        "TXD3": f"{inst_name}_RGMII_TXD3",
                        "MDC": f"{inst_name}_MDC",
                        "MDIO": f"{inst_name}_MDIO"
                    }

            # === 分配執行 ===
            temp_assignment = {}
            possible = True
            
            if "PWM" in peri_type:
                pin, func = self.find_pin_for_signal(r"TIM\d+_CH\d+", preferred_instances=target_instances)
                if pin:
                    tim_inst = func.split('_')[0]
                    meta = TIMER_METADATA.get(tim_inst, "Unknown")
                    temp_assignment[pin] = f"{func} [{meta}]"
                else: possible = False
            elif "ADC" in peri_type:
                pin, func = self.find_pin_for_signal(r"ADC\d+_IN(P)?\d+")
                if pin: temp_assignment[pin] = func
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
            
            if ("PWM" in peri_type or "ADC" in peri_type) and possible:
                pass 

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
            used_count = len(assignments); free_count = total_pins - used_count
            ws.update(values=[['Resource Summary', ''], ['Total GPIO', total_pins], ['Used GPIO', used_count], ['Free GPIO', free_count]], range_name='A1:B4')
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
            ws.update(values=rows, range_name='A6')
            ws.format('A6:D6', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
        except: pass

if __name__ == "__main__":
    log("🚀 程式啟動 (V9 - ETH & SDMMC Fix)...")
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
