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

# ================= 資料解析功能 =================
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

def get_target_skus(client):
    """ 
    壓力測試專用：只讀取 Promotion 表格來決定要跑哪些 SKU，
    不執行任何清除或寫入動作。
    """
    print("🔄 [壓力測試] 讀取 Promotion 清單...")
    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        source_sheet = spreadsheet.worksheet(WORKSHEET_PROMO)
    except Exception as e:
        print(f"❌ 無法開啟工作表: {e}")
        return []

    all_values = source_sheet.get_all_values()
    target_list = []
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
        
        target_list.append({
            "sku": sku,
            "name": prod_name,
            "user_prices": user_prices,
            "date_status": date_status
        })
    return target_list

# ================= 郵件通知功能 =================
# (省略 generate_html_table 以節省篇幅，功能不變)
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
        table_html += f"<tr style='background-color: {bg_color};'><td style='padding: 8px;'>{sku}</td><td style='padding: 8px;'>{name}</td><td style='padding: 8px;'>{result}</td><td style='padding: 8px;'>{time_str}</td></tr>"
    table_html += "</table>"
    return table_html

def send_notification_email(all_match, error_summary, full_data, attachment_files, round_num):
    if not MAIL_USERNAME or not MAIL_PASSWORD: return
    print("📧 正在發送通知郵件...")
    
    subject_prefix = "✅" if all_match else "🔥"
    subject_text = f"[Ozio壓力測試 R{round_num}]"
    
    now = get_taiwan_time_now()
    final_subject = f"{now.strftime('%m/%d %H:%M')} {subject_prefix} {subject_text}"
    snapshot_table = generate_html_table(full_data)

    msg = MIMEMultipart()
    msg['From'] = MAIL_USERNAME
    msg['To'] = ", ".join(MAIL_RECEIVER)
    msg['Subject'] = final_subject

    html = f"<html><body><h2>{final_subject}</h2><p>{error_summary}</p>{snapshot_table}</body></html>"
    msg.attach(MIMEText(html, 'html'))

    # 夾帶附件 (略過細節保持原樣)
    if attachment_files:
        for fpath in attachment_files:
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
        print("✅ 郵件發送成功")
    except Exception as e: print(f"❌ 郵件發送失敗: {e}")

# ================= 核心邏輯 =================
def validate_user_inputs(user_prices):
    clean_prices = [clean_price(p) for p in user_prices]
    if all(not p for p in clean_prices): return "異常:User價格全空"
    return None

def compare_prices(user_prices, web_prices, product_url):
    user_validation_error = validate_user_inputs(user_prices)
    if user_validation_error: return user_validation_error

    if "Not Found" in product_url:
        return "該商品未上架"

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
            if u_val != w_val: mismatches.append(f"Q{i+1}:Diff")

    if valid_comparison_count == 0: return ""
    if not mismatches: return "均相符"
    else: return "; ".join(mismatches)

def connect_google_sheet():
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
        popups = ["button[aria-label='Close']", "div.close-popup", "#onetrust-accept-btn-handler"]
        for p in popups:
            try:
                elem = driver.find_element(By.CSS_SELECTOR, p)
                if elem.is_displayed(): driver.execute_script("arguments[0].click();", elem); time.sleep(1)
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

# ★★★★★ 關鍵修正：修復價格重複提取的問題 ★★★★★
def process_sku(driver, sku):
    print(f"\n🔍 開始搜尋 SKU: {sku}")
    prices = [] 
    product_url = "" 
    
    # 關鍵變數：用來比對價格是否變動
    previous_price_val = -1.0 
    
    sku_folder = str(sku)
    if os.path.exists(sku_folder): shutil.rmtree(sku_folder) 
    os.makedirs(sku_folder)
    
    generated_zip = None

    try:
        driver.get(URL)
        time.sleep(5)
        handle_popups(driver)

        # 搜尋邏輯
        search_input = WebDriverWait(driver, 8).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search']")))
        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku)
        time.sleep(1)
        search_input.send_keys(Keys.RETURN)
        time.sleep(5)
        handle_popups(driver)

        try:
            xpath_sku = f"//a[contains(@href, '{sku}')]"
            try:
                link = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath_sku)))
            except:
                link = driver.find_element(By.XPATH, "(//div[contains(@class, 'product')]//a)[1]")
            
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
            driver.execute_script("arguments[0].click();", link)
            time.sleep(3)
            product_url = driver.current_url
            if "search.html" in product_url: raise NoSuchElementException
        except:
            driver.save_screenshot(f"{sku_folder}/{sku}_not_found.png")
            generated_zip = create_zip_evidence(sku, sku_folder)
            return ["Not Found"] * 5, "URL Not Found", generated_zip

        time.sleep(4)
        handle_popups(driver)

        # 加入購物車 (Qty 1)
        try:
            add_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart")))
            driver.execute_script("arguments[0].click();", add_btn)
            print("🛒 已點擊加入購物車 (Qty 1)")
            time.sleep(5) 
            driver.get("https://guardian.com.sg/cart")
        except:
            driver.save_screenshot(f"{sku_folder}/{sku}_add_fail.png")
            generated_zip = create_zip_evidence(sku, sku_folder)
            return ["Add Fail"] * 5, product_url, generated_zip

        time.sleep(5)

        # === 迴圈提取價格 (含防重複機制) ===
        for qty in range(1, 6):
            
            # 等待轉圈圈消失
            try: WebDriverWait(driver, 20).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')]")))
            except: pass
            
            final_price = "Error"
            max_retries = 15 # 增加重試次數
            
            for attempt in range(max_retries):
                current_price_str = get_price_safely(driver)
                is_valid = False
                current_val = -1.0

                if current_price_str:
                    try:
                        current_val = float(current_price_str)
                        
                        # ★★★ 關鍵邏輯：驗證價格是否改變 ★★★
                        if qty == 1:
                            is_valid = True # 第一個一定有效
                        else:
                            # 必須跟上一個價格不同 (通常是變大)
                            # 為了保險，加上一點容錯 (大於 0.1)
                            if current_val > previous_price_val + 0.1:
                                is_valid = True
                            else:
                                # 如果價格沒變，印出 Log 方便除錯
                                print(f"   ⏳ 價格尚未更新 (Qty {qty}): 目前 {current_val} == 上次 {previous_price_val}，重試中...")
                    except: is_valid = False
                
                if is_valid:
                    final_price = current_price_str
                    previous_price_val = current_val # 更新基準價格
                    print(f"   💰 數量 {qty}: SGD {final_price}")
                    driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}.png")
                    break
                else:
                    time.sleep(2) # 等待網頁刷新
            
            # 檢查是否達上限
            if final_price == "Error":
                 try:
                    error_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')]")
                    if error_msg.is_displayed():
                         print("   🛑 達到購買上限")
                         for _ in range(qty, 6): prices.append("Limit Reached")
                         break 
                 except: pass

            if len(prices) < qty:
                prices.append(final_price)

            # 點擊增加按鈕 (為下一輪做準備)
            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    driver.execute_script("arguments[0].click();", plus_btn)
                    print(f"   ➕ 點擊增加數量 -> {qty + 1}")
                    time.sleep(2) # 給網頁一點反應時間
                except Exception: break
        
        while len(prices) < 5: prices.append("Error")
        empty_cart(driver)

        generated_zip = create_zip_evidence(sku, sku_folder)
        return prices, product_url, generated_zip

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        try: empty_cart(driver)
        except: pass
        return ["Error"] * 5, product_url, generated_zip

# ================= 主程式 (壓力測試迴圈) =================
def run_stress_cycle(client, round_num):
    driver = init_driver()
    print(f"--- Round {round_num} Initialization ---")
    empty_cart(driver)
    
    # 1. 取得測試目標 (只讀不寫)
    target_list = get_target_skus(client)
    
    # 2. 準備寫入 (在最下方插入分隔線)
    sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MAIN)
    sheet.append_row([f"--- Round {round_num} Start ---"] + [""]*14)
    
    overall_status_match = True
    error_summary_list = []
    full_data_for_mail = []
    attachment_files = []

    print(f"📋 Round {round_num}: 共有 {len(target_list)} 筆資料待處理")

    for item in target_list:
        sku = item['sku']
        
        if "非檔期" in item['date_status'] or "尚未開始" in item['date_status']:
            print(f"⚠️ SKU {sku} {item['date_status']}")

        # 執行爬蟲
        web_prices, product_url, zip_file = process_sku(driver, sku)
        if zip_file: attachment_files.append(zip_file)

        update_time = get_taiwan_time_display()
        comparison_result = compare_prices(item['user_prices'], web_prices, product_url)
        
        if item['date_status']:
            comparison_result = f"{item['date_status']} | {comparison_result}"

        # 組合資料並累加寫入
        row_data = [sku, item['name']] + item['user_prices'] + web_prices + [update_time, comparison_result, product_url]
        sheet.append_row(row_data)
        
        print(f"✅ SKU {sku} 完成 | 結果: {comparison_result}")
        print("-" * 30)

        if "均相符" not in comparison_result and "該商品未上架" not in comparison_result:
            overall_status_match = False
            error_summary_list.append(f"SKU {sku}: {comparison_result}")
        
        full_data_for_mail.append(row_data)

    driver.quit()
    
    error_text = "<br>".join(error_summary_list) if error_summary_list else ""
    send_notification_email(overall_status_match, error_text, full_data_for_mail, attachment_files, round_num)
    
    print("🧹 清理本輪暫存檔...")
    for f in attachment_files:
        try: os.remove(f)
        except: pass

def main():
    client = connect_google_sheet()
    round_count = 1
    
    print("🔥 壓力測試模式啟動 (無限循環 + 價格防呆檢查 + 累加記錄)")
    print("🛑 若要停止，請按 Ctrl + C")
    
    try:
        while True:
            print(f"\n{'='*30}\n開始第 {round_count} 輪循環測試\n{'='*30}")
            run_stress_cycle(client, round_count)
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
