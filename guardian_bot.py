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

# ================= 🎛️ 控制台 (請在此切換模式) =================
# True = 壓力測試模式 (無限循環、不清除資料、往下累加)
# False = 正常運作模式 (跑一次、清除舊資料、更新欄位)
STRESS_MODE = True  

# 壓力測試時，每一輪中間休息幾秒
STRESS_WAIT_SECONDS = 60 
# ============================================================

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

def sync_promotion_data(client):
    """ 正常模式專用：清除舊資料並同步 """
    print("🔄 [正常模式] 正在從 promotion 同步資料 (清除舊資料)...")
    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        source_sheet = spreadsheet.worksheet(WORKSHEET_PROMO)
        target_sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
    except Exception as e:
        print(f"❌ 無法開啟工作表: {e}")
        return False

    all_values = source_sheet.get_all_values()
    new_rows = []
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
        
        row_data = [sku, prod_name] + user_prices + [""] * 6 + [date_status] + [""]
        new_rows.append(row_data)

    if not new_rows: return False

    print("🧹 清除舊資料...")
    current_rows = len(target_sheet.get_all_values())
    if current_rows > 1:
        target_sheet.batch_clear([f"A2:O{current_rows}"])
    
    print(f"📝 寫入 {len(new_rows)} 筆新資料...")
    end_row = 2 + len(new_rows) - 1
    target_sheet.update(values=new_rows, range_name=f"A2:O{end_row}")
    return True

def get_stress_test_data(client):
    """ 壓力測試模式專用：只讀取，不清除，回傳清單 """
    print("🔄 [壓力模式] 讀取 Promotion 資料 (不清除 Sheet)...")
    spreadsheet = client.open(SPREADSHEET_FILE_NAME)
    source_sheet = spreadsheet.worksheet(WORKSHEET_PROMO)
    
    all_values = source_sheet.get_all_values()
    data_list = []
    today = get_taiwan_time_now().date()
    
    for row in all_values[6:]:
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
             if today < d_start: date_status = f"⚠️ 尚未開始"

        data_list.append({
            "sku": sku,
            "name": prod_name,
            "user_prices": user_prices,
            "date_status": date_status
        })
    return data_list

# ================= 郵件通知功能 =================
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

def send_notification_email(all_match, error_summary, full_data, attachment_files, round_info=""):
    if not MAIL_USERNAME or not MAIL_PASSWORD: return
    print("📧 正在發送通知郵件...")
    
    has_limit_reached = False
    if full_data:
        for row in full_data:
            web_prices_slice = row[7:12] 
            if any("Limit Reached" in str(p) for p in web_prices_slice):
                has_limit_reached = True; break
    
    subject_prefix = "⚠️" if has_limit_reached else ("🔥" if not all_match else "✅")
    subject_text = "[Ozio比對結果-警告]" if has_limit_reached else ("[Ozio比對結果-異常]" if not all_match else "[Ozio比對結果-正常]")
    color = "#ff9800" if has_limit_reached else ("red" if not all_match else "green")
    
    now = get_taiwan_time_now()
    date_str = f"{now.month}/{now.day} {now.strftime('%H:%M')}"
    final_subject = f"{date_str} {subject_prefix} {subject_text} {round_info}"
    
    summary = f"發現異常：<br>{error_summary}" if error_summary else "所有商品價格比對結果均相符。"
    
    msg = MIMEMultipart()
    msg['From'] = MAIL_USERNAME
    msg['To'] = ", ".join(MAIL_RECEIVER)
    msg['Subject'] = final_subject

    html = f"<html><body><h2 style='color:{color}'>{final_subject}</h2><p>{summary}</p>{generate_html_table(full_data)}<br><a href='{SHEET_URL_FOR_MAIL}'>Google Sheet</a></body></html>"
    msg.attach(MIMEText(html, 'html'))

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
        server.starttls(); server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(msg); server.quit()
        print("✅ 郵件發送成功")
    except Exception as e: print(f"❌ 郵件發送失敗: {e}")

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
                try: float(p); has_any_price = True; break
                except: pass
        return "該商品未上架，但是卻有商品價格請確認!" if has_any_price else "該商品未上架"

    mismatches = []
    valid_comparison_count = 0
    for i in range(5):
        u_val = clean_price(user_prices[i])
        w_val = clean_price(web_prices[i])
        if w_val == "Limit Reached":
            if u_val: mismatches.append(f"Q{i+1}:Limit Reached")
            continue
        if not u_val: continue
        valid_comparison_count += 1
        try:
            if abs(float(u_val) - (float(w_val) if w_val and w_val not in ["Error", "N/A"] else -999)) >= 0.01:
                mismatches.append(f"Q{i+1}:User({u_val})!=Web({w_val})")
        except:
            if u_val != w_val: mismatches.append(f"Q{i+1}:Diff")

    if valid_comparison_count == 0: return ""
    return "均相符" if not mismatches else "; ".join(mismatches)

def connect_google_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    return gspread.authorize(creds)

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
    try:
        if "guardian.com.sg" not in driver.current_url: driver.get("https://guardian.com.sg/"); time.sleep(2)
        driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear();")
        driver.execute_script("window.sessionStorage.clear();")
        driver.refresh()
        time.sleep(3) 
    except: pass

def get_price_safely(driver):
    try:
        return clean_price(driver.find_element(By.XPATH, "//span[contains(@class, 'priceSummary-totalPrice')]").text)
    except: pass
    return None

def process_sku(driver, sku):
    # [爬蟲邏輯核心 - 您若要修改爬蟲邏輯請改這裡]
    print(f"\n🔍 開始搜尋 SKU: {sku}")
    prices = []; product_url = ""; previous_price_val = -1.0; sku_folder = str(sku)
    if os.path.exists(sku_folder): shutil.rmtree(sku_folder) 
    os.makedirs(sku_folder)
    
    try:
        driver.get(URL); time.sleep(5); handle_popups(driver)
        search_input = WebDriverWait(driver, 8).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search']")))
        driver.execute_script("arguments[0].value = '';", search_input)
        search_input.send_keys(sku); time.sleep(1); search_input.send_keys(Keys.RETURN); time.sleep(5); handle_popups(driver)

        try:
            try: link = driver.find_element(By.XPATH, f"//a[contains(@href, '{sku}')]")
            except: link = driver.find_element(By.XPATH, "(//div[contains(@class, 'product')]//a)[1]")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
            driver.execute_script("arguments[0].click();", link)
            time.sleep(3); product_url = driver.current_url
            if "search.html" in product_url: raise NoSuchElementException
        except:
            driver.save_screenshot(f"{sku_folder}/{sku}_not_found.png")
            return ["Not Found"] * 5, "URL Not Found", create_zip_evidence(sku, sku_folder)

        time.sleep(4); handle_popups(driver)
        try:
            add_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart']")))
            driver.execute_script("arguments[0].click();", add_btn)
            time.sleep(5); driver.get("https://guardian.com.sg/cart")
        except:
            driver.save_screenshot(f"{sku_folder}/{sku}_add_fail.png")
            return ["Add Fail"] * 5, product_url, create_zip_evidence(sku, sku_folder)

        time.sleep(5)
        for qty in range(1, 6):
            try: WebDriverWait(driver, 15).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')]")))
            except: pass
            
            final_price = "Error"
            for _ in range(5):
                p = get_price_safely(driver)
                if p:
                    final_price = p; driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}.png"); break
                time.sleep(2)
            
            if len(prices) < qty: prices.append(final_price)
            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    driver.execute_script("arguments[0].click();", plus_btn); time.sleep(2)
                except: break
        
        while len(prices) < 5: prices.append("Error")
        empty_cart(driver)
        return prices, product_url, create_zip_evidence(sku, sku_folder)

    except Exception as e:
        print(f"❌ 發生錯誤: {e}"); empty_cart(driver)
        return ["Error"] * 5, product_url, create_zip_evidence(sku, sku_folder)

# ================= 🚀 核心執行循環 (自動判斷模式) =================
def run_cycle(client, round_num):
    driver = init_driver()
    print(f"\n{'='*20} Round {round_num} Start ({'Stress' if STRESS_MODE else 'Normal'}) {'='*20}")
    
    try:
        # 1. 取得測試資料
        if STRESS_MODE:
            # 壓力模式：讀 Promo，不寫 Sheet，拿到 List
            data_list = get_stress_test_data(client)
        else:
            # 正常模式：Sync Promo (Clear & Write)，然後讀 Main Sheet
            if not sync_promotion_data(client): return
            sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MAIN)
            raw_data = sheet.get_all_values()
            data_list = []
            for r in raw_data[1:]:
                data_list.append({
                    "sku": safe_get(r, 0), "name": safe_get(r, 1),
                    "user_prices": [safe_get(r, 2), safe_get(r, 3), safe_get(r, 4), safe_get(r, 5), safe_get(r, 6)],
                    "date_status": safe_get(r, 13)
                })

        # 2. 準備寫入 (壓力模式：定位到最後一行並空一行)
        sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MAIN)
        if STRESS_MODE:
            sheet.append_row([f"--- Stress Test Round {round_num} ({get_taiwan_time_display()}) ---"])
            print(f"📝 已插入分隔線 (Round {round_num})")

        # 3. 執行迴圈
        results_mail = []; attachments = []; all_match = True; error_sum = []
        
        empty_cart(driver) # 初始化

        for i, item in enumerate(data_list):
            sku = item['sku']; name = item['name']
            if not sku: continue
            
            # 如果是正常模式，i 是 list index，要對應到 sheet row (header=1 + start=2 -> index+2)
            sheet_row_idx = i + 2 

            if "非檔期" in item['date_status'] or "尚未開始" in item['date_status']:
                print(f"⚠️ {sku} {item['date_status']}")

            web_prices, url, zip_f = process_sku(driver, sku)
            if zip_f: attachments.append(zip_f)

            result = compare_prices(item['user_prices'], web_prices, url)
            if item['date_status']: result = f"{item['date_status']} | {result}"
            
            update_time = get_taiwan_time_display()
            final_row_data = item['user_prices'] + web_prices + [update_time, result, url]

            # === 關鍵寫入差異 ===
            if STRESS_MODE:
                # 壓力模式：直接 Append 到最後面
                # 組合完整一行: SKU, Name, UserQ1~5, WebQ1~5, Time, Result, Link
                full_append_row = [sku, name] + final_row_data
                sheet.append_row(full_append_row)
                print(f"✅ Append: {sku} | {result}")
            else:
                # 正常模式：更新特定範圍 (H~O欄)
                # WebQ1~5 (5 cols) + Time + Result + Link = 8 cols
                # 對應到 H(col 8) ~ O(col 15)
                # 注意：web_prices (5) + time (1) + result (1) + url (1) = 8
                sheet.update(values=[web_prices + [update_time, result, url]], range_name=f"H{sheet_row_idx}:O{sheet_row_idx}")
                print(f"✅ Update: {sku} | {result}")

            # 收集 Email 資料
            results_mail.append(item['user_prices'] + web_prices + [update_time, result, url])
            # 這裡為了簡單，Email 格式可能需要根據您的需求調整，這裡只做簡單收集
            if "均相符" not in result and "該商品未上架" not in result:
                all_match = False
                error_sum.append(f"{sku}: {result}")

        # 4. 發送通知
        round_tag = f"(R{round_num})" if STRESS_MODE else ""
        error_text = "<br>".join(error_sum) if error_sum else ""
        # 為了相容原本的 send_notification_email 格式，這裡做個轉換
        # 原本 full_data 包含前7欄，這裡我們簡單重組一下給 Email 用
        mail_data = []
        for j, m in enumerate(results_mail):
            # 重組: [SKU, Name, User1...5, Web1...5, Time, Result, Link]
            # data_list[j] 有 sku/name
            # m 有 user/web/time/result
            full_row = [data_list[j]['sku'], data_list[j]['name']] + m
            mail_data.append(full_row)

        send_notification_email(all_match, error_text, mail_data, attachments, round_tag)

        # 5. 清理檔案
        for f in attachments:
            try: os.remove(f)
            except: pass

    finally:
        driver.quit()

def main():
    round_count = 1
    client = connect_google_sheet()
    
    if STRESS_MODE:
        print("🔥 壓力測試模式啟動 (無限循環)... 按 Ctrl+C 停止")
        try:
            while True:
                run_cycle(client, round_count)
                print(f"⏳ 休息 {STRESS_WAIT_SECONDS} 秒...")
                time.sleep(STRESS_WAIT_SECONDS)
                round_count += 1
        except KeyboardInterrupt:
            print("\n👋 測試停止")
    else:
        print("🟢 正常執行模式 (跑一次)...")
        run_cycle(client, 1)
        print("🎉 執行結束")

if __name__ == "__main__":
    main()
