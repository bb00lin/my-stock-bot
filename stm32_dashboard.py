import os
import sys
import json
import re
import csv
import io
import gspread
import hashlib
import xml.etree.ElementTree as ET
from oauth2client.service_account import ServiceAccountCredentials
from collections import defaultdict
from datetime import datetime

# ================= 設定區 =================
XML_FILENAME = "STM32MP133CAFx.xml"
SPREADSHEET_NAME = 'STM32_GPIO_Planner'
WORKSHEET_CONFIG = 'Config_Panel'
WORKSHEET_RESULT = 'Pinout_View'
WORKSHEET_GPIO = 'GPIO' 

TIMER_METADATA = {
    "TIM1": "16-bit, Advanced", "TIM8": "16-bit, Advanced",
    "TIM2": "32-bit, General",  "TIM5": "32-bit, General",
    "TIM3": "16-bit, General",  "TIM4": "16-bit, General",
    "TIM12": "16-bit, General", "TIM13": "16-bit, General", "TIM14": "16-bit, General",
    "TIM6": "16-bit, Basic",    "TIM7": "16-bit, Basic"
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ================= 配色引擎 =================
class ColorEngine:
    def __init__(self):
        self.family_palette = {
            "SPI":    {"red": 1.0, "green": 0.85, "blue": 0.9},
            "ETH":    {"red": 0.8, "green": 1.0, "blue": 1.0},
            "SDMMC":  {"red": 0.8, "green": 0.9, "blue": 1.0},
            "I2C":    {"red": 0.8, "green": 1.0, "blue": 0.8},
            "UART":   {"red": 1.0, "green": 0.95, "blue": 0.8},
            "USART":  {"red": 1.0, "green": 0.95, "blue": 0.8},
            "TIM":    {"red": 0.95, "green": 0.95, "blue": 0.95},
            "ADC":    {"red": 1.0, "green": 0.9, "blue": 0.8},
            "FDCAN":  {"red": 1.0, "green": 0.8, "blue": 1.0},
            "USB":    {"red": 0.8, "green": 0.8, "blue": 1.0},
        }
        self.special_palette = {
            "Reserved": {"red": 0.9, "green": 0.9, "blue": 0.9},
            "System":   {"red": 1.0, "green": 0.8, "blue": 0.8},
        }

    def get_color(self, func_name):
        func_name = str(func_name).strip().upper()
        if "RESERVED" in func_name: return self.special_palette["Reserved"]
        if "SYSTEM" in func_name: return self.special_palette["System"]
        
        match = re.match(r'^([A-Z]+)(\d+)?', func_name)
        if match:
            peri_type = match.group(1)
            instance = match.group(2)
            if peri_type in self.family_palette:
                base_color = self.family_palette[peri_type]
                if instance: return self._hash_tweak(base_color, int(instance))
                else: return base_color
        return {"red": 1.0, "green": 1.0, "blue": 1.0}

    def _hash_tweak(self, base, seed):
        import math
        shift_r = math.sin(seed) * 0.15
        shift_g = math.cos(seed) * 0.15
        shift_b = math.sin(seed * 2) * 0.15
        return {
            "red":   max(0.6, min(1.0, base["red"] + shift_r)),
            "green": max(0.6, min(1.0, base["green"] + shift_g)),
            "blue":  max(0.6, min(1.0, base["blue"] + shift_b))
        }

# ================= XML 解析器 =================
class STM32XMLParser:
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.pin_map = defaultdict(list)
        self.detected_peripherals = set()
        
    def parse(self):
        log(f"📖 讀取 XML (完整功能庫): {self.xml_path}")
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

# ================= Google Sheet 控制器 =================
class DashboardController:
    def __init__(self):
        self.client = None; self.sheet = None
        self.color_engine = ColorEngine()
        self.gpio_af_data = {}

    def connect(self):
        log("🔌 連線 Google Sheet..."); json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        if not json_content: return False
        try:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_content), ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
            self.client = gspread.authorize(creds); self.sheet = self.client.open(SPREADSHEET_NAME)
            return True
        except: return False
    
    def load_gpio_af_data(self):
        try:
            ws = self.sheet.worksheet(WORKSHEET_GPIO)
            rows = ws.get_all_values()
            for row in rows[1:]:
                if len(row) < 1: continue
                pin_name = row[0].strip().upper()
                while len(row) < 20: row.append("")
                self.gpio_af_data[pin_name] = row[4:20] 
        except: pass

    def sync_to_gpio(self, assignments):
        log("🔄 同步資料至 'GPIO' 工作表...")
        try:
            ws = self.sheet.worksheet(WORKSHEET_GPIO)
            rows = ws.get_all_values()
            if not rows: return

            pin_row_map = {}
            for idx, row in enumerate(rows):
                if idx == 0: continue 
                if row and row[0]: 
                    pin_row_map[row[0].strip().upper()] = idx

            updates = []
            for pin, row_idx in pin_row_map.items():
                gateway_val = ""
                remark_val = ""
                if pin in assignments:
                    data = assignments[pin]
                    if isinstance(data, dict):
                        raw_func = data.get('desc', '')
                        if "]" in raw_func: content = raw_func.split(']')[1].strip()
                        else: content = raw_func
                        
                        if "(" in content and ")" in content:
                            start_index = content.find('(')
                            if start_index != -1: gateway_val = content[start_index:].strip()
                            else: gateway_val = content
                        else: gateway_val = content
                        
                        remark_val = data.get('note', '')
                    else:
                        gateway_val = str(data)
                        remark_val = ""

                sheet_row = row_idx + 1
                updates.append({'range': f'C{sheet_row}', 'values': [[gateway_val]]})
                updates.append({'range': f'D{sheet_row}', 'values': [[remark_val]]})

            if updates:
                try: ws.batch_update(updates, value_input_option='RAW')
                except TypeError: ws.batch_update(updates)
                log("✅ GPIO 表格同步完成！")
        except Exception as e: log(f"❌ 同步失敗: {e}")

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
                ws.append_row(["Category", "Peripheral", "Quantity (Groups)", "Option / Fixed Pin", "Status (Result)", "Pin Define"])
                ws.format('A1:F1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 1.0, 'green': 0.9, 'blue': 0.6}})
            rule_cat = {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": c} for c in categories]}, "showCustomUi": True}
            rule_peri = {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": p} for p in all_peris]}, "showCustomUi": True}
            reqs = [{"setDataValidation": {"range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 50, "startColumnIndex": 0, "endColumnIndex": 1}, "rule": rule_cat}},
                    {"setDataValidation": {"range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 50, "startColumnIndex": 1, "endColumnIndex": 2}, "rule": rule_peri}}]
            self.sheet.batch_update({"requests": reqs})
        except: pass
    def read_config(self):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            raw_rows = ws.get_all_values()
            if len(raw_rows) < 1: return []
            headers = raw_rows[0]
            col_map = {}
            for idx, text in enumerate(headers):
                text = str(text).strip()
                if 'Peripheral' in text: col_map['peri'] = idx
                elif 'Quantity' in text: col_map['qty'] = idx
                elif 'Option' in text or 'Fixed' in text: col_map['opt'] = idx
                elif 'Define' in text: col_map['def'] = idx
            if 'peri' not in col_map or 'opt' not in col_map: return []
            data_list = []
            for row in raw_rows[1:]:
                while len(row) < len(headers): row.append("")
                item = {
                    'Peripheral': row[col_map['peri']],
                    'Quantity (Groups)': row[col_map.get('qty', -1)] if 'qty' in col_map else "0",
                    'Option / Fixed Pin': row[col_map['opt']],
                    'Pin Define': row[col_map['def']] if 'def' in col_map else ""
                }
                data_list.append(item)
            log(f"🔎 成功解析 {len(data_list)} 筆設定資料")
            return data_list
        except Exception as e: log(f"❌ 讀取失敗: {e}"); return []
    def write_status_back(self, status_list):
        try:
            ws = self.sheet.worksheet(WORKSHEET_CONFIG)
            cell_list = [[s] for s in status_list]
            range_str = f"E2:E{1 + len(status_list)}"
            ws.update(range_name=range_str, values=cell_list)
        except: pass
    
    def generate_pinout_view(self, planner, dashboard):
        try:
            try: ws = self.sheet.worksheet(WORKSHEET_RESULT)
            except: ws = self.sheet.add_worksheet(title=WORKSHEET_RESULT, rows="200", cols="30")
            
            # ✨ V29: 在清除之前，先讀取並備份 Remark 欄位 (F欄)
            preserved_remarks = {}
            try:
                existing_data = ws.get_all_values()
                if len(existing_data) > 0:
                    headers = existing_data[0]
                    # 尋找 Remark 欄位 (假設標題叫 "Remark")
                    if "Remark" in headers:
                        rem_col_idx = headers.index("Remark")
                        name_col_idx = 0 # 假設 A 欄是 Name
                        for row in existing_data[1:]:
                            if len(row) > rem_col_idx and row[name_col_idx]:
                                pin_name = row[name_col_idx].strip().upper()
                                remark_val = row[rem_col_idx]
                                if remark_val:
                                    preserved_remarks[pin_name] = remark_val
            except Exception as e:
                log(f"⚠️ 讀取舊 Remark 失敗 (可能為首次執行): {e}")

            ws.clear()
            
            assignments = planner.assignments; used_count = len(assignments); free_count = len(planner.pin_map) - used_count
            ws.update(values=[['Resource Summary', ''], ['Total GPIO', len(planner.pin_map)], ['Used GPIO', used_count], ['Free GPIO', free_count]], range_name='A1:B4')
            ws.format('A1:B4', {'backgroundColor': {'red': 0.9, 'green': 0.9, 'blue': 0.9}})
            
            # ✨ V29: 插入 Remark 到欄位 5 (F欄)
            # A=0, B=1, C=2, D=3, E=4, F=5, G=6...
            af_headers = [f"AF{i}" for i in range(16)]
            headers = ["Pin Name", "Assigned Function", "Detail Spec", "Mode", "Pin Define", "Remark"] + af_headers
            rows = [headers]
            
            format_requests = []
            sheet_id = ws.id
            start_row_idx = 6 
            
            # ✨ V29: 先清除所有背景顏色 (全白)
            # 清除 A6 到 V500 的背景色
            format_requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 5, # Row 6 (Header)
                        "endRowIndex": 500,
                        "startColumnIndex": 0,
                        "endColumnIndex": 22 # A to V
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}}},
                    "fields": "userEnteredFormat.backgroundColor"
                }
            })
            
            sorted_pins = sorted(assignments.keys(), key=lambda p: (assignments[p].get('row', 999) if isinstance(assignments[p], dict) else 999, p))
            
            for i, pin in enumerate(sorted_pins):
                raw_data = assignments[pin]
                if isinstance(raw_data, dict):
                    usage = raw_data.get('desc', '')
                    mode = raw_data.get('mode', '')
                    note = raw_data.get('note', '')
                else:
                    usage = str(raw_data)
                    mode = "Unknown"
                    note = ""
                    
                spec = "-"
                if "TIM" in usage:
                    match = re.search(r'(TIM\d+)', usage)
                    if match: spec = TIMER_METADATA.get(match.group(1), "")
                
                af_data = dashboard.gpio_af_data.get(pin, [""] * 16)
                
                # ✨ V29: 填回保留的 Remark
                user_remark = preserved_remarks.get(pin, "")
                
                rows.append([pin, usage, spec, mode, note, user_remark] + af_data)
                
                func_key = usage
                if "]" in usage: func_key = usage.split(']')[1].strip().split('(')[0].strip()
                bg_color = self.color_engine.get_color(func_key)
                
                format_requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": start_row_idx + i,
                            "endRowIndex": start_row_idx + i + 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 22
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": bg_color}},
                        "fields": "userEnteredFormat.backgroundColor"
                    }
                })

            if planner.failed_reports:
                rows.append(["--- FAILED / MISSING ---", "", "", "", "", ""] + [""]*16)
                
                sep_row = start_row_idx + len(sorted_pins)
                format_requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": sep_row,
                            "endRowIndex": sep_row + 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 22
                        },
                        "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.8}}},
                        "fields": "userEnteredFormat.backgroundColor"
                    }
                })
                
                fail_start_row = sep_row + 1
                for i, report in enumerate(planner.failed_reports):
                    rows.append([report['pin'], report['desc'], "-", report['mode'], "", ""] + [""]*16)
                    
                    sig_name = report['desc']
                    func_key = sig_name.split('_')[0] if '_' in sig_name else sig_name
                    bg_color = self.color_engine.get_color(func_key)
                    
                    format_requests.append({
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet_id,
                                "startRowIndex": fail_start_row + i,
                                "endRowIndex": fail_start_row + i + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": 22
                            },
                            "cell": {"userEnteredFormat": {"backgroundColor": bg_color}},
                            "fields": "userEnteredFormat.backgroundColor"
                        }
                    })
            
            ws.update(values=rows, range_name='A6')
            ws.format('A6:V6', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.7, 'green': 0.85, 'blue': 1.0}})
            if format_requests: self.sheet.batch_update({"requests": format_requests})
                
        except Exception as e: log(f"❌ 寫入結果失敗: {e}")

# ================= 規劃核心 =================
class GPIOPlanner:
    def __init__(self, pin_map):
        self.pin_map = pin_map
        self.assignments = {}
        self.failed_reports = [] 

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
    
    def diagnose_conflict(self, signal_regex):
        for pin, funcs in self.pin_map.items():
            for func in funcs:
                if re.match(signal_regex, func):
                    if pin in self.assignments:
                        data = self.assignments[pin]
                        if isinstance(data, dict): occupier = data.get('desc', 'Unknown')
                        else: occupier = str(data)
                        if "]" in occupier: occupier = occupier.split(']')[1].strip().split('(')[0]
                        return f"{occupier} on {pin}"
        return "HW Limitation"

    def allocate_system_critical(self, peri_type, row_idx, option_str="", pin_define=""):
        locked_count = 0
        target_prefixes = []
        opt_clean = self.normalize_option(option_str)
        is_4bit = "4BIT" in opt_clean
        is_1bit = "1BIT" in opt_clean
        
        if "DDR" in peri_type: target_prefixes = ["DDR_", "DDRPHYC_"]
        elif "SDMMC" in peri_type:
            instance_prefix = "SDMMC1"
            if "SDMMC2" in opt_clean: instance_prefix = "SDMMC2"
            elif "SDMMC3" in opt_clean: instance_prefix = "SDMMC3"
            target_prefixes = [instance_prefix]
        elif "QUADSPI" in peri_type: target_prefixes = ["QUADSPI"]
        elif "FMC" in peri_type: target_prefixes = ["FMC"]

        for pin, funcs in self.pin_map.items():
            if not self.is_pin_free(pin): continue
            for func in funcs:
                match = False
                for t in target_prefixes:
                    if func.startswith(t): 
                        if "SDMMC" in peri_type:
                            if is_1bit:
                                if any(x in func for x in ["_D1", "_D2", "_D3", "_D4", "_D5", "_D6", "_D7"]): continue 
                            elif is_4bit:
                                if any(x in func for x in ["_D4", "_D5", "_D6", "_D7"]): continue 
                        match = True; break
                if match:
                    self.assignments[pin] = {'desc': f"[System] {peri_type} ({func})", 'row': row_idx, 'mode': 'Critical', 'note': pin_define}
                    locked_count += 1
                    break
        if locked_count > 0: return f"✅ Reserved {locked_count} pins"
        else: return "⚠️ No pins found/locked"

    def allocate_group(self, peri_type, count, option_str="", row_idx=0, pin_define=""):
        if count == 0: return ""
        if peri_type in ["DDR", "FMC", "SDMMC", "QUADSPI"]:
            return self.allocate_system_critical(peri_type, row_idx, option_str, pin_define)

        results = []
        failure_reasons = [] 
        success_groups = 0
        opt_clean = self.normalize_option(option_str)
        
        needs_rts_cts = ("RTS" in opt_clean and "CTS" in opt_clean)
        needs_nss = "NSS" in opt_clean
        force_32bit = "32BIT" in opt_clean
        force_16bit = "16BIT" in opt_clean
        is_rgmii = "RGMII" in opt_clean
        is_rmii = "RMII" in opt_clean
        
        search_range = range(1, 15)
        target_instances = None 
        
        if "PWM" in peri_type:
            if force_32bit: target_instances = ["TIM2", "TIM5"]
            elif force_16bit: target_instances = ["TIM1", "TIM3", "TIM4", "TIM8", "TIM12", "TIM13", "TIM14", "TIM6", "TIM7"]
        elif "ETH" in peri_type or "RGMII" in peri_type or "RMII" in peri_type:
            if "ETH1" in opt_clean: target_instances = ["ETH1"]
            elif "ETH2" in opt_clean: target_instances = ["ETH2"]
            else: target_instances = ["ETH1", "ETH2"]
            search_range = range(1, 3) 

        for i in search_range:
            if success_groups >= count: break
            
            if "PWM" in peri_type: inst_name = "PWM"
            elif "ADC" in peri_type: inst_name = "ADC"
            elif "ETH" in peri_type or "RGMII" in peri_type or "RMII" in peri_type: inst_name = f"ETH{i}"
            else: inst_name = f"{peri_type}{i}"
            
            if target_instances and ("ETH" in peri_type or "RGMII" in peri_type):
                if inst_name not in target_instances: continue

            required_signals = {}
            if "I2C" in peri_type: required_signals = {"SCL": f"{inst_name}_SCL", "SDA": f"{inst_name}_SDA"}
            elif "SPI" in peri_type:
                required_signals = {"SCK": f"{inst_name}_SCK", "MISO": f"{inst_name}_MISO", "MOSI": f"{inst_name}_MOSI"}
                if needs_nss: required_signals["NSS"] = f"{inst_name}_NSS"
            elif "UART" in peri_type or "USART" in peri_type:
                required_signals = {"TX": f"{inst_name}_TX", "RX": f"{inst_name}_RX"}
                if needs_rts_cts: required_signals["RTS"] = f"{inst_name}_RTS"; required_signals["CTS"] = f"{inst_name}_CTS"
            elif "ETH" in peri_type or "RGMII" in peri_type or "RMII" in peri_type:
                use_rmii = is_rmii or ("RMII" in peri_type)
                use_rgmii = is_rgmii or ("RGMII" in peri_type)
                if not use_rmii and not use_rgmii: use_rmii = True
                
                if use_rmii:
                    required_signals = {"REF_CLK": f"{inst_name}_RMII_REF_CLK", "CRS_DV": f"{inst_name}_RMII_CRS_DV", "RXD0": f"{inst_name}_RMII_RXD0", "RXD1": f"{inst_name}_RMII_RXD1", "TX_EN": f"{inst_name}_RMII_TX_EN", "TXD0": f"{inst_name}_RMII_TXD0", "TXD1": f"{inst_name}_RMII_TXD1", "MDC": f"{inst_name}_MDC", "MDIO": f"{inst_name}_MDIO"}
                elif use_rgmii:
                    required_signals = {"GTX_CLK": f"{inst_name}_RGMII_GTX_CLK", "RX_CLK": f"{inst_name}_RGMII_RX_CLK", "RX_CTL": f"{inst_name}_RGMII_RX_CTL", "RXD0": f"{inst_name}_RGMII_RXD0", "RXD1": f"{inst_name}_RGMII_RXD1", "RXD2": f"{inst_name}_RGMII_RXD2", "RXD3": f"{inst_name}_RGMII_RXD3", "TX_CTL": f"{inst_name}_RGMII_TX_CTL", "TXD0": f"{inst_name}_RGMII_TXD0", "TXD1": f"{inst_name}_RGMII_TXD1", "TXD2": f"{inst_name}_RGMII_TXD2", "TXD3": f"{inst_name}_RGMII_TXD3", "MDC": f"{inst_name}_MDC", "MDIO": f"{inst_name}_MDIO"}

            temp_assignment = {}
            possible = True
            missing_signal_reason = "" 
            
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
                    else: possible = False; culprit = self.diagnose_conflict(f"^{sig_name}$"); missing_signal_reason = f"Missing {sig_name} (Blocked by: {culprit})"; break
            
            if possible:
                for p, f in temp_assignment.items():
                    self.assignments[p] = {'desc': f"[Auto] {inst_name} ({f})", 'row': row_idx, 'mode': 'Auto', 'note': pin_define}
                success_groups += 1
                results.append(f"✅ {inst_name}")
            else:
                if "PWM" not in peri_type and "ADC" not in peri_type:
                    report_entry = []
                    for role, sig_name in required_signals.items():
                        pin, func = self.find_pin_for_signal(f"^{sig_name}$", exclude_pins=temp_assignment.keys())
                        if pin:
                             report_entry.append({'pin': pin, 'desc': f"{sig_name} (Proposed)", 'row': row_idx, 'mode': 'Auto (Proposed)'})
                        else:
                            culprit = self.diagnose_conflict(f"^{sig_name}$")
                            report_entry.append({'pin': "MISSING", 'desc': f"{sig_name}", 'row': row_idx, 'mode': f"❌ Blocked by {culprit}"})
                    self.failed_reports.extend(report_entry)
                if missing_signal_reason: failure_reasons.append(missing_signal_reason)
            
            if ("PWM" in peri_type or "ADC" in peri_type) and possible: pass 

        if success_groups >= count: return f"✅ OK ({success_groups}/{count})"
        else:
            reason_str = ""
            if failure_reasons: reason_str = f"\n❌ {failure_reasons[0]}"
            return f"❌ Insufficient ({success_groups}/{count}){reason_str}"
        
    def allocate_manual(self, peri_name, pin, row_idx=0, pin_define=""):
        pin = pin.strip().upper() 
        if pin in self.pin_map:
            if self.is_pin_free(pin):
                self.assignments[pin] = {'desc': f"[Manual] {peri_name}", 'row': row_idx, 'mode': 'Manual', 'note': pin_define}
                return "✅ Locked"
            else: 
                conflict_desc = self.assignments[pin]['desc']
                if isinstance(conflict_desc, dict): conflict_desc = str(conflict_desc) 
                return f"❌ Conflict ({conflict_desc})"
        else: return "❌ Invalid Pin"

if __name__ == "__main__":
    log("🚀 程式啟動 (V29 - Remark Preservation & Color Reset)...")
    dashboard = DashboardController()
    if not dashboard.connect(): sys.exit(1)
    
    # 1. XML Parser 負責讀取完整功能
    xml_parser = STM32XMLParser(XML_FILENAME)
    xml_parser.parse()
    
    # 2. Dashboard 負責讀取 AF 表格供顯示
    dashboard.load_gpio_af_data()
    
    menu_data, all_peris = xml_parser.get_organized_menu_data()
    
    log("⚙️ 初始化表單...")
    categories = dashboard.setup_reference_data(menu_data)
    dashboard.init_config_sheet(categories, all_peris)
    log("⚙️ 讀取設定..."); config_data = dashboard.read_config()
    log("⚙️ 執行規劃..."); planner = GPIOPlanner(xml_parser.pin_map); status_results = []
    
    for row_idx, row in enumerate(config_data):
        peri = str(row.get('Peripheral', '')).strip()
        qty_str = str(row.get('Quantity (Groups)', '0'))
        option = str(row.get('Option / Fixed Pin', '')).strip().upper()
        pin_define = str(row.get('Pin Define', '')).strip()
        
        if option in xml_parser.pin_map:
            if not peri: peri = "Reserved" 
            result = planner.allocate_manual(peri, option, row_idx, pin_define)
            status_results.append(result)
            log(f"   🔹 Row {row_idx+2}: 腳位鎖定 {option} -> {result}")
            continue

        if not peri:
            if "RGMII" in option or "ETH" in option: peri = "ETH"
            elif "SDMMC" in option: peri = "SDMMC"
            elif "SPI" in option: peri = "SPI"
            else:
                status_results.append("")
                continue

        try: qty = int(qty_str)
        except: qty = 0
        
        result = planner.allocate_group(peri, qty, option, row_idx, pin_define)
        status_results.append(result); log(f"   🔹 Row {row_idx+2}: {peri} (x{qty}) -> {result}")

    log("📝 寫回結果 (Pinout View)..."); dashboard.write_status_back(status_results); dashboard.generate_pinout_view(planner, dashboard)
    
    dashboard.sync_to_gpio(planner.assignments)
    
    log("🎉 執行成功！")
