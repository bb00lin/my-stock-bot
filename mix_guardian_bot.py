import time
import gspread
import re
import os
import shutil
import smtplib
import math
import json
from itertools import cycle, combinations_with_replacement
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

# Google Sheet 網址
SHEET_URL_FOR_MAIL = "https://docs.google.com/spreadsheets/d/1pqa6DU-qo3lR84QYgpoiwGE7tO-QSY2-kC_ecf868cY/edit?gid=1727836519#gid=1727836519"

URL = "https://guardian.com.sg/"

# [設定] 測試方案選擇
# 'A': 基本模式 | 'B': 極端模式 (每個數量測 2 種組合，推薦) | 'C': 全組合模式
TEST_PLAN = 'B'

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
    # 範例: 2026-01-18_10-15
    return get_taiwan_time_now().strftime("%Y-%m-%d_%H-%M")

def get_folder_date_prefix():
    # 範例: 2026-01-18
    return get_taiwan_time_now().strftime("%Y-%m-%d")

def safe_get(row_list, index):
    if index < len(row_list): return str(row_list[index])
    return ""

def parse_date(date_str):
    try:
        date_part = date_str.split()[0]
        return datetime.strptime(date_part, "%d/%m/%Y").date()
    except: return None

def create_zip_evidence(sku, sku_folder):
    try:
        if not os.path.exists(sku_folder) or not os.listdir(sku_folder): return None
        ts = get_filename_time_prefix()
        zip_filename_base = f"{ts}_{sku}"
        zip_path = shutil.make_archive(zip_filename_base, 'zip', sku_folder)
        shutil.rmtree(sku_folder) 
        return zip_path
    except: return None

# ================= Google Sheet 格式化工程 =================
def connect_google_sheet():
    print("📊 正在連線 Google Sheet (使用 Secrets)...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    if not json_key_str: return None
    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except: return None

def format_group_colors(sheet, data_rows):
    """ 根據主商品為表格上色 (對比明顯版) """
    print("🎨 正在執行表格美化工程...")
    COLOR_1 = {"red": 1.0, "green": 1.0, "blue": 1.0}
    COLOR_2 = {"red": 0.85, "green": 0.85, "blue": 0.85}
    requests = []
    if len(data_rows) < 2: return
    current_sku, current_color_idx = "", 0
    colors = [COLOR_1, COLOR_2]
    for i, row in enumerate(data_rows[1:]):
        sku = safe_get(row, 0)
        if sku != current_sku:
            current_sku = sku
            current_color_idx = (current_color_idx + 1) % 2
        bg_color = colors[current_color_idx]
        requests.append({
            "repeatCell": {
                "range": {"sheetId": sheet.id, "startRowIndex": 1 + i, "endRowIndex": 2 + i, "startColumnIndex": 0, "endColumnIndex": 10},
                "cell": {"userEnteredFormat": {"backgroundColor": bg_color}},
                "fields": "userEnteredFormat.backgroundColor"
            }
        })
    try:
        if requests: sheet.spreadsheet.batch_update({"requests": requests})
        print("✅ 上色完成")
    except: pass

# ================= Selenium 爬蟲引擎 =================
def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

def handle_popups(driver):
    popups = ["button[aria-label='Close']", "div.close-popup", "#onetrust-accept-btn-handler", "div[class*='popup'] button"]
    for p in popups:
        try:
            elem = driver.find_element(By.CSS_SELECTOR, p)
            if elem.is_displayed(): driver.execute_script("arguments[0].click();", elem)
        except: pass

def empty_cart(driver):
    try:
        driver.get(URL)
        driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
        driver.refresh()
        time.sleep(2)
    except: pass

def check_item_exists(driver, sku):
    """ 搜尋並確認商品連結是否存在 """
    try:
        driver.get(URL)
        time.sleep(1)
        handle_popups(driver)
        search_input = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search']")))
        search_input.clear()
        search_input.send_keys(sku + Keys.RETURN)
        time.sleep(4)
        driver.find_element(By.XPATH, f"//a[contains(@href, '{sku}')] | (//div[contains(@class, 'product')]//a)[1]")
        return True
    except: return False

def add_single_item_to_cart(driver, sku, qty):
    """ 進入商品頁並連續點擊加入購物車 """
    print(f"   🛒 正在加入: {sku} (數量: {qty})")
    try:
        driver.get(URL)
        time.sleep(1)
        search_input = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search']")))
        search_input.send_keys(sku + Keys.RETURN)
        time.sleep(4)
        link = driver.find_element(By.XPATH, f"//a[contains(@href, '{sku}')] | (//div[contains(@class, 'product')]//a)[1]")
        driver.execute_script("arguments[0].click();", link)
        time.sleep(3)
        
        for _ in range(qty):
            add_btn = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart")))
            driver.execute_script("arguments[0].click();", add_btn)
            # 等待「已加入購物車」的提示消失，避免阻擋下次點擊
            time.sleep(1.5)
        return True
    except Exception as e:
        print(f"      ❌ 加入失敗: {e}")
        return False

# ================= Task 2: Mix & Match 同步與執行 =================
def sync_mix_match_data(client):
    print(f"🔄 同步 Mix & Match 清單 (模式: {TEST_PLAN})...")
    promo_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_PROMO)
    try: mix_sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    except: mix_sheet = client.open(SPREADSHEET_FILE_NAME).add_worksheet(title=WORKSHEET_MIX, rows=500, cols=15)
    
    mix_sheet.clear()
    headers = ["Main SKU", "Product Name", "Promo Rule", "Target Qty", "Mix Strategy", "Expected Price", "Web Total Price", "Result", "Update Time", "Main Link"]
    rows = promo_sheet.get_all_values()
    new_data = [headers]
    today = get_taiwan_time_now().date()

    for row in rows[6:]:
        desc = safe_get(row, 6) 
        if "Mix & Match" not in desc: continue
        
        start, end = parse_date(safe_get(row, 8)), parse_date(safe_get(row, 9))
        date_note = ""
        if start and end and not (start <= today <= end): date_note = f"⚠️非檔期({start}~{end})"
        elif start and today < start: date_note = "⚠️尚未開始"

        main_sku = safe_get(row, 11).replace("'", "").strip()[-6:]
        prod_name = safe_get(row, 12)
        matches = re.findall(r'(\d+)\s+[Ff]or\s*\$?([\d\.]+)', desc)
        if not matches: continue
        rule_display = f"{matches[-1][0]} For ${matches[-1][1]}"

        if date_note:
            new_data.append([main_sku, prod_name, rule_display, "", "", "", "", date_note, "", ""])
            continue 

        # 解析混搭夥伴
        partners = []
        match_p = re.search(r'Mix & Match\s*([\d,]+)', desc)
        if match_p:
            partners = [p.strip()[-6:] for p in match_p.group(1).split(',') if p.strip()[-6:] != main_sku]
        
        pool = [main_sku] + partners
        price_map = {int(q): float(p) for q, p in matches}
        best_unit = min([p/q for q, p in price_map.items()])

        for target_qty in range(2, 6):
            # 依據 Unit Price 計算期望金額
            expected = price_map[target_qty] if target_qty in price_map else round(best_unit * target_qty, 1)
            
            strategies = []
            if TEST_PLAN == 'C':
                # 窮舉：主商品固定 1，剩餘隨意搭
                for combo in combinations_with_replacement(pool, target_qty-1):
                    s = {main_sku: 1}
                    for itm in combo: s[itm] = s.get(itm, 0) + 1
                    strategies.append(s)
            else:
                # Plan A & B：平均分配
                c = cycle(pool)
                s_a = {}
                for _ in range(target_qty): 
                    it = next(c); s_a[it] = s_a.get(it, 0) + 1
                strategies.append(s_a)
                # Plan B：集中於一個夥伴
                if TEST_PLAN == 'B' and partners:
                    s_b = {main_sku: 1, partners[0]: target_qty - 1}
                    if s_b != s_a: strategies.append(s_b)
            
            for s in strategies:
                # 格式化策略字串 (例如 630247:1; 632202:1)
                s_str = "; ".join([f"{k}:{v}" for k, v in s.items()])
                new_data.append([main_sku, prod_name, rule_display, target_qty, s_str, str(expected), "", "", "", ""])

    mix_sheet.update(values=new_data, range_name="A1")
    format_group_colors(mix_sheet, new_data)
    print(f"✅ 生成 {len(new_data)-1} 條測試項目")
    return new_data

def process_mix_case_dynamic(driver, strategy_str, target_total_qty, main_sku):
    """ 實際執行混搭比對，並確保『總件數』正確 """
    print(f"🧪 正在執行混搭比對，目標數量: {target_total_qty}")
    empty_cart(driver)
    raw_items = strategy_str.split(';')
    # 原始規劃清單
    planned_dict = {i.split(':')[0].strip(): int(i.split(':')[1].strip()) for i in raw_items}
    
    date_p = get_folder_date_prefix()
    folder_name = f"{date_p}_mix_{main_sku}"
    if not os.path.exists(folder_name): os.makedirs(folder_name)
    ts_file = get_filename_time_prefix()
    
    # 檢查架上庫存
    available_skus, missing_skus = [], []
    for sku in planned_dict.keys():
        if check_item_exists(driver, sku): available_skus.append(sku)
        else: missing_skus.append(sku)
    
    # 若主商品都沒了，直接跳過
    if main_sku in missing_skus: return "Main Missing", "URL Not Found", None, [main_sku], strategy_str

    # === [關鍵修正] 數量補齊邏輯：總數必須等於 Target Qty ===
    # 建立最終執行的字典
    final_run_dict = {sku: 0 for sku in planned_dict.keys()}
    current_total_in_cart = 0
    for sku, qty in planned_dict.items():
        if sku in available_skus:
            final_run_dict[sku] = qty
            current_total_in_cart += qty
    
    # 計算缺額並補給主商品 (Main SKU)
    if current_total_in_cart < target_total_qty:
        deficit = target_total_qty - current_total_in_cart
        final_run_dict[main_sku] += deficit
        print(f"   ⚠️ 商品 {missing_skus} 缺貨，已自動將差額 {deficit} 件補給主商品 {main_sku}")

    final_display_str = "; ".join([f"{k}:{v}" for k, v in final_run_dict.items()])
    
    # 執行加入購物車
    empty_cart(driver)
    for sku, qty in final_run_dict.items():
        if qty > 0:
            if not add_single_item_to_cart(driver, sku, qty):
                driver.save_screenshot(f"{folder_name}/{ts_file}_Fail_{sku}.png")
                return "Add Fail", "", create_zip_evidence(main_sku, folder_name), missing_skus, final_display_str

    # 前往結帳頁抓取最終總額
    driver.get("https://guardian.com.sg/cart")
    try: WebDriverWait(driver, 20).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING')]")))
    except: pass
    
    # 強制等待與清除 Side Cart
    print("   ⏳ 強制等待 6 秒並截圖...")
    time.sleep(6)
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        driver.execute_script("arguments[0].click();", body)
    except: pass

    # 抓取總價
    web_total = "Error"
    try:
        price_elem = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//span[contains(@class, 'priceSummary-totalPrice')]")))
        web_total = clean_price(price_elem.text)
    except: print("      ❌ 無法抓取網頁總額")

    driver.save_screenshot(f"{folder_name}/{ts_file}_Result_{main_sku}.png")
    return web_total, driver.current_url, create_zip_evidence(main_sku, folder_name), missing_skus, final_display_str

def run_mix_match_task(client, driver):
    """ Task 2 主流程 """
    data_list = sync_mix_match_data(client)
    sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    results_for_mail, attachments, all_match = [], [], True

    for i, row in enumerate(data_list[1:], start=2):
        main_sku = safe_get(row, 0)
        target_qty = int(row[3]) if row[3] else 0
        expected = float(row[5]) if row[5] else 0.0
        
        # 排除非檔期商品
        if "⚠️" in safe_get(row, 7):
            results_for_mail.append([main_sku, row[1], row[7], get_taiwan_time_display()])
            continue
        
        print(f"\n🚀 執行項目 {i-1}/{len(data_list)-1}: {main_sku} (目標總數: {target_qty})")
        
        # 執行購買並取得補齊後的實際策略
        web_p, link, zip_file, missing, actual_strat = process_mix_case_dynamic(driver, row[4], target_qty, main_sku)
        
        # 1. 更新『實際採購策略』回表格 E 欄 (讓您看到補齊結果)
        sheet.update_cell(i, 5, actual_strat)
        
        # 2. 比對金額
        res_text = "❌ 失敗"
        try:
            if abs(float(web_p) - expected) < 0.05: res_text = "✅ 相符"
            else: res_text = f"🔥 差異 (Exp:{expected} != Web:{web_p})"; all_match = False
        except: all_match = False
        
        if missing: res_text += f" (⚠️缺:{','.join(missing)})"
        
        # 3. 無論如何都附上照片 (需求: 25張)
        if zip_file: attachments.append(zip_file)
        
        # 4. 更新結果
        now_ts = get_taiwan_time_display()
        sheet.update(values=[[web_p, res_text, now_ts, link]], range_name=f"G{i}:J{i}")
        results_for_mail.append([main_sku, row[1], res_text, now_ts])
        print(f"   🚩 結果: {res_text}")

    # 最後重刷一次顏色與寄信
    format_group_colors(sheet, data_list)
    subject = f"{get_taiwan_time_now().strftime('%m/%d')} {'✅' if all_match else '🔥'}[Ozio Mix&Match 完整報告]"
    send_final_email(subject, results_for_mail, attachments)

def send_final_email(subject, data, attachments):
    if not MAIL_USERNAME: return
    print(f"📧 正在發送郵件 (夾帶附件: {len(attachments)})...")
    
    rows_html = ""
    for r in data:
        bg = "#ffffff"
        if "🔥" in str(r[2]): bg = "#ffebee"
        elif "⚠️" in str(r[2]): bg = "#fff3e0"
        rows_html += f"<tr style='background:{bg}'><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"

    html = f"""
    <html><body>
        <h2>{subject}</h2>
        <table border='1' style='border-collapse:collapse; width:100%'>
            <tr style='background:#f2f2f2'><th>SKU</th><th>產品名稱</th><th>比對結果 (包含補齊邏輯)</th><th>時間</th></tr>
            {rows_html}
        </table>
    </body></html>
    """
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = MAIL_USERNAME
    msg['To'] = ", ".join(MAIL_RECEIVER)
    msg.attach(MIMEText(html, 'html'))
    
    for f in attachments:
        try:
            with open(f, 'rb') as fp: part = MIMEApplication(fp.read(), Name=os.path.basename(f))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(f)}"'
            msg.attach(part)
        except: pass

    with smtplib.SMTP('smtp.gmail.com', 587) as s:
        s.starttls()
        s.login(MAIL_USERNAME, MAIL_PASSWORD)
        s.send_message(msg)
    print("✅ 郵件發送成功")

def main():
    try:
        client = connect_google_sheet()
        driver = init_driver()
        run_mix_match_task(client, driver)
        driver.quit()
        print("\n🏁 Mix & Match 任務全數圓滿結束")
    except Exception as e:
        print(f"💥 發生重大崩潰: {e}")
        if 'driver' in locals(): driver.quit()

if __name__ == "__main__":
    main()
