import time
import gspread
import re
import os
import shutil
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ================= 設定區 =================
# Google Sheet 檔案名稱
SPREADSHEET_FILE_NAME = 'Guardian_Price_Check'

# 工作表名稱
WORKSHEET_MAIN = '工作表1' 
WORKSHEET_PROMO = 'promotion'

# Google Sheet 網址 (用於 Email 連結)
# ★★★ 請確認此網址是否正確 ★★★
SHEET_URL_FOR_MAIL = "https://docs.google.com/spreadsheets/d/您的試算表ID/edit"

CREDENTIALS_FILE = 'google_key.json'
URL = "https://guardian.com.sg/"

# Email 設定
MAIL_SENDER = os.environ.get('MAIL_USERNAME', 'bb00lin@gmail.com')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
MAIL_RECEIVER = 'bb00lin@gmail.com' 

# ================= 輔助功能 =================
def clean_price(price_text):
    if not price_text:
        return ""
    cleaned = str(price_text).replace("SGD", "").replace("$", "").replace(",", "").replace("\n", "").replace(" ", "").strip()
    return cleaned

def get_taiwan_time_display():
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M")

def get_taiwan_time_str():
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    return now.strftime("%Y%m%d%H%M")

def safe_get(row_list, index):
    if index < len(row_list):
        return str(row_list[index])
    return ""

# ================= 資料同步與解析功能 =================
def parse_promo_string(promo_text):
    if not promo_text:
        return ["", "", "", "", ""]

    matches = re.findall(r'(\d+)\s+[Ff]or\s+\$?([\d\.]+)', promo_text)
    
    price_map = {}
    for qty_str, price_str in matches:
        try:
            qty = int(qty_str)
            price = float(price_str)
            price_map[qty] = price
        except:
            continue
            
    if not price_map:
        return ["", "", "", "", ""]

    calculated_prices = []
    
    # 取得 Qty 1 的單價
    unit_price_base = 0
    if 1 in price_map:
        unit_price_base = price_map[1]
    else:
        min_qty = min(price_map.keys())
        unit_price_base = price_map[min_qty] / min_qty

    for q in range(1, 6):
        if q in price_map:
            calculated_prices.append(str(price_map[q]))
        else:
            total = unit_price_base * q
            val_str = "{:.2f}".format(total).rstrip('0').rstrip('.')
            calculated_prices.append(val_str)
            
    return calculated_prices

def sync_promotion_data(client):
    print("🔄 正在從 promotion 同步資料...")
    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        source_sheet = spreadsheet.worksheet(WORKSHEET_PROMO)
        target_sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
    except Exception as e:
        print(f"❌ 無法開啟工作表: {e}")
        return False

    # 1. 讀取 Source 資料
    all_values = source_sheet.get_all_values()
    new_rows = []
    
    # 假設資料從第 7 列開始 (index 6)
    start_row_index = 6 
    
    for row in all_values[start_row_index:]:
        raw_sku = safe_get(row, 11) # L欄
        prod_name = safe_get(row, 12) # M欄
        promo_desc = safe_get(row, 6) # G欄
        
        if not raw_sku:
            continue
            
        sku = raw_sku
        if len(raw_sku) > 6:
            sku = raw_sku[-6:]
            
        user_prices = parse_promo_string(promo_desc)
        
        # 組合資料: A~G (資料) + H~O (空白) -> 共 15 欄
        row_data = [sku, prod_name] + user_prices + [""] * 8
        new_rows.append(row_data)

    if not new_rows:
        print("⚠️ Promotion 表格無資料")
        return False

    # 2. 清除 Sheet1 舊資料
    print("🧹 清除舊資料...")
    current_rows = len(target_sheet.get_all_values())
    if current_rows > 1:
        target_sheet.batch_clear([f"A2:O{current_rows}"])
    
    # 3. 寫入新資料
    print(f"📝 寫入 {len(new_rows)} 筆新資料...")
    
    # === 關鍵修正：範圍改為 A2:O (原本是 G，導致寬度不足報錯) ===
    end_row = 2 + len(new_rows) - 1
    target_sheet.update(values=new_rows, range_name=f"A2:O{end_row}")
    
    print("✅ 資料同步完成")
    return True

# ================= 郵件通知功能 =================
def send_notification_email(all_match, error_summary):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print("⚠️ 未設定 Email 帳密，跳過寄信")
        return

    print("📧 正在發送通知郵件...")
    
    if all_match:
        subject = "[Ozio比對結果-正常]價格相符"
        body_content = f"所有商品價格比對結果均相符。<br><br>查看表格: <a href='{SHEET_URL_FOR_MAIL}'>Google Sheet 連結</a>"
        color = "green"
    else:
        subject = "[Ozio比對結果-異常]請檢查表格"
        body_content = f"發現價格異常，請檢查表格。<br><br>異常摘要:<br>{error_summary}<br><br>查看表格: <a href='{SHEET_URL_FOR_MAIL}'>Google Sheet 連結</a>"
        color = "red"

    msg = MIMEMultipart()
    msg['From'] = MAIL_SENDER
    msg['To'] = MAIL_RECEIVER
    msg['Subject'] = subject

    html = f"""
    <html><body>
        <h2 style="color:{color}">{subject}</h2>
        <p>{body_content}</p>
        <p>此郵件由 Guardian Price Bot 自動發送</p>
    </body></html>
    """
    msg.attach(MIMEText(html, 'html'))

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
    valid_numbers = []
    for p in clean_prices:
        if not p: continue 
        try:
            val = float(p)
            valid_numbers.append(val)
        except: return f"異常:User含非數值({p})"
    if len(valid_numbers) > 1:
        if len(set(valid_numbers)) == 1: return "異常:User價格數值皆相同"
    return None

def compare_prices(user_prices, web_prices):
    user_validation_error = validate_user_inputs(user_prices)
    if user_validation_error: return user_validation_error

    mismatches = []
    valid_comparison_count = 0

    for i in range(5):
        u_raw = user_prices[i]
        w_raw = web_prices[i]
        u_val = clean_price(u_raw)
        w_val = clean_price(w_raw)

        if not u_val: continue
        valid_comparison_count += 1

        try:
            u_num = float(u_val)
            w_num = float(w_val) if w_val and w_val not in ["Error", "N/A", "Limit Reached"] else -999
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
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def handle_popups(driver):
    try:
        popups = ["button[aria-label='Close']", "div.close-popup", "button.align-right.secondary.slidedown-button", "#onetrust-accept-btn-handler"]
        for p in popups:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, p)
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
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
    print(f"\n🔍 開始搜尋 SKU: {sku}")
    prices = [] 
    product_url = "" 
    previous_price_val = -1.0 
    
    sku_folder = str(sku)
    if os.path.exists(sku_folder): shutil.rmtree(sku_folder) 
    os.makedirs(sku_folder)
    
    try:
        driver.get(URL)
        time.sleep(5)
        handle_popups(driver)

        # 1. 搜尋
        try:
            search_input = None
            selectors = ["input[placeholder*='Search']", "input[name='q']", "input[type='search']", "input.search-input"]
            for selector in selectors:
                try:
                    search_input = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, selector)))
                    if search_input: break
                except: continue
            
            if not search_input: raise TimeoutException("找不到搜尋框")

            search_input.clear()
            search_input.send_keys(sku)
            time.sleep(1)
            search_input.send_keys(Keys.RETURN)
        except TimeoutException:
            print("❌ 搜尋框載入超時")
            return ["Search Fail"] * 5, "URL Not Found"

        time.sleep(5)
        handle_popups(driver)

        # 2. 點擊商品
        try:
            xpath_selectors = [f"//a[contains(@href, '{sku}')]", "(//div[contains(@class, 'product')]//a)[1]", "(//main//a[.//img])[1]", "//div[data-testid='product-card']//a"]
            clicked = False
            for xpath in xpath_selectors:
                try:
                    product_link = driver.find_element(By.XPATH, xpath)
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", product_link)
                    time.sleep(1)
                    try: product_link.click()
                    except: driver.execute_script("arguments[0].click();", product_link)
                    clicked = True
                    break
                except: continue
            
            if not clicked: raise NoSuchElementException("無法找到任何商品連結")
            
            try: WebDriverWait(driver, 10).until(lambda d: "search.html" not in d.current_url)
            except: print("   ⚠️ 警告：網址似乎仍停留在搜尋頁")
            
            time.sleep(2) 
            product_url = driver.current_url
            print(f"🔗 取得目前連結: {product_url}")
            if "search.html" in product_url:
                print("❌ 點擊後仍停留在搜尋結果頁")
                return ["Click Fail"] * 5, product_url

        except NoSuchElementException:
            print(f"⚠️ 搜尋不到 SKU {sku}")
            return ["Not Found"] * 5, "URL Not Found"

        time.sleep(4)
        handle_popups(driver)

        # 3. 加入購物車
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
            return ["Add Fail"] * 5, product_url

        time.sleep(5)

        # 4. 調整數量與抓取價格
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
                            else: print(f"   ⚠️ 抓取異常 (嘗試 {attempt+1}): Qty {qty} (${current_val}) <= Qty {qty-1} (${previous_price_val})")
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
            
            if final_price == "Error" and current_price_str:
                final_price = current_price_str
                try: previous_price_val = float(final_price)
                except: pass
                print(f"   ❌ 重試後強制填入: {final_price}")
                driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}_abnormal.png")

            prices.append(final_price)

            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    driver.execute_script("arguments[0].click();", plus_btn)
                    
                    print(f"   ⏳ 正在增加數量 ({qty}->{qty+1})...")
                    time.sleep(0.5) 
                    try: WebDriverWait(driver, 20).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')] | //div[contains(@class, 'loading-mask')]")))
                    except TimeoutException: pass
                    
                    try:
                        error_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')]")
                        if error_msg.is_displayed():
                            print("   🛑 達到購買上限")
                            for _ in range(qty, 5): prices.append("Limit Reached")
                            break
                    except: pass
                except Exception:
                    print("   ⚠️ 無法點擊 + 按鈕")
                    break
        
        while len(prices) < 5: prices.append("Error")
        empty_cart(driver)

        timestamp = get_taiwan_time_str()
        zip_filename = f"{sku}_{timestamp}"
        shutil.make_archive(zip_filename, 'zip', sku_folder)
        shutil.rmtree(sku_folder) 

        return prices, product_url

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        try:
            if 'sku_folder' in locals() and os.path.exists(sku_folder):
                 driver.save_screenshot(f"{sku_folder}/{sku}_exception.png")
            empty_cart(driver)
        except: pass
        return ["Error"] * 5, product_url

# ================= 主程式 =================
def main():
    try:
        client = connect_google_sheet()
        
        sync_success = sync_promotion_data(client)
        if not sync_success:
            print("⚠️ 資料同步失敗，停止執行後續爬蟲")
            return

        driver = init_driver()
        print("--- 初始化檢查 ---")
        empty_cart(driver)
        
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
        all_values = sheet.get_all_values()
        
        print(f"📋 共有 {len(all_values)-1} 筆資料待處理")

        overall_status_match = True
        error_summary_list = []

        for i, row_data in enumerate(all_values[1:], start=2):
            sku = safe_get(row_data, 0).strip()
            if not sku: continue
            
            user_prices = [safe_get(row_data, 2), safe_get(row_data, 3), safe_get(row_data, 4), safe_get(row_data, 5), safe_get(row_data, 6)]

            web_prices, product_url = process_sku(driver, sku)
            update_time = get_taiwan_time_display()
            comparison_result = compare_prices(user_prices, web_prices)
            
            data_to_write = web_prices + [update_time, comparison_result, product_url]
            cell_range = f"H{i}:O{i}"
            sheet.update(values=[data_to_write], range_name=cell_range)
            
            print(f"✅ SKU {sku} 完成 | 結果: {comparison_result} | URL: {product_url}")
            print("-" * 30)

            if comparison_result != "均相符":
                overall_status_match = False
                error_summary_list.append(f"SKU {sku}: {comparison_result}")

        print("🎉 所有任務完成！")
        driver.quit()
        
        error_text = "\n".join(error_summary_list) if error_summary_list else ""
        send_notification_email(overall_status_match, error_text)

    except Exception as main_e:
        print(f"💥 程式執行發生重大錯誤: {main_e}")
        if 'driver' in locals(): driver.quit()

if __name__ == "__main__":
    main()
