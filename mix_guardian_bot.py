import time
import gspread
import re
import os
import shutil
import smtplib
import math
import json
from itertools import cycle, combinations_with_replacement  # [修改] 新增 combinations_with_replacement
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

# 請確認此網址正確
SHEET_URL_FOR_MAIL = "https://docs.google.com/spreadsheets/d/1pqa6DU-qo3lR84QYgpoiwGE7tO-QSY2-kC_ecf868cY/edit?gid=1727836519#gid=1727836519"

URL = "https://guardian.com.sg/"

# [修改] 測試方案選擇
# 'A': 基本模式 (每個數量只測 1 種平均分配) -> 速度快，省時間
# 'B': 極端模式 (每個數量測 2 種：平均 + 集中於單一贈品) -> 測試庫存極限 (推薦)
# 'C': 全組合模式 (窮舉所有可能的排列組合) -> 測試最完整，但耗時極長 (慎用)
TEST_PLAN = 'C'

# Email 設定 (從 Secrets 讀取)
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

def get_filename_time_prefix():
    # 檔名專用時間格式 (避免冒號)
    return get_taiwan_time_now().strftime("%Y-%m-%d_%H-%M")

def get_folder_date_prefix():
    # 資料夾專用日期格式
    return get_taiwan_time_now().strftime("%Y-%m-%d")

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
        
        # Zip 檔名加上詳細日期時間
        ts = get_filename_time_prefix()
        zip_filename_base = f"{ts}_{sku}"
        
        zip_path = shutil.make_archive(zip_filename_base, 'zip', sku_folder)
        shutil.rmtree(sku_folder) 
        return zip_path
    except: return None

# ================= Google Sheet 連線與格式化 =================
def connect_google_sheet():
    print("📊 正在連線 Google Sheet (使用 Secrets)...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 環境變數！")
        return None

    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ 解析金鑰或連線失敗: {e}")
        return None

def format_group_colors(sheet, data_rows):
    """
    根據 Main SKU (第1欄) 為 Google Sheet 添加交替顏色
    """
    print("🎨 正在為表格上色 (依主商品分組)...")
    
    COLOR_1 = {"red": 1.0, "green": 1.0, "blue": 1.0}      # 白色
    COLOR_2 = {"red": 0.9, "green": 0.9, "blue": 0.9}      # 淺灰色
    
    requests = []
    start_row_index = 1 
    
    if len(data_rows) < 2:
        return

    current_sku = ""
    current_color_idx = 0
    colors = [COLOR_1, COLOR_2]
    
    for i, row in enumerate(data_rows[1:]):
        sku = safe_get(row, 0)
        
        if sku != current_sku:
            current_sku = sku
            current_color_idx = (current_color_idx + 1) % 2
        
        bg_color = colors[current_color_idx]
        
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet.id,
                    "startRowIndex": start_row_index + i,
                    "endRowIndex": start_row_index + i + 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 10 
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": bg_color
                    }
                },
                "fields": "userEnteredFormat.backgroundColor"
            }
        })

    try:
        if requests:
            sheet.spreadsheet.batch_update({"requests": requests})
            print("✅ 表格上色完成")
    except Exception as e:
        print(f"⚠️ 表格上色失敗 (API錯誤): {e}")

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

# ================= Task 2: Mix & Match =================
def sync_mix_match_data(client):
    print(f"🔄 [Task 2] 同步 Mix & Match 資料 (擴充 Qty 2~5 | 模式: {TEST_PLAN})...")
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
            
            is_valid_date = True
            date_note = ""
            if d_start and d_end and not (d_start <= today <= d_end):
                is_valid_date = False
                date_note = f"⚠️主商品非上架期間 ({d_start.strftime('%m/%d')}~{d_end.strftime('%m/%d')})"
            elif d_start and not d_end and today < d_start:
                is_valid_date = False
                date_note = f"⚠️主商品非上架期間 (尚未開始)"

            main_sku = safe_get(row, 11).replace("'", "").strip()
            if len(main_sku) > 6: main_sku = main_sku[-6:]
            prod_name = safe_get(row, 12)

            matches = re.findall(r'(\d+)\s+[Ff]or\s*\$?([\d\.]+)', desc)
            rule_text_display = desc[:20] + "..." if len(desc)>20 else desc
            if matches:
                rule_text_display = f"{matches[-1][0]} For ${matches[-1][1]}"

            if not is_valid_date:
                row_data = [main_sku, prod_name, rule_text_display, "", "", "", "", date_note, "", ""]
                new_data.append(row_data)
                continue 

            partners = []
            match_partners = re.search(r'Mix & Match\s*([\d,]+)', desc)
            if match_partners:
                raw_partners = match_partners.group(1).split(',')
                for p in raw_partners:
                    p = p.strip()
                    if len(p) > 6: p = p[-6:]
                    if p != main_sku: partners.append(p)
            
            if not partners: continue 
            if not matches: continue
            
            price_map = {}
            for q_str, p_str in matches:
                try: price_map[int(q_str)] = float(p_str)
                except: continue
            if not price_map: continue

            best_unit_price = min([p/q for q, p in price_map.items()])
            pool = [main_sku] + partners
            
            for target_qty in range(2, 6):
                expected_price = 0.0
                rule_text = ""
                
                if target_qty in price_map:
                    expected_price = price_map[target_qty]
                    rule_text = f"{target_qty} For ${expected_price}"
                else:
                    raw_total = best_unit_price * target_qty
                    expected_price = int(raw_total * 10) / 10.0
                    rule_text = f"Calculated (Unit: {best_unit_price:.2f})"

                # === [新增] 根據 TEST_PLAN 產生不同策略組合 ===
                strategies_list = []
                
                if TEST_PLAN == 'C':
                    # === Plan C: 全組合 (Exhaustive) ===
                    # 規則: 主商品固定 1 個，剩餘 (Target-1) 個位置從 Pool 中任選
                    slots_to_fill = target_qty - 1
                    if slots_to_fill > 0:
                        combos = combinations_with_replacement(pool, slots_to_fill)
                        for combo in combos:
                            strat_c = {main_sku: 1} # 固定主商品
                            for item in combo:
                                strat_c[item] = strat_c.get(item, 0) + 1
                            strategies_list.append(strat_c)
                    else:
                        # 如果 Target=1 (理論上不會跑到這，因為 loop 從 2 開始)，就只有主商品
                        strategies_list.append({main_sku: 1})
                        
                else:
                    # === Plan A / B ===
                    
                    # 策略 1: 平均分配 (Plan A 基本款)
                    current_cycle = cycle(pool)
                    strat_avg = {}
                    for _ in range(target_qty):
                        item = next(current_cycle)
                        strat_avg[item] = strat_avg.get(item, 0) + 1
                    strategies_list.append(strat_avg)
                    
                    # 策略 2: 集中分配 (Plan B 加強款)
                    if TEST_PLAN == 'B' and partners:
                        strat_conc = {main_sku: 1}
                        remaining = target_qty - 1
                        target_p = partners[0]
                        strat_conc[target_p] = strat_conc.get(target_p, 0) + remaining
                        
                        if strat_conc != strat_avg:
                            strategies_list.append(strat_conc)
                
                # 將所有策略寫入清單
                for strat in strategies_list:
                    strategy_str = "; ".join([f"{k}:{v}" for k, v in strat.items()])
                    row_data = [main_sku, prod_name, rule_text, target_qty, strategy_str, str(expected_price), "", "", "", ""]
                    new_data.append(row_data)

    mix_sheet.update(values=new_data, range_name="A1")
    
    # 初始上色
    format_group_colors(mix_sheet, new_data)
    
    print(f"✅ [Task 2] 已生成 {len(new_data)-1} 筆混搭測試案例 (模式 {TEST_PLAN})")
    return len(new_data)-1

def process_mix_case_dynamic(driver, strategy_str, target_total_qty, main_sku):
    empty_cart(driver)
    
    raw_items = strategy_str.split(';')
    unique_skus_planned = []
    for item in raw_items:
        s = item.split(':')[0].strip()
        if s not in unique_skus_planned: unique_skus_planned.append(s)
        
    date_prefix = get_folder_date_prefix()
    folder_name = f"{date_prefix}_mix_{main_sku}"
    
    if not os.path.exists(folder_name): os.makedirs(folder_name)
    
    ts_file = get_filename_time_prefix()
    
    available_skus = []
    missing_skus = [] 
    
    print(f"   🕵️ 正在檢查商品庫存狀況...")
    
    if not check_item_exists(driver, main_sku):
        print(f"   🛑 主商品 {main_sku} 搜尋不到")
        return "Main Missing", "URL Not Found", None, [main_sku], strategy_str
    
    available_skus.append(main_sku)
    
    for sku in unique_skus_planned:
        if sku == main_sku: continue 
        if check_item_exists(driver, sku):
            available_skus.append(sku)
        else:
            print(f"   ⚠️ 混搭商品 {sku} 搜尋不到，將移除")
            missing_skus.append(sku)
    
    if len(available_skus) == 1 and len(unique_skus_planned) > 1:
        print(f"   🛑 所有 MIX 商品皆從缺，只剩主料，停止比較")
        final_display_parts = []
        for s in unique_skus_planned:
            if s == main_sku: final_display_parts.append(f"{s}:1")
            else: final_display_parts.append(f"{s}:0")
        final_display_str = "; ".join(final_display_parts)
        
        return "Only Main", "", None, missing_skus, final_display_str

    final_strategy = {sku: 0 for sku in unique_skus_planned} 
    
    # 解析 strategy_str 內的數量設定
    for item in raw_items:
        parts = item.split(':')
        s_code = parts[0].strip()
        s_qty = int(parts[1].strip())
        
        if s_code in available_skus:
            final_strategy[s_code] = s_qty
        else:
            final_strategy[s_code] = 0

    final_display_parts = []
    for s in unique_skus_planned:
        qty = final_strategy.get(s, 0)
        final_display_parts.append(f"{s}:{qty}")
    final_display_str = "; ".join(final_display_parts)
    
    print(f"   🔄 實際執行策略: {final_display_str}")

    items_to_add = []
    for sku, qty in final_strategy.items():
        for _ in range(qty):
            items_to_add.append(sku)
            
    empty_cart(driver)
    main_url = ""
    
    for sku in items_to_add:
        success = add_single_item_to_cart(driver, sku, 1)
        if not success:
            driver.save_screenshot(f"{folder_name}/{ts_file}_Add_Fail_{sku}.png")
            zip_path = create_zip_evidence(f"Mix_Error_{main_sku}", folder_name)
            return "Add Fail", "", zip_path, missing_skus, final_display_str
        
        if not main_url and sku == main_sku: main_url = driver.current_url

    driver.get("https://guardian.com.sg/cart")
    
    print("   ⏳ 等待購物車計算 (Fetching Cart)...")
    try:
        WebDriverWait(driver, 20).until_not(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')] | //div[contains(@class, 'loading-mask')]"))
        )
    except TimeoutException:
        print("   ⚠️ 等待購物車載入超時")
    
    time.sleep(2) 
    
    # === 強制等待 6 秒 ===
    try:
        print("   ⏳ 等待 6 秒讓 Side Cart/Notification 彈窗完全消失...")
        time.sleep(6) 
        
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        driver.execute_script("arguments[0].click();", body)
        time.sleep(1)
    except: pass
    # =====================
    
    total_price = "Error"
    for retry in range(5):
        price = get_total_price_safely(driver)
        if price and price != "Error":
            total_price = price
            break
        print(f"   ⚠️ 尚未抓到價格，重試 ({retry+1}/5)...")
        time.sleep(2)
        
    if not total_price: total_price = "Error"
    
    screenshot_name = f"{ts_file}_Mix_{main_sku}_Total.png"
    driver.save_screenshot(f"{folder_name}/{screenshot_name}")
    
    zip_path = create_zip_evidence(f"Mix_{main_sku}", folder_name)
    
    return total_price, main_url, zip_path, missing_skus, final_display_str

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
        pre_result = safe_get(row, 7)
        
        if "主商品非上架期間" in pre_result:
            print(f"   ⚠️ {main_sku}: 非上架期間，跳過")
            sheet.update_cell(i, 9, get_taiwan_time_display()) 
            results_for_mail.append([main_sku, row[1], pre_result, get_taiwan_time_display()])
            continue

        original_strategy = row[4]
        target_qty = int(row[3])
        expected = float(row[5])
        
        print(f"   🧪 測試: {main_sku} Qty:{target_qty} (預期 ${expected})")
        
        web_total, link, zip_file, missing_list, actual_strategy = process_mix_case_dynamic(driver, original_strategy, target_qty, main_sku)
        
        sheet.update_cell(i, 5, actual_strategy) 

        missing_note = ""
        if missing_list: missing_note = f" (⚠️缺: {','.join(missing_list)})"
        
        is_error = False
        result_text = ""
        
        if web_total == "All Missing":
            result_text = "⚠️全部商品尚未上架"
            is_error = False
        
        elif web_total == "Main Missing":
            result_text = f"⚠️主商品尚未上架: {main_sku}"
            is_error = False 
            
        elif web_total == "Only Main":
            result_text = f"⚠️MIX全缺: 只剩主料 (忽略比較)"
            is_error = False
            
        elif "Fail" in web_total or "Error" in web_total:
            result_text = f"🔥 錯誤 ({web_total}){missing_note}"
            is_error = True
        else:
            try:
                web_val = float(web_total)
                if abs(web_val - expected) < 0.05:
                    result_text = f"✅ 相符{missing_note}"
                else:
                    result_text = f"🔥 差異 (Exp:{expected} != Web:{web_val}){missing_note}"
                    is_error = True
            except:
                result_text = f"🔥 錯誤 ({web_total}){missing_note}"
                is_error = True

        if is_error:
            all_match = False
            error_summary.append(f"{main_sku} (Qty{target_qty}): {result_text}")
        
        if zip_file: attachments.append(zip_file)

        update_time = get_taiwan_time_display()
        sheet.update(values=[[web_total, result_text, update_time, link]], range_name=f"G{i}:J{i}")
        results_for_mail.append([main_sku, row[1], result_text, update_time])

    format_group_colors(sheet, all_values)

    subject_prefix = "✅" if all_match else "🔥"
    date_info = f"{get_taiwan_time_now().strftime('%m/%d(%a)')}"
    subject = f"{date_info}{subject_prefix}[Ozio Mix & Match比對結果]"
    
    summary_text = "所有混搭組合價格均相符。" if all_match else f"發現混搭價格異常。<br>{'<br>'.join(error_summary)}"
    if any("⚠️缺" in str(r) for r in results_for_mail):
        summary_text += "<br>(註：部分結果含有缺貨商品遞補標記)"
    
    send_email_generic(subject, summary_text, results_for_mail, attachments)

def send_email_generic(subject, summary, data_rows, attachments):
    if not MAIL_USERNAME or not MAIL_PASSWORD: return

    table_html = "<table border='1' style='border-collapse:collapse;width:100%'>"
    table_html += "<tr style='background:#f2f2f2'><th>SKU</th><th>商品</th><th>結果</th><th>時間</th></tr>"
    
    current_sku = ""
    current_color_idx = 0
    colors = ["#ffffff", "#f0f0f0"] 
    
    for r in data_rows:
        sku = r[0]
        result = r[2]
        
        if sku != current_sku:
            current_sku = sku
            current_color_idx = (current_color_idx + 1) % 2
        
        base_bg = colors[current_color_idx]
        
        if "🔥" in result or "Diff" in result or "Error" in result:
            final_bg = "#ffebee" 
        elif "⚠️" in result:
            final_bg = "#fff3e0"
        else:
            final_bg = base_bg
        
        table_html += f"<tr style='background:{final_bg}'><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"
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
        
        run_mix_match_task(client, driver)
        
        driver.quit()
        print("\n🎉 Mix & Match 任務完成！")
        
    except Exception as e:
        print(f"💥 Fatal Error: {e}")
        try: driver.quit()
        except: pass

if __name__ == "__main__":
    main()
