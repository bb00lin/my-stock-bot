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
WORKSHEET_MIX = 'Mix_Match_Check' 
WORKSHEET_PROMO = 'promotion'

# ★★★ 請確認此網址正確 ★★★
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

# ================= Google Sheet 連線 (已補回) =================
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
    """ 抓取購物車總金額 (Total) """
    try:
        total_element = driver.find_element(By.XPATH, "//span[contains(@class, 'priceSummary-totalPrice')]")
        return clean_price(total_element.text)
    except: pass
    try:
        total_element = driver.find_element(By.XPATH, "//*[contains(text(), 'Total')]/ancestor::div[contains(@class, 'priceSummary-totalLineItems')]//span[contains(@class, 'priceSummary-totalPrice')]")
        return clean_price(total_element.text)
    except: pass
    return None

def add_single_item_to_cart(driver, sku, qty_needed=1):
    """ 搜尋並加入單一商品到購物車 (不進入購物車頁面) """
    print(f"   ➕ 加入商品: {sku} x {qty_needed}")
    try:
        driver.get(URL)
        time.sleep(3)
        handle_popups(driver)

        # 搜尋
        search_input = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search'], input[name='q']")))
        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku)
        time.sleep(0.5)
        search_input.send_keys(Keys.RETURN)
        time.sleep(3)
        handle_popups(driver)

        # 點擊
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
            
            # 調整數量輸入框
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

# ================= Task 1: 單一商品檢查 (原功能) =================
def parse_promo_string(promo_text):
    if not promo_text: return ["", "", "", "", ""]
    matches = re.findall(r'(\d+)\s+[Ff]or\s*\$?([\d\.]+)', promo_text)
    price_map = {}
    for qty_str, price_str in matches:
        try: price_map[int(qty_str)] = float(price_str)
        except: continue
    if not price_map: return ["", "", "", "", ""]
    
    best_unit_price = min([p/q for q, p in price_map.items()])
    calculated_prices = []
    
    for q in range(1, 6):
        if q in price_map: calculated_prices.append(str(price_map[q]))
        else:
            total = best_unit_price * q
            total_truncated = int(total * 10) / 10.0
            val_str = "{:.1f}".format(total_truncated).rstrip('0').rstrip('.')
            calculated_prices.append(val_str)
    return calculated_prices

def process_sku_single(driver, sku):
    """ 原本的單商品爬蟲流程 """
    print(f"\n🔍 [Task 1] 開始搜尋 SKU: {sku}")
    prices = [] 
    product_url = "" 
    sku_folder = str(sku)
    if os.path.exists(sku_folder): shutil.rmtree(sku_folder) 
    os.makedirs(sku_folder)
    generated_zip = None

    try:
        driver.get(URL)
        time.sleep(5)
        handle_popups(driver)
        
        # 搜尋
        search_input = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search'], input[name='q']")))
        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku)
        search_input.send_keys(Keys.RETURN)
        time.sleep(5)
        handle_popups(driver)

        # 點擊
        try:
            xpath = f"//a[contains(@href, '{sku}')]"
            try: link = driver.find_element(By.XPATH, xpath)
            except: link = driver.find_element(By.XPATH, "(//div[contains(@class, 'product')]//a)[1]")
            driver.execute_script("arguments[0].click();", link)
        except:
            driver.save_screenshot(f"{sku_folder}/{sku}_not_found.png")
            generated_zip = create_zip_evidence(sku, sku_folder)
            return ["Not Found"]*5, "URL Not Found", generated_zip

        time.sleep(3)
        product_url = driver.current_url
        if "search.html" in product_url:
            return ["Click Fail"]*5, product_url, generated_zip

        # 加入購物車
        try:
            add_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart")))
            driver.execute_script("arguments[0].click();", add_btn)
            time.sleep(5)
            driver.get("https://guardian.com.sg/cart")
        except:
            return ["Add Fail"]*5, product_url, generated_zip

        time.sleep(5)

        # 1~5 數量循環
        for qty in range(1, 6):
            # 數量驗證 (簡化版)
            try:
                qty_input = driver.find_element(By.CSS_SELECTOR, "input[data-role='cart-item-qty'], input.input-text.qty")
                for _ in range(5):
                    if qty_input.get_attribute("value") == str(qty): break
                    time.sleep(0.5)
            except: pass

            try: WebDriverWait(driver, 10).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')]")))
            except: pass
            
            price = get_total_price_safely(driver)
            
            # 檢查是否達上限
            try:
                err = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')]")
                if err.is_displayed():
                    for _ in range(qty, 6): prices.append("Limit Reached")
                    break
            except: pass

            if not price: price = "Error"
            prices.append(price)
            driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}.png")

            if qty < 5:
                try:
                    plus = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    driver.execute_script("arguments[0].click();", plus)
                    time.sleep(1)
                except: break
        
        while len(prices) < 5: prices.append("Error")
        generated_zip = create_zip_evidence(sku, sku_folder)
        empty_cart(driver)
        return prices, product_url, generated_zip

    except Exception as e:
        print(f"❌ Error: {e}")
        empty_cart(driver)
        return ["Error"]*5, product_url, generated_zip

# ================= Task 2: Mix & Match (新功能) =================
def sync_mix_match_data(client):
    """ 讀取 Promotion，找出 Mix & Match，生成測試案例寫入 Sheet2 """
    print("🔄 [Task 2] 同步 Mix & Match 資料...")
    promo_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_PROMO)
    try:
        mix_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    except:
        mix_sheet = client.open(SPREADSHEET_FILE_NAME).add_worksheet(title=WORKSHEET_MIX, rows=100, cols=20)

    # 清空舊資料並寫入標題
    mix_sheet.clear()
    headers = ["Main SKU", "Product Name", "Promo Rule", "Target Qty", "Mix Strategy", "Expected Price", "Web Total Price", "Result", "Update Time", "Main Link"]
    
    rows = promo_sheet.get_all_values()
    new_data = [headers]
    today = get_taiwan_time_now().date()

    # 從第7列開始 (Index 6)
    for row in rows[6:]:
        desc = safe_get(row, 6) # G欄
        
        if "Mix & Match" in desc:
            start_str = safe_get(row, 8)
            end_str = safe_get(row, 9)
            d_start = parse_date(start_str)
            d_end = parse_date(end_str)
            
            if d_start and d_end and not (d_start <= today <= d_end):
                continue 

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

            row_data = [
                main_sku, prod_name, rule_text, max_qty, strategy_str, 
                str(expected_price), "", "", "", "" 
            ]
            new_data.append(row_data)

    mix_sheet.update(values=new_data, range_name="A1")
    print(f"✅ [Task 2] 已生成 {len(new_data)-1} 筆混搭測試案例")
    return len(new_data)-1

def process_mix_case(driver, strategy_str):
    """ 執行混搭加入購物車 """
    empty_cart(driver)
    
    items = strategy_str.split(';')
    folder_name = "mix_temp"
    if not os.path.exists(folder_name): os.makedirs(folder_name)
    
    main_url = ""
    
    for item in items:
        try:
            sku, qty = item.split(':')
            sku = sku.strip()
            qty = int(qty.strip())
            
            success = add_single_item_to_cart(driver, sku, qty)
            if not success:
                return "Add Fail", "", None
            
            if not main_url: main_url = driver.current_url 
            
        except Exception as e:
            print(f"   ⚠️ 解析策略失敗: {e}")
            return "Error", "", None

    driver.get("https://guardian.com.sg/cart")
    time.sleep(5)
    
    total_price = get_total_price_safely(driver)
    if not total_price: total_price = "Error"
    
    screenshot_name = f"Mix_{items[0].split(':')[0]}_Total.png"
    driver.save_screenshot(f"{folder_name}/{screenshot_name}")
    
    zip_path = create_zip_evidence("Mix_Evidence", folder_name)
    
    return total_price, main_url, zip_path

def run_mix_match_task(client, driver):
    """ 執行 Mix & Match 完整任務流程 """
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
        strategy = row[4]
        expected = float(row[5])
        
        print(f"   🧪 測試案例: {strategy} (預期: {expected})")
        
        web_total, link, zip_file = process_mix_case(driver, strategy)
        
        if zip_file: attachments.append(zip_file)
        
        result_text = ""
        try:
            web_val = float(web_total)
            if abs(web_val - expected) < 0.05:
                result_text = "✅ 相符"
            else:
                result_text = f"🔥 差異 (Exp:{expected} != Web:{web_val})"
                all_match = False
                error_summary.append(f"{main_sku}: {result_text}")
        except:
            result_text = f"🔥 錯誤 ({web_total})"
            all_match = False
            error_summary.append(f"{main_sku}: {web_total}")

        update_time = get_taiwan_time_display()
        
        sheet.update(values=[[web_total, result_text, update_time, link]], range_name=f"G{i}:J{i}")
        
        results_for_mail.append([main_sku, row[1], result_text, update_time])

    subject_prefix = "✅" if all_match else "🔥"
    subject = f"{get_taiwan_time_now().strftime('%m/%d(%a)')}{subject_prefix} [Ozio Mix & Match比對結果]"
    
    summary_text = "所有混搭組合價格均相符。" if all_match else f"發現混搭價格異常。<br>{'<br>'.join(error_summary)}"
    
    send_email_generic(subject, summary_text, results_for_mail, attachments)

def send_email_generic(subject, summary, data_rows, attachments):
    """ 通用的發信函式 """
    if not MAIL_USERNAME or not MAIL_PASSWORD: return

    table_html = "<table border='1' style='border-collapse:collapse;width:100%'>"
    table_html += "<tr style='background:#f2f2f2'><th>SKU</th><th>商品</th><th>結果</th><th>時間</th></tr>"
    for r in data_rows:
        bg = "#fff"
        if "🔥" in r[2]: bg = "#ffebee"
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

# ================= 主程式 (雙任務) =================
def run_task_1(client, driver):
    """ 執行原本的單商品檢查 """
    print("\n🟢 [Task 1] 啟動單商品檢查...")
    
    sheet_main = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MAIN)
    sheet_promo = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_PROMO)
    
    all_promos = sheet_promo.get_all_values()
    new_rows = []
    today = get_taiwan_time_now().date()
    
    for row in all_promos[6:]: # data start row 7
        raw_sku = safe_get(row, 11)
        if not raw_sku: continue
        sku = raw_sku.replace("'", "").strip()[-6:]
        
        date_status = ""
        d_start = parse_date(safe_get(row, 8))
        d_end = parse_date(safe_get(row, 9))
        if d_start and d_end and not (d_start <= today <= d_end):
            date_status = f"⚠️ 非檔期 ({d_start.strftime('%m/%d')}~{d_end.strftime('%m/%d')})"
        elif d_start and not d_end and today < d_start:
            date_status = f"⚠️ 尚未開始"

        prices = parse_promo_string(safe_get(row, 6))
        new_rows.append([sku, safe_get(row, 12)] + prices + [""]*6 + [date_status] + [""])

    sheet_main.batch_clear(["A2:O1000"])
    sheet_main.update(values=new_rows, range_name="A2")
    
    mail_data = []
    attachments = []
    all_match = True
    error_list = []
    
    rows_to_check = sheet_main.get_all_values()[1:] 
    
    for i, row in enumerate(rows_to_check, start=2):
        sku = row[0]
        date_status = row[13]
        
        web_prices, link, zip_f = process_sku_single(driver, sku)
        if zip_f: attachments.append(zip_f)
        
        user_prices = row[2:7]
        mismatches = []
        
        if "Not Found" in link:
             has_p = any(p and p not in ["Error","Search Fail"] for p in web_prices)
             res = "該商品未上架，但是卻有商品價格請確認!" if has_p else "該商品未上架"
        else:
            for idx, up in enumerate(user_prices):
                wp = web_prices[idx]
                if wp == "Limit Reached" and up: mismatches.append("Limit")
                elif clean_price(up) != clean_price(wp):
                    try: 
                        if abs(float(clean_price(up)) - float(clean_price(wp))) > 0.01:
                            mismatches.append("Diff")
                    except: mismatches.append("Diff")
            
            res = "均相符" if not mismatches else "; ".join(mismatches)

        if date_status: res = f"{date_status} | {res}"
        
        sheet_main.update(values=[web_prices + [get_taiwan_time_display(), res, link]], range_name=f"H{i}:O{i}")
        
        mail_data.append([sku, row[1], res, get_taiwan_time_display()])
        if "均相符" not in res and "未上架" not in res:
            all_match = False
            error_list.append(f"{sku}: {res}")
            
    print("📧 發送 Task 1 郵件...")
    has_limit = any("Limit" in str(r) for r in mail_data)
    prefix = "⚠️" if has_limit else ("✅" if all_match else "🔥")
    subject = f"{get_taiwan_time_now().strftime('%m/%d(%a)')}{prefix} [Ozio比對結果-{'警告' if has_limit else ('正常' if all_match else '異常')}]"
    if has_limit: subject += " 達購買上限/異常"
    elif all_match: subject += " 價格相符"
    else: subject += " 請檢查表格"
    
    summary = "價格相符。" if all_match else f"異常:<br>{'<br>'.join(error_list)}"
    send_email_generic(subject, summary, mail_data, attachments)
    
    for f in attachments:
        try: os.remove(f)
        except: pass

def main():
    try:
        client = connect_google_sheet()
        driver = init_driver()
        
        # === 執行 Task 1 (單商品) ===
        run_task_1(client, driver)
        
        print("\n⏳ 休息 10 秒後執行 Task 2...")
        time.sleep(10)
        
        # === 執行 Task 2 (Mix Match) ===
        run_mix_match_task(client, driver)
        
        driver.quit()
        print("\n🎉 全部任務完成！")
        
    except Exception as e:
        print(f"💥 Fatal Error: {e}")
        try: driver.quit()
        except: pass

if __name__ == "__main__":
    main()
