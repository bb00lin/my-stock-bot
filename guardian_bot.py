import time
import gspread
import re
import os
import shutil
import smtplib
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
# [修改] 改為寫入一個專門的壓力測試工作表，以免撐爆原本的正式報表
# 當然您也可以維持 '工作表1'，但要注意資料量會暴增
WORKSHEET_MAIN = '工作表1' 
WORKSHEET_PROMO = 'promotion'

# 請確認此網址正確
SHEET_URL_FOR_MAIL = "https://docs.google.com/spreadsheets/d/1pqa6DU-qo3lR84QYgpoiwGE7tO-QSY2-kC_ecf868cY/edit?gid=0#gid=0" 

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
    except Exception as e:
        print(f"   ⚠️ 打包截圖失敗: {e}")
        return None

# ================= 資料同步與解析功能 =================
def parse_promo_string(promo_text):
    if not promo_text: return ["", "", "", "", ""]
    matches = re.findall(r'(\d+)\s+[Ff]or\s*\$?([\d\.]+)', promo_text)
    price_map = {}
    for qty_str, price_str in matches:
        try:
            qty = int(qty_str)
            price = float(price_str)
            price_map[qty] = price
        except: continue
        
    if not price_map: return ["", "", "", "", ""]

    best_unit_price = float('inf')
    for q, p in price_map.items():
        unit_p = p / q
        if unit_p < best_unit_price:
            best_unit_price = unit_p
    
    if best_unit_price == float('inf'): return ["", "", "", "", ""]

    calculated_prices = []
    for q in range(1, 6):
        if q in price_map:
            calculated_prices.append(str(price_map[q]))
        else:
            total = best_unit_price * q
            total_truncated = int(total * 10) / 10.0
            val_str = "{:.1f}".format(total_truncated).rstrip('0').rstrip('.')
            calculated_prices.append(val_str)
            
    return calculated_prices

# [修改] 壓力測試版：這個函式現在只負責回傳資料結構，不再寫入 Sheet
def get_promotion_data(client):
    print("🔄 讀取 Promotion 資料...")
    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        source_sheet = spreadsheet.worksheet(WORKSHEET_PROMO)
    except Exception as e:
        print(f"❌ 無法開啟工作表: {e}")
        return []

    all_values = source_sheet.get_all_values()
    data_list = []
    today = get_taiwan_time_now().date()
    start_row_index = 6 
    
    for row in all_values[start_row_index:]:
        raw_sku = safe_get(row, 11)
        prod_name = safe_get(row, 12)
        promo_desc = safe_get(row, 6)
        date_start_str = safe_get(row, 8)
        date_end_str = safe_get(row, 9)
        
        if not raw_sku: continue
            
        sku = str(raw_sku).replace("'", "").replace('"', '').strip()
        if len(sku) > 6: sku = sku[-6:]
            
        user_prices = parse_promo_string(promo_desc)
        
        date_status = ""
        d_start = parse_date(date_start_str)
        d_end = parse_date(date_end_str)
        
        if d_start and d_end:
            if not (d_start <= today <= d_end):
                date_status = f"⚠️ 非檔期 ({d_start.strftime('%m/%d')}~{d_end.strftime('%m/%d')})"
        elif d_start and not d_end:
             if today < d_start: date_status = f"⚠️ 尚未開始 (起:{d_start.strftime('%m/%d')})"
        
        # 這裡不清除舊資料，而是回傳整理好的資料結構供主程式使用
        data_list.append({
            "sku": sku,
            "name": prod_name,
            "user_prices": user_prices,
            "date_status": date_status
        })

    return data_list

# ================= 郵件通知功能 (保持原樣) =================
def generate_html_table(data_rows):
    if not data_rows: return ""
    headers = ["SKU", "商品名稱", "比對結果", "更新時間"]
    table_html = "<table border='1' style='border-collapse: collapse; width: 100%; font-size: 12px;'>"
    table_html += "<tr style='background-color: #f2f2f2;'>"
    for h in headers: table_html += f"<th style='padding: 8px; text-align: left;'>{h}</th>"
    table_html += "</tr>"
    
    for row in data_rows:
        sku = safe_get(row, 0)
        name = safe_get(row, 1)
        time_str = safe_get(row, 12)
        result = safe_get(row, 13)
        
        bg_color = "#ffffff"
        if "商品未上架" in result: bg_color = "#eeeeee"
        elif "Diff" in result or "異常" in result: bg_color = "#ffebee" 
        elif "非檔期" in result or "尚未開始" in result: bg_color = "#fff3e0" 
            
        table_html += f"<tr style='background-color: {bg_color};'>"
        table_html += f"<td style='padding: 8px;'>{sku}</td>"
        table_html += f"<td style='padding: 8px;'>{name}</td>"
        table_html += f"<td style='padding: 8px;'>{result}</td>"
        table_html += f"<td style='padding: 8px;'>{time_str}</td>"
        table_html += "</tr>"
        
    table_html += "</table>"
    return table_html

def send_notification_email(all_match, error_summary, full_data, attachment_files, round_num):
    if not MAIL_USERNAME or not MAIL_PASSWORD: return

    print("📧 正在發送通知郵件...")
    
    has_limit_reached = False
    if full_data:
        for row in full_data:
            web_prices_slice = row[7:12] 
            if any("Limit Reached" in str(p) for p in web_prices_slice):
                has_limit_reached = True
                break
    
    subject_prefix = ""
    subject_text = ""
    color = ""
    summary_text = ""

    if has_limit_reached:
        subject_prefix = "⚠️"
        subject_text = "[Ozio壓力測試-警告] 達購買上限/異常"
        color = "#ff9800" 
        summary_text = f"Round {round_num}: 發現部分商品達到購買上限或有其他異常。<br>異常摘要:<br>{error_summary}"
    elif not all_match:
        subject_prefix = "🔥"
        subject_text = "[Ozio壓力測試-異常] 請檢查表格"
        color = "red" 
        summary_text = f"Round {round_num}: 發現價格異常或非檔期商品。<br>異常摘要:<br>{error_summary}"
    else:
        subject_prefix = "✅"
        subject_text = "[Ozio壓力測試-正常] 價格相符"
        color = "green" 
        summary_text = f"Round {round_num}: 所有商品價格比對結果均相符。"

    now = get_taiwan_time_now()
    date_str = f"{now.month}/{now.day} {now.strftime('%H:%M')}"

    final_subject = f"{date_str} {subject_prefix} {subject_text} (R{round_num})"
    snapshot_table = generate_html_table(full_data)

    msg = MIMEMultipart()
    msg['From'] = MAIL_USERNAME
    msg['To'] = ", ".join(MAIL_RECEIVER)
    msg['Subject'] = final_subject

    html = f"""
    <html><body>
        <h2 style="color:{color}">{final_subject}</h2>
        <p>{summary_text}</p>
        <p><b>以下為本輪測試快照：</b></p>
        {snapshot_table}
        <br>
        <p>查看完整表格: <a href='{SHEET_URL_FOR_MAIL}'>Google Sheet 連結</a></p>
        <p>此郵件由 Guardian Price Bot 壓力測試模式發送</p>
    </body></html>
    """
    msg.attach(MIMEText(html, 'html'))

    total_size = 0
    max_size = 24 * 1024 * 1024 
    
    if attachment_files:
        for fpath in attachment_files:
            try:
                if os.path.exists(fpath):
                    file_size = os.path.getsize(fpath)
                    if total_size + file_size > max_size: break
                    with open(fpath, 'rb') as f:
                        part = MIMEApplication(f.read(), Name=os.path.basename(fpath))
                    part['Content-Disposition'] = f'attachment; filename="{os.path.basename(fpath)}"'
                    msg.attach(part)
                    total_size += file_size
            except: pass

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("✅ 郵件發送成功")
    except Exception as e:
        print(f"❌ 郵件發送失敗: {e}")

# ================= 核心邏輯 =================
def validate_user_inputs(user_prices):
    clean_prices = [clean_price(p) for p in user_prices]
    if all(not p for p in clean_prices): return "異常:User價格全空"
    for p in clean_prices:
        if not p: continue 
        try: float(p)
        except: return f"異常:User含非數值({p})"
    return None

def compare_prices(user_prices, web_prices, product_url):
    user_validation_error = validate_user_inputs(user_prices)
    if user_validation_error: return user_validation_error

    if "Not Found" in product_url:
        has_any_price = False
        for p in web_prices:
            if p and p not in ["Error", "Search Fail", "Not Found", "Add Fail", "Click Fail", "Limit Reached"]:
                try:
                    float(p)
                    has_any_price = True
                    break
                except: pass
        if has_any_price: return "該商品未上架，但是卻有商品價格請確認!"
        else: return "該商品未上架"

    mismatches = []
    valid_comparison_count = 0

    for i in range(5):
        u_raw = user_prices[i]
        w_raw = web_prices[i]
        u_val = clean_price(u_raw)
        
        if w_raw == "Limit Reached":
            if u_val: mismatches.append(f"Q{i+1}:Limit Reached")
            continue

        w_val = clean_price(w_raw)

        if not u_val: continue
        valid_comparison_count += 1

        try:
            u_num = float(u_val)
            w_num = float(w_val) if w_val and w_val not in ["Error", "N/A"] else -999
            if abs(u_num - w_num) < 0.01: pass
            else: mismatches.append(f"Q{i+1}:User({u_val})!=Web({w_val})")
        except:
            if u_val == w_val: pass
            else: mismatches.append(f"Q{i+1}:Diff")

    if valid_comparison_count == 0: return ""
    if not mismatches: return "均相符"
    else: return "; ".join(mismatches)

def connect_google_sheet():
    print("📊 正在連線 Google Sheet...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    return client

# ================= Selenium 功能 =================
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
        popups = [
            "button[aria-label='Close']", "div.close-popup", 
            "button.align-right.secondary.slidedown-button", "#onetrust-accept-btn-handler",
            "div[class*='popup'] button", "iframe[title*='popup']"
        ]
        for p in popups:
            try:
                elem = driver.find_element(By.CSS_SELECTOR, p)
                if elem.is_displayed():
                    driver.execute_script("arguments[0].click();", elem)
                    time.sleep(1)
            except: pass
    except: pass

def empty_cart(driver):
    print("🧹 正在執行核彈級清空 (刪除 Cookies)...")
    try:
        if "guardian.com.sg" not in driver.current_url:
             driver.get("https://guardian.com.sg/")
             time.sleep(2)
        driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear();")
        driver.execute_script("window.sessionStorage.clear();")
        driver.refresh()
        time.sleep(4) 
    except Exception as e: print(f"   ⚠️ 清空過程發生小錯誤: {e}")

def get_price_safely(driver):
    try:
        total_element = driver.find_element(By.XPATH, "//span[contains(@class, 'priceSummary-totalPrice')]")
        return clean_price(total_element.text)
    except: pass
    try:
        total_element = driver.find_element(By.XPATH, "//*[contains(text(), 'Total')]/ancestor::div[contains(@class, 'priceSummary-totalLineItems')]//span[contains(@class, 'priceSummary-totalPrice')]")
        return clean_price(total_element.text)
    except: pass
    return None

def process_sku(driver, sku):
    # [保留原有的爬蟲邏輯]
    print(f"\n🔍 開始搜尋 SKU: {sku}")
    prices = [] 
    product_url = "" 
    previous_price_val = -1.0 
    
    sku_folder = str(sku)
    if os.path.exists(sku_folder): shutil.rmtree(sku_folder) 
    os.makedirs(sku_folder)
    
    generated_zip = None

    try:
        driver.get(URL)
        time.sleep(5)
        handle_popups(driver)

        search_input = None
        selectors = ["input[placeholder*='Search']", "input[name='q']", "input[type='search']", "input.search-input"]
        for attempt in range(2): 
            try:
                for selector in selectors:
                    try:
                        search_input = WebDriverWait(driver, 8).until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))
                        if search_input: break
                    except: continue
                if search_input: break 
                if attempt == 0:
                    print("   ⚠️ 第一次找不到搜尋框，嘗試重整頁面...")
                    driver.refresh()
                    time.sleep(5)
                    handle_popups(driver)
            except: pass
        
        if not search_input:
            print("❌ 搜尋框載入超時")
            driver.save_screenshot(f"{sku_folder}/{sku}_search_fail.png")
            generated_zip = create_zip_evidence(sku, sku_folder)
            return ["Search Fail"] * 5, "URL Not Found", generated_zip

        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku)
        time.sleep(1)
        search_input.send_keys(Keys.RETURN)

        time.sleep(5)
        handle_popups(driver)

        try:
            xpath_sku = f"//a[contains(@href, '{sku}')]"
            xpath_generic = "(//div[contains(@class, 'product')]//a)[1]"
            clicked = False
            try:
                link = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath_sku)))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                driver.execute_script("arguments[0].click();", link)
                clicked = True
            except: pass
            
            if not clicked:
                try:
                    link = driver.find_element(By.XPATH, xpath_generic)
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
                    driver.execute_script("arguments[0].click();", link)
                    clicked = True
                except: pass

            if not clicked: raise NoSuchElementException("無法找到商品連結")
            
            time.sleep(3)
            product_url = driver.current_url
            print(f"🔗 取得目前連結: {product_url}")
            
            if "search.html" in product_url:
                print("❌ 點擊後仍停留在搜尋結果頁")
                driver.save_screenshot(f"{sku_folder}/{sku}_click_fail.png")
                generated_zip = create_zip_evidence(sku, sku_folder)
                return ["Click Fail"] * 5, product_url, generated_zip

        except NoSuchElementException:
            print(f"⚠️ 搜尋不到 SKU {sku}")
            driver.save_screenshot(f"{sku_folder}/{sku}_not_found.png")
            generated_zip = create_zip_evidence(sku, sku_folder)
            return ["Not Found"] * 5, "URL Not Found", generated_zip

        time.sleep(4)
        handle_popups(driver)

        try:
            add_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart")))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", add_btn)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", add_btn)
            print("🛒 已點擊加入購物車，等待處理...")
            time.sleep(5) 
            driver.get("https://guardian.com.sg/cart")
        except TimeoutException:
            print("❌ 加入購物車按鈕找不到")
            driver.save_screenshot(f"{sku_folder}/{sku}_add_fail.png")
            generated_zip = create_zip_evidence(sku, sku_folder)
            return ["Add Fail"] * 5, product_url, generated_zip

        time.sleep(5)

        for qty in range(1, 6):
            try: WebDriverWait(driver, 15).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')] | //div[contains(@class, 'loading-mask')]")))
            except: pass
            
            final_price = "Error"
            max_retries = 10
            
            for attempt in range(max_retries):
                current_price_str = get_price_safely(driver)
                is_valid = False
                current_val = -1.0

                if current_price_str:
                    try:
                        current_val = float(current_price_str)
                        if qty == 1: is_valid = True
                        else:
                            if current_val > previous_price_val: is_valid = True
                    except: is_valid = False
                
                if is_valid:
                    final_price = current_price_str
                    previous_price_val = current_val
                    print(f"   💰 數量 {qty}: SGD {final_price}")
                    driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}.png")
                    break
                else:
                    time.sleep(2)
                    try: WebDriverWait(driver, 2).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')]")))
                    except: pass
            
            if final_price == "Error":
                 try:
                    error_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')] | //div[contains(@class, 'message-error')]")
                    if error_msg.is_displayed():
                         print("   🛑 (重試後確認) 達到購買上限")
                         for _ in range(qty, 6): prices.append("Limit Reached")
                         break 
                 except: pass

            if final_price == "Error" and current_price_str:
                final_price = current_price_str
                driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}_abnormal.png")

            if len(prices) < qty:
                prices.append(final_price)

            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    driver.execute_script("arguments[0].click();", plus_btn)
                    
                    time.sleep(1)
                    try:
                        error_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')] | //div[contains(@class, 'message-error')]")
                        if error_msg.is_displayed():
                            print("   🛑 達到購買上限 (Limit Reached)")
                            for _ in range(qty, 5): 
                                prices.append("Limit Reached")
                            break 
                    except: pass
                    
                    time.sleep(0.5) 
                    try: WebDriverWait(driver, 20).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')] | //div[contains(@class, 'loading-mask')]")))
                    except TimeoutException: pass
                    
                except Exception: break
        
        while len(prices) < 5: prices.append("Error")
        empty_cart(driver)

        # 最終打包
        generated_zip = create_zip_evidence(sku, sku_folder)
        return prices, product_url, generated_zip

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        try:
            if 'sku_folder' in locals() and os.path.exists(sku_folder):
                 driver.save_screenshot(f"{sku_folder}/{sku}_exception.png")
                 generated_zip = create_zip_evidence(sku, sku_folder)
            empty_cart(driver)
        except: pass
        return ["Error"] * 5, product_url, generated_zip

# ================= 壓力測試主程式 =================
def run_one_round(client, round_num):
    driver = init_driver()
    print("--- 初始化檢查 ---")
    empty_cart(driver)
    
    # [修改] 壓力測試不清除資料，每次只讀取商品清單
    data_list = get_promotion_data(client)
    
    # [修改] 獲取 Sheet 物件並決定寫入位置 (累加)
    spreadsheet = client.open(SPREADSHEET_FILE_NAME)
    sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
    all_values = sheet.get_all_values()
    start_row = len(all_values) + 1 # 從現有資料下一行開始
    
    # 寫入分隔線
    if start_row == 1:
        headers = ["SKU", "Product Name", "User Q1", "User Q2", "User Q3", "User Q4", "User Q5", 
                   "Web Q1", "Web Q2", "Web Q3", "Web Q4", "Web Q5", "Update Time", "Result", "Link"]
        sheet.append_row(headers)
    else:
        sheet.append_row([f"--- Round {round_num} Start ---"] + [""]*14)

    overall_status_match = True
    error_summary_list = []
    full_data_for_mail = []
    attachment_files = []

    print(f"📋 開始第 {round_num} 輪，共有 {len(data_list)} 筆資料待處理")

    for item in data_list:
        sku = item['sku']
        prod_name = item['name']
        user_prices = item['user_prices']
        date_status = item['date_status']
        
        if "非檔期" in date_status or "尚未開始" in date_status:
            print(f"⚠️ SKU {sku} {date_status}，但仍執行爬蟲更新數據...")

        # 執行爬蟲
        web_prices, product_url, zip_file = process_sku(driver, sku)
        if zip_file: attachment_files.append(zip_file)

        update_time = get_taiwan_time_display()
        comparison_result = compare_prices(user_prices, web_prices, product_url)
        
        if date_status: comparison_result = f"{date_status} | {comparison_result}"

        # 組裝寫入資料
        data_row = [sku, prod_name] + user_prices + web_prices + [update_time, comparison_result, product_url]
        
        # [修改] 即時寫入 Sheet (累加)
        try:
            sheet.append_row(data_row)
            print(f"✅ SKU {sku} 完成 | 結果: {comparison_result}")
        except Exception as e:
            print(f"❌ 寫入 Sheet 失敗: {e}")

        print("-" * 30)

        if "均相符" not in comparison_result and "該商品未上架" not in comparison_result:
            overall_status_match = False
            error_summary_list.append(f"SKU {sku}: {comparison_result}")
        
        full_data_for_mail.append(data_row)

    driver.quit()
    
    # 寄信
    error_text = "<br>".join(error_summary_list) if error_summary_list else ""
    send_notification_email(overall_status_match, error_text, full_data_for_mail, attachment_files, round_num)
    
    # 清理暫存檔
    print("🧹 清理本輪暫存檔...")
    for f in attachment_files:
        try:
            if os.path.exists(f): os.remove(f)
        except: pass

def main():
    client = connect_google_sheet()
    round_count = 1
    
    print("🔥 壓力測試模式 (無限循環 + 累加記錄)")
    print("🛑 按下 Ctrl + C 可隨時停止")
    
    try:
        while True:
            print(f"\n{'='*30}\n開始第 {round_count} 輪循環測試\n{'='*30}")
            run_one_round(client, round_count)
            
            print(f"✅ 第 {round_count} 輪測試結束。")
            print("⏳ 冷卻 60 秒後開始下一輪...")
            time.sleep(60)
            round_count += 1
            
    except KeyboardInterrupt:
        print("\n👋 收到停止指令，壓力測試結束。")
    except Exception as e:
        print(f"💥 發生重大錯誤: {e}")

if __name__ == "__main__":
    main()
