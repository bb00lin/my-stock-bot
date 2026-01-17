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
WORKSHEET_MAIN = '工作表1' 
WORKSHEET_MIX = 'Mix_Match_Check' 
WORKSHEET_PROMO = 'promotion'

# [已修正] 您的正確 Google Sheet 網址
SHEET_URL_FOR_MAIL = "https://docs.google.com/spreadsheets/d/1pqa6DU-qo3lR84QYgpoiwGE7tO-QSY2-kC_ecf868cY/edit?gid=1727836519#gid=1727836519"

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
    print("🧹 正在清空購物車...")
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
    print(f"   ➕ 加入商品: {sku} (單次加入)")
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
            try: link = driver.find_element(By.XPATH, xpath_sku)
            except: link = driver.find_element(By.XPATH, xpath_generic)
            driver.execute_script("arguments[0].click();", link)
        except:
            print(f"      ❌ 找不到商品 {sku}")
            return False
        time.sleep(3)
        try:
            add_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart")))
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

# ================= Task 2: Mix & Match 邏輯 =================
def sync_mix_match_data(client):
    print("🔄 同步 Mix & Match 資料...")
    promo_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_PROMO)
    try: mix_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    except: mix_sheet = client.open(SPREADSHEET_FILE_NAME).add_worksheet(title=WORKSHEET_MIX, rows=100, cols=20)
    mix_sheet.clear()
    headers = ["Main SKU", "Product Name", "Promo Rule", "Target Qty", "Mix Strategy", "Expected Price", "Web Total Price", "Result", "Update Time", "Main Link"]
    rows = promo_sheet.get_all_values()
    new_data = [headers]
    today = get_taiwan_time_now().date()
    for row in rows[6:]:
        desc = safe_get(row, 6) 
        if "Mix & Match" in desc:
            start_str = safe_get(row, 8); end_str = safe_get(row, 9)
            d_start = parse_date(start_str); d_end = parse_date(end_str)
            is_valid_date = True; date_note = ""
            if d_start and d_end and not (d_start <= today <= d_end):
                is_valid_date = False; date_note = f"⚠️主商品非上架期間 ({d_start.strftime('%m/%d')}~{d_end.strftime('%m/%d')})"
            elif d_start and not d_end and today < d_start:
                is_valid_date = False; date_note = f"⚠️主商品非上架期間 (尚未開始)"
            main_sku = safe_get(row, 11).replace("'", "").strip()
            if len(main_sku) > 6: main_sku = main_sku[-6:]
            prod_name = safe_get(row, 12); matches = re.findall(r'(\d+)\s+[Ff]or\s*\$?([\d\.]+)', desc)
            if not is_valid_date:
                new_data.append([main_sku, prod_name, desc[:15], "", "", "", "", date_note, "", ""]); continue 
            partners = []
            match_partners = re.search(r'Mix & Match\s*([\d,]+)', desc)
            if match_partners:
                for p in match_partners.group(1).split(','):
                    p = p.strip()
                    if len(p) > 6: p = p[-6:]
                    if p != main_sku: partners.append(p)
            if not partners or not matches: continue 
            price_map = {int(q): float(p) for q, p in matches}
            best_unit_price = min([p/q for q, p in price_map.items()])
            pool = [main_sku] + partners
            for target_qty in range(2, 6):
                expected_price = price_map[target_qty] if target_qty in price_map else int(best_unit_price * target_qty * 10) / 10.0
                rule_text = f"{target_qty} For ${expected_price}" if target_qty in price_map else f"Calculated (Unit: {best_unit_price:.2f})"
                c = cycle(pool); s_dict = {}
                for _ in range(target_qty): item = next(c); s_dict[item] = s_dict.get(item, 0) + 1
                strategy_str = "; ".join([f"{k}:{v}" for k, v in s_dict.items()])
                new_data.append([main_sku, prod_name, rule_text, target_qty, strategy_str, str(expected_price), "", "", "", ""])
    mix_sheet.update(values=new_data, range_name="A1")
    return len(new_data)-1

def process_mix_case_dynamic(driver, strategy_str, target_total_qty, main_sku):
    empty_cart(driver); folder_name = "mix_temp"
    if not os.path.exists(folder_name): os.makedirs(folder_name)
    planned_skus = [item.split(':')[0].strip() for item in strategy_str.split(';')]
    available_skus = []; missing_skus = []
    print(f"   🕵️ 檢查商品庫存...")
    if not check_item_exists(driver, main_sku):
        return "Main Missing", "URL Not Found", None, [main_sku], strategy_str
    available_skus.append(main_sku)
    for sku in planned_skus:
        if sku == main_sku: continue 
        if check_item_exists(driver, sku): available_skus.append(sku)
        else: missing_skus.append(sku)
    if len(available_skus) == 1 and len(planned_skus) > 1:
        return "Only Main", "", None, missing_skus, "; ".join([f"{s}:1" if s==main_sku else f"{s}:0" for s in planned_skus])
    
    final_strategy = {sku: 0 for sku in planned_skus}; final_strategy[main_sku] = 1; current_count = 1
    partners_pool = [s for s in available_skus if s != main_sku]
    fill_pool = partners_pool if partners_pool else [main_sku]
    pool_cycle = cycle(fill_pool)
    while current_count < target_total_qty:
        next_item = next(pool_cycle); final_strategy[next_item] = final_strategy.get(next_item, 0) + 1; current_count += 1
    actual_strategy = "; ".join([f"{s}:{final_strategy[s]}" for s in planned_skus])
    
    items_to_add = []
    for s, q in final_strategy.items():
        for _ in range(q): items_to_add.append(s)
    empty_cart(driver); main_url = ""
    for sku in items_to_add:
        success = add_single_item_to_cart(driver, sku, 1)
        if not success: return "Add Fail", "", None, missing_skus, actual_strategy
        if not main_url and sku == main_sku: main_url = driver.current_url
    
    driver.get("https://guardian.com.sg/cart")
    print("   ⏳ [極致等待] 等待購物車計算 (Fetching Cart)...")
    try:
        WebDriverWait(driver, 60).until(
            EC.invisibility_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')] | //div[contains(@class, 'loading-mask')] | //div[contains(@class, 'loader')]"))
        )
    except: print("   ⚠️ 等待超時，嘗試強制抓取")
    time.sleep(5) 
    
    total_price = "Error"
    for retry in range(10):
        price = get_total_price_safely(driver)
        if price and price != "Error" and len(price) > 0:
            total_price = price; break
        print(f"   ⚠️ 尚未抓到價格，重試 ({retry+1}/10)...")
        time.sleep(3)
    driver.save_screenshot(f"{folder_name}/Mix_{main_sku}_Total.png")
    return total_price, main_url, create_zip_evidence("Mix_Evidence", folder_name), missing_skus, actual_strategy

def run_mix_match_task(client, driver):
    row_count = sync_mix_match_data(client)
    sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    all_values = sheet.get_all_values()
    results_for_mail = []; attachments = []; all_match = True; error_summary = []

    for i, row in enumerate(all_values[1:], start=2):
        main_sku = row[0]; pre_result = safe_get(row, 7)
        if "主商品非上架期間" in pre_result:
            results_for_mail.append([main_sku, row[1], pre_result, get_taiwan_time_display()]); continue
        
        target_qty = int(row[3]); expected = float(row[5])
        print(f"\n🚀 測試: {main_sku} Qty:{target_qty}")
        web_total, link, zip_file, m_list, actual_s = process_mix_case_dynamic(driver, row[4], target_qty, main_sku)
        sheet.update_cell(i, 5, actual_s) 
        
        missing_note = f" (⚠️缺: {','.join(m_list)})" if m_list else ""
        is_error = False; result_text = ""
        
        if web_total == "All Missing": result_text = "⚠️全部商品尚未上架"
        elif web_total == "Main Missing": result_text = f"⚠️主商品尚未上架: {main_sku}"
        elif web_total == "Only Main": result_text = f"⚠️MIX全缺: 只剩主料 (忽略比較)"
        elif "Fail" in web_total or "Error" in web_total:
            result_text = f"🔥 錯誤 ({web_total}){missing_note}"; is_error = True
        else:
            try:
                web_val = float(web_total)
                if abs(web_val - expected) < 0.05: result_text = f"✅ 相符{missing_note}"
                else: result_text = f"🔥 差異 (Exp:{expected} != Web:{web_val}){missing_note}"; is_error = True
            except: result_text = f"🔥 錯誤 ({web_total}){missing_note}"; is_error = True

        if is_error:
            all_match = False; error_summary.append(f"{main_sku}: {result_text}")
            if zip_file: attachments.append(zip_file)
        
        update_time = get_taiwan_time_display()
        sheet.update(values=[[web_total, result_text, update_time, link]], range_name=f"G{i}:J{i}")
        results_for_mail.append([main_sku, row[1], result_text, update_time])

    # 寄信
    subject = f"{get_taiwan_time_now().strftime('%m/%d')}{'✅' if all_match else '🔥'}[壓力測試結果]"
    send_email_generic(subject, "壓力測試報告", results_for_mail, attachments)

def send_email_generic(subject, summary, data_rows, attachments):
    if not MAIL_USERNAME or not MAIL_PASSWORD: return
    msg = MIMEMultipart(); msg['From'] = MAIL_USERNAME; msg['To'] = ", ".join(MAIL_RECEIVER); msg['Subject'] = subject
    table_html = "<table border='1' style='border-collapse:collapse;width:100%'><tr style='background:#f2f2f2'><th>SKU</th><th>商品</th><th>結果</th><th>時間</th></tr>"
    for r in data_rows:
        bg = "#ffebee" if "🔥" in r[2] else ("#fff3e0" if "⚠️" in r[2] else "#fff")
        table_html += f"<tr style='background:{bg}'><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"
    table_html += "</table>"
    html = f"<html><body><h2>{subject}</h2><p>{summary}</p>{table_html}<br><a href='{SHEET_URL_FOR_MAIL}'>查看表格</a></body></html>"
    msg.attach(MIMEText(html, 'html'))
    for f in attachments:
        with open(f, 'rb') as fp: part = MIMEApplication(fp.read(), Name=os.path.basename(f))
        part['Content-Disposition'] = f'attachment; filename="{os.path.basename(f)}"'
        msg.attach(part)
    try:
        s = smtplib.SMTP('smtp.gmail.com', 587); s.starttls(); s.login(MAIL_USERNAME, MAIL_PASSWORD)
        s.send_message(msg); s.quit(); print(f"📧 郵件發送成功")
    except Exception as e: print(f"❌ 寄信失敗: {e}")

# ================= 壓力測試主迴圈 =================
def main():
    round_count = 1
    client = connect_google_sheet()
    
    print("🔥 壓力測試模式啟動！程式將不斷循環執行...")
    print("🛑 若要停止測試，請在終端機按下 Ctrl + C")
    
    try:
        while True:
            print(f"\n{'='*20} 開始第 {round_count} 輪測試 {'='*20}")
            driver = None
            try:
                driver = init_driver()
                run_mix_match_task(client, driver)
                print(f"✅ 第 {round_count} 輪測試完成。")
            except Exception as e:
                print(f"⚠️ 這一輪發生錯誤: {e}")
            finally:
                if driver: 
                    driver.quit()
                    print("🧹 瀏覽器資源已回收。")
            
            round_count += 1
            wait_time = 30
            print(f"⏳ 冷卻中，{wait_time} 秒後開始下一輪...")
            time.sleep(wait_time)
            
    except KeyboardInterrupt:
        print("\n👋 收到停止指令，壓力測試結束。")
    except Exception as e:
        print(f"💥 發生重大錯誤: {e}")

if __name__ == "__main__":
    main()
