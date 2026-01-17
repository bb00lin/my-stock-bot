import time
import gspread
import re
import os
import shutil
import smtplib
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
WORKSHEET_MAIN = '工作表1' 
WORKSHEET_MIX = 'Mix_Match_Check'  # ★★★ 鎖定目標工作表 ★★★
WORKSHEET_PROMO = 'promotion'

# 請確認此網址正確
SHEET_URL_FOR_MAIL = "https://docs.google.com/spreadsheets/d/您的試算表ID/edit"

CREDENTIALS_FILE = 'google_key.json'
URL = "https://guardian.com.sg/"

# Email 設定
MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
MAIL_RECEIVER = ['bb00lin@gmail.com', 'helen.chen.168@gmail.com']

# ================= 輔助功能 =================
def clean_price(price_text):
    if not price_text: return ""
    return str(price_text).replace("SGD", "").replace("$", "").replace(",", "").replace("\n", "").replace(" ", "").strip()

def get_taiwan_time_now():
    return datetime.now(timezone(timedelta(hours=8)))

def get_taiwan_time_display():
    return get_taiwan_time_now().strftime("%Y-%m-%d %H:%M")

def get_taiwan_time_str():
    return get_taiwan_time_now().strftime("%Y%m%d%H%M")

def safe_get(row_list, index):
    if index < len(row_list): return str(row_list[index])
    return ""

def parse_date(date_str):
    try:
        date_part = date_str.split()[0]
        return datetime.strptime(date_part, "%d/%m/%Y").date()
    except:
        return None

def create_zip_evidence(sku, sku_folder):
    try:
        if not os.path.exists(sku_folder) or not os.listdir(sku_folder): return None
        timestamp = get_taiwan_time_str()
        zip_filename_base = f"{sku}_{timestamp}"
        zip_path = shutil.make_archive(zip_filename_base, 'zip', sku_folder)
        shutil.rmtree(sku_folder) 
        return zip_path
    except: return None

# ================= Google Sheet 連線 =================
def connect_google_sheet():
    print("📊 正在連線 Google Sheet...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    return client

# ================= 共用 Selenium 功能 =================
def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def handle_popups(driver):
    try:
        popups = ["button[aria-label='Close']", "div.close-popup", "#onetrust-accept-btn-handler", "div[class*='popup'] button", "iframe[title*='popup']"]
        for p in popups:
            try:
                elem = driver.find_element(By.CSS_SELECTOR, p)
                if elem.is_displayed():
                    driver.execute_script("arguments[0].click();", elem)
                    time.sleep(1)
            except: pass
    except: pass

def empty_cart(driver):
    print("🧹 正在清空購物車 (Cookies)...")
    try:
        if "guardian.com.sg" not in driver.current_url:
             driver.get("https://guardian.com.sg/")
             time.sleep(2)
        driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear();")
        driver.execute_script("window.sessionStorage.clear();")
        driver.refresh()
        time.sleep(3) 
    except: pass

def get_total_price_safely(driver):
    try:
        total_element = driver.find_element(By.XPATH, "//span[contains(@class, 'priceSummary-totalPrice')]")
        return clean_price(total_element.text)
    except: pass
    try:
        total_element = driver.find_element(By.XPATH, "//*[contains(text(), 'Total')]/ancestor::div[contains(@class, 'priceSummary-totalLineItems')]//span[contains(@class, 'priceSummary-totalPrice')]")
        return clean_price(total_element.text)
    except: pass
    return None

def check_item_exists(driver, sku):
    """ 檢查商品是否存在 """
    try:
        driver.get(URL)
        time.sleep(2)
        handle_popups(driver)
        search_input = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search'], input[name='q']")))
        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku)
        search_input.send_keys(Keys.RETURN)
        time.sleep(3)
        handle_popups(driver)
        try:
            xpath_sku = f"//a[contains(@href, '{sku}')]"
            xpath_generic = "(//div[contains(@class, 'product')]//a)[1]"
            try:
                driver.find_element(By.XPATH, xpath_sku)
                return True
            except:
                driver.find_element(By.XPATH, xpath_generic)
                return True
        except:
            return False
    except:
        return False

def add_single_item_to_cart(driver, sku, qty_needed=1):
    print(f"   ➕ 加入商品: {sku} x {qty_needed}")
    try:
        driver.get(URL)
        time.sleep(3)
        handle_popups(driver)

        search_input = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search'], input[name='q']")))
        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku)
        time.sleep(0.5)
        search_input.send_keys(Keys.RETURN)
        time.sleep(3)
        handle_popups(driver)

        try:
            xpath_sku = f"//a[contains(@href, '{sku}')]"
            xpath_generic = "(//div[contains(@class, 'product')]//a)[1]"
            try:
                link = driver.find_element(By.XPATH, xpath_sku)
            except:
                link = driver.find_element(By.XPATH, xpath_generic)
            driver.execute_script("arguments[0].click();", link)
        except:
            print(f"      ❌ 找不到商品 {sku}")
            return False

        time.sleep(3)
        
        try:
            add_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart")))
            try:
                qty_input = driver.find_element(By.CSS_SELECTOR, "input[name='qty']")
                driver.execute_script("arguments[0].value = arguments[1];", qty_input, str(qty_needed))
            except: pass

            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", add_btn)
            driver.execute_script("arguments[0].click();", add_btn)
            time.sleep(2) 
            return True
        except:
            print(f"      ❌ 無法點擊加入購物車 {sku}")
            return False
            
    except Exception as e:
        print(f"      ❌ 加入過程發生錯誤: {e}")
        return False

# ================= Task 2: Mix & Match (自動遞補) =================
def sync_mix_match_data(client):
    print("🔄 [Task 2] 同步 Mix & Match 資料...")
    promo_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_PROMO)
    try:
        mix_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    except:
        mix_sheet = client.open(SPREADSHEET_FILE_NAME).add_worksheet(title=WORKSHEET_MIX, rows=100, cols=20)

    mix_sheet.clear()
    headers = ["Main SKU", "Product Name", "Promo Rule", "Target Qty", "Mix Strategy", "Expected Price", "Web Total Price", "Result", "Update Time", "Main Link"]
    
    rows = promo_sheet.get_all_values()
    new_data = [headers]
    today = get_taiwan_time_now().date()

    for row in rows[6:]:
        desc = safe_get(row, 6) 
        if "Mix & Match" in desc:
            start_str = safe_get(row, 8)
            end_str = safe_get(row, 9)
            d_start = parse_date(start_str)
            d_end = parse_date(end_str)
            if d_start and d_end and not (d_start <= today <= d_end): continue 

            main_sku = safe_get(row, 11).replace("'", "").strip()
            if len(main_sku) > 6: main_sku = main_sku[-6:]
            prod_name = safe_get(row, 12)

            partners = []
            match_partners = re.search(r'Mix & Match\s*([\d,]+)', desc)
            if match_partners:
                raw_partners = match_partners.group(1).split(',')
                for p in raw_partners:
                    p = p.strip()
                    if len(p) > 6: p = p[-6:]
                    if p != main_sku: partners.append(p)
            
            if not partners: continue 

            matches = re.findall(r'(\d+)\s+[Ff]or\s*\$?([\d\.]+)', desc)
            if not matches: continue
            
            max_qty = 0
            expected_price = 0.0
            rule_text = ""
            for q_str, p_str in matches:
                q = int(q_str)
                if q > max_qty:
                    max_qty = q
                    expected_price = float(p_str)
                    rule_text = f"{q} For ${p_str}"

            pool = [main_sku] + partners
            pool_cycle = cycle(pool)
            strategy_dict = {}
            for _ in range(max_qty):
                item = next(pool_cycle)
                strategy_dict[item] = strategy_dict.get(item, 0) + 1
            
            strategy_str = "; ".join([f"{k}:{v}" for k, v in strategy_dict.items()])

            row_data = [main_sku, prod_name, rule_text, max_qty, strategy_str, str(expected_price), "", "", "", ""]
            new_data.append(row_data)

    mix_sheet.update(values=new_data, range_name="A1")
    print(f"✅ [Task 2] 已生成 {len(new_data)-1} 筆混搭測試案例")
    return len(new_data)-1

def process_mix_case_dynamic(driver, strategy_str, target_total_qty):
    empty_cart(driver)
    raw_items = strategy_str.split(';')
    unique_skus = []
    for item in raw_items:
        s = item.split(':')[0].strip()
        if s not in unique_skus: unique_skus.append(s)
        
    folder_name = "mix_temp"
    if not os.path.exists(folder_name): os.makedirs(folder_name)
    
    available_skus = []
    print(f"   🕵️ 正在檢查商品庫存狀況...")
    
    for sku in unique_skus:
        if check_item_exists(driver, sku):
            available_skus.append(sku)
        else:
            print(f"   ⚠️ 商品 {sku} 搜尋不到，將從混搭名單移除")
            
    if not available_skus:
        return "Error (All Missing)", "", None

    final_strategy = {}
    pool_cycle = cycle(available_skus)
    for _ in range(target_total_qty):
        item = next(pool_cycle)
        final_strategy[item] = final_strategy.get(item, 0) + 1
        
    print(f"   🔄 調整後策略: {final_strategy}")
    empty_cart(driver)
    
    main_url = ""
    first_sku = available_skus[0] 
    
    for sku, qty in final_strategy.items():
        success = add_single_item_to_cart(driver, sku, qty)
        if not success:
            driver.save_screenshot(f"{folder_name}/Add_Fail_{sku}.png")
            zip_path = create_zip_evidence("Mix_Error", folder_name)
            return "Add Fail", "", zip_path
        
        if not main_url: main_url = driver.current_url

    driver.get("https://guardian.com.sg/cart")
    time.sleep(5)
    
    total_price = get_total_price_safely(driver)
    if not total_price: total_price = "Error"
    
    screenshot_name = f"Mix_{first_sku}_Total.png"
    driver.save_screenshot(f"{folder_name}/{screenshot_name}")
    
    zip_path = create_zip_evidence("Mix_Evidence", folder_name)
    return total_price, main_url, zip_path

def run_mix_match_task(client, driver):
    row_count = sync_mix_match_data(client)
    if row_count == 0: return [], [], True

    sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    all_values = sheet.get_all_values()
    results_for_mail = []
    attachments = []
    all_match = True
    error_summary = []

    print(f"🚀 [Task 2] 開始執行混搭測試...")

    for i, row in enumerate(all_values[1:], start=2):
        main_sku = row[0]
        original_strategy = row[4]
        target_qty = int(row[3])
        expected = float(row[5])
        
        print(f"   🧪 測試: {original_strategy} (目標 {target_qty} 個)")
        
        web_total, link, zip_file = process_mix_case_dynamic(driver, original_strategy, target_qty)
        
        is_error = False
        result_text = ""
        
        if "Fail" in web_total or "Error" in web_total:
            result_text = f"🔥 錯誤 ({web_total})"
            is_error = True
        else:
            try:
                web_val = float(web_total)
                if abs(web_val - expected) < 0.05:
                    result_text = "✅ 相符"
                else:
                    result_text = f"🔥 差異 (Exp:{expected} != Web:{web_val})"
                    is_error = True
            except:
                result_text = f"🔥 錯誤 ({web_total})"
                is_error = True

        if is_error:
            all_match = False
            error_summary.append(f"{main_sku}: {result_text}")
            if zip_file: attachments.append(zip_file)
        else:
            if zip_file: 
                try: os.remove(zip_file)
                except: pass

        update_time = get_taiwan_time_display()
        sheet.update(values=[[web_total, result_text, update_time, link]], range_name=f"G{i}:J{i}")
        results_for_mail.append([main_sku, row[1], result_text, update_time])

    subject_prefix = "✅" if all_match else "🔥"
    date_info = f"{get_taiwan_time_now().strftime('%m/%d(%a)')}"
    subject = f"{date_info}{subject_prefix}[Ozio Mix & Match比對結果]"
    
    summary_text = "所有混搭組合價格均相符。" if all_match else f"發現混搭價格異常。<br>{'<br>'.join(error_summary)}"
    
    send_email_generic(subject, summary_text, results_for_mail, attachments)
    
    for f in attachments:
        try: os.remove(f)
        except: pass

def send_email_generic(subject, summary, data_rows, attachments):
    if not MAIL_USERNAME or not MAIL_PASSWORD: return

    table_html = "<table border='1' style='border-collapse:collapse;width:100%'>"
    table_html += "<tr style='background:#f2f2f2'><th>SKU</th><th>商品</th><th>結果</th><th>時間</th></tr>"
    for r in data_rows:
        bg = "#fff"
        if "🔥" in r[2] or "Diff" in r[2] or "Error" in r[2] or "Limit" in r[2]: bg = "#ffebee"
        table_html += f"<tr style='background:{bg}'><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"
    table_html += "</table>"

    msg = MIMEMultipart()
    msg['From'] = MAIL_USERNAME
    msg['To'] = ", ".join(MAIL_RECEIVER)
    msg['Subject'] = subject
    
    html = f"<html><body><h2>{subject}</h2><p>{summary}</p>{table_html}<br><a href='{SHEET_URL_FOR_MAIL}'>查看表格</a></body></html>"
    msg.attach(MIMEText(html, 'html'))

    for fpath in attachments:
        try:
            with open(fpath, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(fpath))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(fpath)}"'
            msg.attach(part)
        except: pass

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"📧 郵件已發送: {subject}")
    except Exception as e: print(f"❌ 寄信失敗: {e}")

def main():
    try:
        client = connect_google_sheet()
        driver = init_driver()
        
        # === 唯一執行的任務：Mix & Match ===
        run_mix_match_task(client, driver)
        
        driver.quit()
        print("\n🎉 Mix & Match 任務完成！")
        
    except Exception as e:
        print(f"💥 Fatal Error: {e}")
        try: driver.quit()
        except: pass

if __name__ == "__main__":
    main()
