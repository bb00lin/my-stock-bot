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
                        occupier = self.assignments[pin]['desc']
                        if "]" in occupier: occupier = occupier.split(']')[1].strip().split('(')[0]
                        return f"{occupier} on {pin}"
        return "HW Limitation"

    def allocate_system_critical(self, peri_type, row_idx, option_str=""):
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
                    self.assignments[pin] = {'desc': f"[System] {peri_type} ({func})", 'row': row_idx, 'mode': 'Critical'}
                    locked_count += 1
                    break
        if locked_count > 0: return f"✅ Reserved {locked_count} pins"
        else: return "⚠️ No pins found/locked"

    def allocate_group(self, peri_type, count, option_str="", row_idx=0):
        if count == 0: return ""
        if peri_type in ["DDR", "FMC", "SDMMC", "QUADSPI"]:
            return self.allocate_system_critical(peri_type, row_idx, option_str)

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
                    self.assignments[p] = {'desc': f"[Auto] {inst_name} ({f})", 'row': row_idx, 'mode': 'Auto'}
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
        
    def allocate_manual(self, peri_name, pin, row_idx=0):
        pin = pin.strip().upper() 
        if pin in self.pin_map:
            if self.is_pin_free(pin):
                self.assignments[pin] = {'desc': f"[Manual] {peri_name}", 'row': row_idx, 'mode': 'Manual'}
                return "✅ Locked"
            else: 
                conflict_desc = self.assignments[pin]['desc']
                return f"❌ Conflict ({conflict_
