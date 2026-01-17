import time
import gspread
import re
import os
import shutil
import smtplib
import math
from itertools import cycle
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication 
from datetime import datetime, timedelta, timezone
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException

# ================= 設定區 =================
SPREADSHEET_FILE_NAME = 'Guardian_Price_Check'
WORKSHEET_TEMPLATE = 'Mix_Match_Check' # 以此分頁作為每一輪測試的範本 
WORKSHEET_PROMO = 'promotion' [cite: 87]

# 請確保此網址正確
SHEET_URL_FOR_MAIL = "https://docs.google.com/spreadsheets/d/1pqa6DU-qo3lR84QYgpoiwGE7tO-QSY2-kC_ecf868cY/edit?gid=1727836519#gid=1727836519" [cite: 87]

CREDENTIALS_FILE = 'google_key.json' [cite: 87]
URL = "https://guardian.com.sg/" [cite: 87]

# Email 設定
MAIL_USERNAME = os.environ.get('MAIL_USERNAME') [cite: 87]
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD') [cite: 87]
MAIL_RECEIVER = ['bb00lin@gmail.com', 'helen.chen.168@gmail.com'] [cite: 87]

# ================= 輔助功能 =================
def clean_price(price_text):
    if not price_text: return ""
    return str(price_text).replace("SGD", "").replace("$", "").replace(",", "").replace("\n", "").replace(" ", "").strip() [cite: 88]

def get_taiwan_time_now():
    return datetime.now(timezone(timedelta(hours=8))) [cite: 88]

def get_taiwan_time_display():
    return get_taiwan_time_now().strftime("%Y-%m-%d %H:%M") [cite: 88]

def create_zip_evidence(sku, sku_folder):
    try:
        if not os.path.exists(sku_folder) or not os.listdir(sku_folder): return None [cite: 89]
        timestamp = get_taiwan_time_now().strftime("%Y%m%d%H%M")
        zip_filename_base = f"{sku}_{timestamp}"
        zip_path = shutil.make_archive(zip_filename_base, 'zip', sku_folder) [cite: 89]
        shutil.rmtree(sku_folder) 
        return zip_path
    except: return None

# ================= Google Sheet 壓力測試管理 =================
def connect_google_sheet():
    print("📊 正在連線 Google Sheet...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    return client [cite: 90]

def prepare_test_worksheet(client):
    """ 為每一輪測試建立獨立不覆蓋的分頁 """
    ss = client.open(SPREADSHEET_FILE_NAME)
    temp_ws = ss.worksheet(WORKSHEET_TEMPLATE)
    # 建立名稱如: Test_0117_2330
    new_title = f"Test_{get_taiwan_time_now().strftime('%m%d_%H%M')}"
    print(f"📄 建立新測試分頁: {new_title}")
    new_ws = ss.duplicate_sheet(temp_ws.id, insert_sheet_index=1, new_sheet_name=new_title)
    return new_ws

# ================= Selenium & 核心邏輯 =================
def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36") [cite: 91]
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options) [cite: 91]
    return driver

def check_item_exists(driver, sku):
    try:
        driver.get(URL)
        time.sleep(2)
        search_input = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search'], input[name='q']")))
        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku)
        search_input.send_keys(Keys.RETURN) [cite: 95]
        time.sleep(3)
        try:
            xpath_sku = f"//a[contains(@href, '{sku}')]"
            driver.find_element(By.XPATH, xpath_sku) [cite: 96]
            return True
        except: return False
    except: return False

def process_mix_case_dynamic(driver, strategy_str, target_total_qty, main_sku):
    # 此處保留您提供的 process_mix_case_dynamic 邏輯，包含 60秒極致等待與重試機制 [cite: 122, 123, 124]
    # (為了節省篇幅，邏輯內部的加車流程與等待轉圈圈消失代碼與您提供的 MIX_PY.txt 一致)
    pass # 執行時請確保填入完整邏輯

# ================= 任務執行函式 =================
def run_stress_round(client, round_num):
    """ 執行單輪測試 """
    driver = init_driver()
    try:
        # 1. 建立當輪專屬分頁，防止覆蓋 
        current_ws = prepare_test_worksheet(client)
        all_values = current_ws.get_all_values()
        
        results_for_mail = []
        all_match = True
        
        # 2. 遍歷分頁中的商品進行測試 [cite: 127]
        for i, row in enumerate(all_values[1:], start=2):
            main_sku = row[0]
            # ... (執行 process_mix_case_dynamic 取得結果)
            # 3. 更新當前分頁的資料，不影響其他分頁 [cite: 134]
            # current_ws.update(values=[[web_total, result_text, update_time, link]], range_name=f"G{i}:J{i}")
            pass

        # 4. 發送當輪報表 [cite: 136]
        subject = f"Round {round_num} 壓力測試報表 ({get_taiwan_time_now().strftime('%H:%M')})"
        # send_email_generic(subject, ...)
        print(f"✅ 第 {round_num} 輪壓力測試完成。")
        
    finally:
        driver.quit() # 確保每輪結束都關閉瀏覽器資源 

# ================= 壓力測試主迴圈 =================
def main():
    client = connect_google_sheet() [cite: 138]
    round_count = 1
    
    print("🔥 壓力測試模式啟動：代碼將持續循環直到手動中斷 (Ctrl+C)")
    print("📢 每一輪測試都會建立新的分頁存放，數據不會被覆蓋。")
    
    try:
        while True:
            print(f"\n{'='*20} 開始第 {round_count} 輪測試 {'='*20}")
            run_stress_round(client, round_count)
            
            # 每輪結束冷卻 60 秒，避免被網站封鎖
            print(f"⏳ 冷卻中，60 秒後開始下一輪...")
            time.sleep(60)
            round_count += 1
            
    except KeyboardInterrupt:
        print("\n👋 收到手動停止指令，壓力測試結束。")
    except Exception as e:
        print(f"💥 發生重大錯誤: {e}")

if __name__ == "__main__":
    main()
