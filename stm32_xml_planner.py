import os
import json
import gspread
import smtplib
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from oauth2client.service_account import ServiceAccountCredentials
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ================= 設定區 =================
XML_FILENAME = "STM32MP133CAFx.xml"  # 您的 XML 檔名
SPREADSHEET_NAME = 'STM32_GPIO_Planner' # Google Sheet 名稱
WORKSHEET_RESULT = '規劃結果'
MAIL_RECEIVERS = ['bb00lin@gmail.com']

# 請將您的 Google JSON 金鑰內容貼在這裡，或設定環境變數 GOOGLE_SHEETS_JSON
# 若在本地執行，建議直接指定 JSON 檔案路徑
GOOGLE_CREDENTIALS_FILE = "e-caldron-484313-m4-001936cf040b.json" 

# ================= 類別定義 =================

class STM32XMLParser:
    """負責解析本地 STM32 XML 定義檔"""
    def __init__(self, xml_path):
        self.xml_path = xml_path
        self.pin_map = defaultdict(list) # { 'PA0': ['TIM2_CH1', 'UART4_TX'], ... }

    def parse(self):
        print(f"📖 正在讀取 XML: {self.xml_path} ...")
        try:
            tree = ET.parse(self.xml_path)
            root = tree.getroot()
            
            # STM32 XML 通常有 Namespace，我們需處理
            ns = {'ns': 'http://mcd.rou.st.com/modules.php?name=mcu'}
            
            pins = root.findall("ns:Pin", ns)
            print(f"⚙️ 發現 {len(pins)} 個腳位定義，正在解析訊號...")
            
            for pin in pins:
                pin_name = pin.attrib.get('Name')
                # 過濾掉電源腳位 (VSS, VDD)
                if pin_name.startswith("V") and len(pin_name) < 4: 
                    continue
                
                # 抓取 Signal
                signals = pin.findall('ns:Signal', ns)
                for sig in signals:
                    sig_name = sig.attrib.get('Name')
                    # 過濾掉 GPIO 標記
                    if sig_name == "GPIO" or sig_name.startswith("GPIO_"): 
                        continue
                    self.pin_map[pin_name].append(sig_name)
            
            # 排序
            for p in self.pin_map:
                self.pin_map[p].sort()
                
            print(f"✅ 解析完成！有效 I/O 腳位數: {len(self.pin_map)}")
            
        except Exception as e:
            print(f"❌ XML 解析失敗: {e}")

class GPIOPlanner:
    def __init__(self, pin_map):
        self.pin_map = pin_map
        self.assignments = {} 
        self.logs = []

    def log(self, msg):
        print(msg)
        self.logs.append(msg)

    def manual_lock(self, pin, usage):
        """手動鎖定固定腳位"""
        if pin not in self.pin_map:
            self.log(f"⚠️ [警告] 腳位 {pin} 不存在於 XML 中，但強制鎖定。")
        
        if pin in self.assignments:
            self.log(f"❌ [衝突] 腳位 {pin} 已被分配給 '{self.assignments[pin]}'")
            return

        self.assignments[pin] = f"[固定] {usage}"
        self.log(f"🔒 鎖定: {pin} -> {usage}")

    def auto_allocate(self, function_type, count, specific_regex=None):
        """自動分配功能"""
        self.log(f"\n🔍 尋找 {count} 組 {function_type} ...")
        found_count = 0
        
        search_key = function_type
        if function_type == "PWM": search_key = "TIM"

        for pin, funcs in self.pin_map.items():
            if found_count >= count: break
            if pin in self.assignments: continue

            for func in funcs:
                # 若有指定 Regex (例如特定 Timer)，需符合
                if specific_regex and specific_regex not in func:
                    continue
                
                if search_key in func:
                    # 針對 PWM 需更嚴謹 (必須是 CHx)
                    if function_type == "PWM" and "_CH" not in func:
                        continue
                        
                    self.assignments[pin] = f"[自動] {function_type} ({func})"
                    self.log(f"   ✅ 分配: {pin} -> {func}")
                    found_count += 1
                    break
        
        if found_count < count:
            self.log(f"❌ [不足] 請求 {count} 組，僅找到 {found_count} 組。")

class ReportGenerator:
    def __init__(self, creds_file):
        self.creds_file = creds_file
        self.client = None
    
    def connect(self):
        # 優先讀取環境變數 (GitHub Actions 用)，其次讀取本地檔案
        json_content = os.environ.get('GOOGLE_SHEETS_JSON')
        
        try:
            scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
            
            if json_content:
                creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_content), scope)
            elif os.path.exists(self.creds_file):
                creds = ServiceAccountCredentials.from_json_keyfile_name(self.creds_file, scope)
            else:
                print("⚠️ 找不到 Google 憑證 (JSON 或 Env)，跳過 Sheet 更新。")
                return False
                
            self.client = gspread.authorize(creds)
            return True
        except Exception as e:
            print(f"❌ Google Sheet 連線失敗: {e}")
            return False

    def update_sheet(self, assignments):
        if not self.client: return
        try:
            # 開啟 Sheet (若不存在 Worksheet 則建立)
            sheet = self.client.open(SPREADSHEET_NAME)
            try:
                ws = sheet.worksheet(WORKSHEET_RESULT)
            except:
                ws = sheet.add_worksheet(title=WORKSHEET_RESULT, rows="100", cols="20")
            
            ws.clear()
            
            headers = ["Pin Name", "Function / Usage", "Type", "Last Updated"]
            rows = [headers]
            update_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

            # 將結果轉為 List
            for pin, usage in sorted(assignments.items()):
                alloc_type = "Manual" if "[固定]" in usage else "Auto"
                rows.append([pin, usage, alloc_type, update_time])

            ws.update(rows)
            ws.format('A1:D1', {'textFormat': {'bold': True}, 'backgroundColor': {'red': 0.8, 'green': 0.8, 'blue': 0.8}})
            print("📊 Google Sheet 更新完成！")
            
        except Exception as e:
            print(f"❌ 寫入 Sheet 失敗: {e}")

    def send_email_report(self, logs):
        mail_user = os.environ.get('MAIL_USERNAME')
        mail_pass = os.environ.get('MAIL_PASSWORD')
        
        if not mail_user or not mail_pass:
            print("⚠️ 未設定 Email 帳密 (MAIL_USERNAME/PASSWORD)，跳過寄信")
            return

        msg = MIMEMultipart()
        msg['From'] = mail_user
        msg['To'] = ", ".join(MAIL_RECEIVERS)
        msg['Subject'] = f"STM32 XML 規劃報告 - {datetime.now().strftime('%m/%d %H:%M')}"
        
        log_html = "<br>".join(logs)
        body = f"""
        <html><body>
            <h2>STM32MP133C GPIO 規劃結果</h2>
            <p><b>資料來源:</b> 本地 XML ({XML_FILENAME})</p>
            <hr>
            <div style="font-family: monospace; background-color: #f4f4f4; padding: 10px;">
                {log_html}
            </div>
        </body></html>
        """
        msg.attach(MIMEText(body, 'html'))

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(mail_user, mail_pass)
            server.send_message(msg)
            server.quit()
            print("📧 Email 通知已發送")
        except Exception as e:
            print(f"❌ Email 發送失敗: {e}")

# ================= 主程式執行 =================
if __name__ == "__main__":
    # 1. 解析 XML
    parser = STM32XMLParser(XML_FILENAME)
    parser.parse()

    # 2. 建立規劃器
    planner = GPIOPlanner(parser.pin_map)

    # --- [用戶設定區] ---
    print("\n🚀 開始規劃 GPIO...")
    
    # A. 鎖定 AO 關鍵腳位 (TIM2 + TIM5 組合)
    planner.manual_lock('PA5', 'AO_CH1 (TIM2_CH1)')
    planner.manual_lock('PB10', 'AO_CH2 (TIM2_CH3)')
    planner.manual_lock('PA3', 'AO_CH3 (TIM2_CH4)')
    planner.manual_lock('PH10', 'AO_CH4 (TIM5_CH1)')
    
    # B. 鎖定其他固定腳位
    planner.manual_lock('PE10', 'System_LED')
    
    # C. 自動分配 (範例需求)
    planner.auto_allocate('I2C', 2)     # I2C x2
    planner.auto_allocate('SPI', 1)     # SPI x1
    planner.auto_allocate('ADC', 1)     # ADC x1
    planner.auto_allocate('PWM', 4)     # 額外的 PWM
    
    # --------------------

    # 3. 執行報表與通知
    reporter = ReportGenerator(GOOGLE_CREDENTIALS_FILE)
    if reporter.connect():
        reporter.update_sheet(planner.assignments)
    
    reporter.send_email_report(planner.logs)
    
    print("\n🎉 程式執行完畢。")
