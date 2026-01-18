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

# [重要] 測試方案選擇 ----------------------------------------------------
# 'A': 快速模式 - 每個數量僅測 1 種組合 (平均分配)
# 'B': 推薦模式 - 每個數量測 2 種組合 (平均 + 集中單品測庫存)
# 'C': 全方位模式 - 窮舉所有可能的排列組合 (Main固定1，其餘隨機搭配)
TEST_PLAN = 'B'
# ----------------------------------------------------------------------

# Email 設定 (從 GitHub Secrets 讀取)
MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
MAIL_RECEIVER = ['bb00lin@gmail.com', 'helen.chen.168@gmail.com']

# ================= 輔助功能 (細節增強版) =================
def clean_price(price_text):
    """ 去除 SGD, $, 逗號並轉為純數字字串 """
    if not price_text: return ""
    p = str(price_text).replace("SGD", "").replace("$", "").replace(",", "").replace("\n", "").replace(" ", "").strip()
    return p

def get_taiwan_time_now():
    """ 取得當前台北時間 """
    return datetime.now(timezone(timedelta(hours=8)))

def get_taiwan_time_display():
    """ 格式化顯示時間: 2026-01-18 10:15 """
    return get_taiwan_time_now().strftime("%Y-%m-%d %H:%M")

def get_filename_time_prefix():
    """ 檔名專用時間戳 (避免冒號): 2026-01-18_10-15 """
    return get_taiwan_time_now().strftime("%Y-%m-%d_%H-%M")

def get_folder_date_prefix():
    """ 資料夾專用日期: 2026-01-18 """
    return get_taiwan_time_now().strftime("%Y-%m-%d")

def safe_get(row_list, index):
    """ 安全取得串列元素 """
    if index < len(row_list): return str(row_list[index])
    return ""

def parse_date(date_str):
    """ 將日期字串 18/01/2026 轉換為 Date 物件 """
    try:
        date_part = date_str.split()[0]
        return datetime.strptime(date_part, "%d/%m/%Y").date()
    except: return None

def create_zip_evidence(sku, sku_folder):
    """ 將資料夾內的截圖打包成 ZIP 檔並刪除原始資料夾 """
    try:
        if not os.path.exists(sku_folder) or not os.listdir(sku_folder):
            return None
        ts = get_filename_time_prefix()
        zip_filename_base = f"{ts}_{sku}"
        zip_path = shutil.make_archive(zip_filename_base, 'zip', sku_folder)
        shutil.rmtree(sku_folder) 
        return zip_path
    except Exception as e:
        print(f"   ⚠️ 打包 Zip 失敗 ({sku}): {e}")
        return None

# ================= Google Sheet 與表格操作 =================
def connect_google_sheet():
    """ 建立 Google Sheet 連線 """
    print("📊 正在連線 Google Sheet (使用環境變數 Secrets)...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 變數")
        return None
    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ 解析金鑰失敗: {e}")
        return None

def format_group_colors(sheet, data_rows):
    """
    根據 Main SKU 分組，在 Google Sheet 上顯示交替顏色 (強化對比版)
    """
    print("🎨 正在執行表格美化工程 (上色與格式)...")
    # 顏色定義 (RGB 0.0 ~ 1.0)
    COLOR_1 = {"red": 1.0, "green": 1.0, "blue": 1.0}      # 純白
    COLOR_2 = {"red": 0.85, "green": 0.85, "blue": 0.85}  # 明顯灰色

    requests = []
    if len(data_rows) < 2: return

    current_sku = ""
    current_color_idx = 0
    colors = [COLOR_1, COLOR_2]
    
    # 指令起點從 Row 1 開始 (Header 是 Row 0)
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
                    "startRowIndex": 1 + i,
                    "endRowIndex": 2 + i,
                    "startColumnIndex": 0,
                    "endColumnIndex": 10 
                },
                "cell": {"userEnteredFormat": {"backgroundColor": bg_color}},
                "fields": "userEnteredFormat.backgroundColor"
            }
        })

    try:
        if requests:
            # 必須使用 spreadsheet 調用 batch_update
            sheet.spreadsheet.batch_update({"requests": requests})
            print("✅ 表格上色成功！")
    except Exception as e:
        print(f"⚠️ 上色失敗: {e}")

# ================= Selenium 操作 (完整健壯版) =================
def init_driver():
    """ 初始化 Chrome 瀏覽器設定 """
    print("🌐 正在初始化瀏覽器引擎...")
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def handle_popups(driver):
    """ 清除頁面上可能出現的廣告、Cookie 同意書或 Side Cart """
    popups = [
        "button[aria-label='Close']", 
        "div.close-popup", 
        "#onetrust-accept-btn-handler", 
        "div[class*='popup'] button",
        "button.align-right.secondary.slidedown-button"
    ]
    for p in popups:
        try:
            elem = driver.find_element(By.CSS_SELECTOR, p)
            if elem.is_displayed():
                driver.execute_script("arguments[0].click();", elem)
                time.sleep(0.5)
        except: pass

def empty_cart(driver):
    """ 清空購物車最徹底的方法：清除所有 Cookies 與快取 """
    try:
        if "guardian.com.sg" not in driver.current_url:
            driver.get(URL)
            time.sleep(2)
        driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear(); window.sessionStorage.clear();")
        driver.refresh()
        time.sleep(3)
        print("   🧹 購物車已重置為空")
    except Exception as e:
        print(f"   ⚠️ 重置購物車異常: {e}")

def check_item_exists(driver, sku):
    """ 檢查商品是否能搜尋到且在架上 """
    try:
        driver.get(URL)
        time.sleep(1)
        handle_popups(driver)
        search_input = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search']")))
        search_input.clear()
        search_input.send_keys(sku + Keys.RETURN)
        time.sleep(4)
        # 只要能找到商品連結就視為存在
        driver.find_element(By.XPATH, f"//a[contains(@href, '{sku}')] | (//div[contains(@class, 'product')]//a)[1]")
        return True
    except:
        return False

def add_single_item_to_cart(driver, sku, qty):
    """ 前往商品頁並按指定次數點擊「加入購物車」 """
    print(f"   🛒 嘗試將商品 {sku} 加入購物車 (Qty: {qty})...")
    try:
        driver.get(URL)
        time.sleep(1)
        search_input = WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Search']")))
        search_input.send_keys(sku + Keys.RETURN)
        time.sleep(4)
        
        # 優先搜尋特定 SKU 連結，若無則抓第一個結果
        link_xpath = f"//a[contains(@href, '{sku}')] | (//div[contains(@class, 'product')]//a)[1]"
        link = driver.find_element(By.XPATH, link_xpath)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", link)
        driver.execute_script("arguments[0].click();", link)
        time.sleep(3)
        handle_popups(driver)

        add_success_count = 0
        for i in range(qty):
            add_btn = WebDriverWait(driver, 12).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart")))
            driver.execute_script("arguments[0].click();", add_btn)
            time.sleep(1.5)
            add_success_count += 1
            print(f"      - 第 {add_success_count} 件已加入")
        return True
    except Exception as e:
        print(f"      ❌ SKU {sku} 加入失敗: {e}")
        return False

# ================= Mix & Match 資料同步任務 =================
def sync_mix_match_data(client):
    """ 從 Promotion 分頁抓取 Mix & Match 規則並生成多種測試案例 """
    print(f"🔄 正在從 promotion 同步資料 (當前方案模式: {TEST_PLAN})...")
    spreadsheet = client.open(SPREADSHEET_FILE_NAME)
    source_sheet = spreadsheet.worksheet(WORKSHEET_PROMO)
    try:
        mix_sheet = spreadsheet.worksheet(WORKSHEET_MIX)
    except:
        mix_sheet = spreadsheet.add_worksheet(title=WORKSHEET_MIX, rows=500, cols=15)
    
    mix_sheet.clear()
    headers = ["Main SKU", "Product Name", "Promo Rule", "Target Qty", "Mix Strategy", "Expected Price", "Web Total Price", "Result", "Update Time", "Main Link"]
    all_values = source_sheet.get_all_values()
    new_data = [headers]
    today = get_taiwan_time_now().date()

    # 從第 7 列開始 (Index 6)
    for row in all_values[6:]:
        promo_desc = safe_get(row, 6) 
        if "Mix & Match" not in promo_desc: continue
        
        date_start_str, date_end_str = safe_get(row, 8), safe_get(row, 9)
        d_start, d_end = parse_date(date_start_str), parse_date(date_end_str)
        date_note = ""
        if d_start and d_end and not (d_start <= today <= d_end):
            date_note = f"⚠️非檔期({d_start.strftime('%m/%d')}~{d_end.strftime('%m/%d')})"
        elif d_start and today < d_start: date_note = "⚠️尚未開始"

        main_sku = safe_get(row, 11).replace("'", "").strip()[-6:]
        prod_name = safe_get(row, 12)
        
        # 尋找 2 For $94.0 這種模式
        matches = re.findall(r'(\d+)\s+[Ff]or\s*\$?([\d\.]+)', promo_desc)
        if not matches: continue
        rule_summary = f"{matches[-1][0]} For ${matches[-1][1]}"

        if date_note:
            new_data.append([main_sku, prod_name, rule_summary, "", "", "", "", date_note, "", ""])
            continue 

        # 解析混搭商品
        partners = []
        match_p = re.search(r'Mix & Match\s*([\d,]+)', promo_desc)
        if match_p:
            partners = [p.strip()[-6:] for p in match_p.group(1).split(',') if p.strip()[-6:] != main_sku]
        
        pool = [main_sku] + partners
        price_map = {int(q): float(p) for q, p in matches}
        best_unit = min([p/q for q, p in price_map.items()])

        # Qty 從 2 到 5 生成案例
        for target_qty in range(2, 6):
            expected = price_map[target_qty] if target_qty in price_map else int(best_unit * target_qty * 10) / 10.0
            strategies = []
            
            if TEST_PLAN == 'C':
                # 窮舉排列組合
                for combo in combinations_with_replacement(pool, target_qty-1):
                    s = {main_sku: 1}
                    for item in combo: s[item] = s.get(item, 0) + 1
                    strategies.append(s)
            else:
                # Plan A: 只有平均 | Plan B: 平均 + 集中
                # 1. 平均分配
                c_pool = cycle(pool)
                strat_a = {}
                for _ in range(target_qty):
                    it = next(c_pool); strat_a[it] = strat_a.get(it, 0) + 1
                strategies.append(strat_a)
                # 2. 集中於單一贈品
                if TEST_PLAN == 'B' and partners:
                    strat_b = {main_sku: 1, partners[0]: target_qty - 1}
                    if strat_b != strat_a: strategies.append(strat_b)
            
            for s in strategies:
                s_str = "; ".join([f"{k}:{v}" for k, v in s.items()])
                new_data.append([main_sku, prod_name, rule_summary, target_qty, s_str, str(expected), "", "", "", ""])

    mix_sheet.update(values=new_data, range_name="A1")
    format_group_colors(mix_sheet, new_data)
    print(f"✅ 已生成 {len(new_data)-1} 條測試案例")
    return new_data

# ================= 爬蟲核心逻辑 (數量修正版) =================
def process_mix_case_dynamic(driver, strategy_str, target_qty, main_sku):
    """ 實際執行混搭購買，並解決商品缺貨導致數量不足的問題 """
    empty_cart(driver)
    raw_items = strategy_str.split(';')
    planned_dict = {i.split(':')[0].strip(): int(i.split(':')[1].strip()) for i in raw_items}
    
    date_p = get_folder_date_prefix()
    folder_name = f"{date_p}_mix_{main_sku}"
    if not os.path.exists(folder_name): os.makedirs(folder_name)
    ts_file = get_filename_time_prefix()
    
    # 庫存檢查
    available_skus, missing_skus = [], []
    for sku in planned_dict.keys():
        if check_item_exists(driver, sku): available_skus.append(sku)
        else: missing_skus.append(sku)
    
    # 如果主商品都沒了，直接判斷為失效
    if main_sku in missing_skus:
        return "Main Missing", "URL Not Found", None, [main_sku], strategy_str

    # === [核心修復：數量補齊邏輯] ===
    # 若某個混搭夥伴缺貨，將它的額度補給主商品 (Main SKU)
    final_run_dict = {sku: 0 for sku in planned_dict.keys()}
    current_cart_count = 0
    for sku, qty in planned_dict.items():
        if sku in available_skus:
            final_run_dict[sku] = qty
            current_cart_count += qty
    
    if current_cart_count < target_qty:
        deficit = target_qty - current_cart_count
        final_run_dict[main_sku] += deficit
        print(f"   ⚠️ 發現缺口 {deficit} 件 (因商品 {missing_skus} 缺貨)，已自動補在主商品 {main_sku}")

    actual_strategy_display = "; ".join([f"{k}:{v}" for k, v in final_run_dict.items()])
    
    # 執行購買
    empty_cart(driver)
    for sku, qty in final_run_dict.items():
        if qty > 0:
            if not add_single_item_to_cart(driver, sku, qty):
                driver.save_screenshot(f"{folder_name}/{ts_file}_Fail_{sku}.png")
                return "Add Fail", "", create_zip_evidence(main_sku, folder_name), missing_skus, actual_strategy_display

    driver.get("https://guardian.com.sg/cart")
    try: WebDriverWait(driver, 20).until_not(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING')]")))
    except: pass
    
    # 用戶要求：進入購物車後強制等待 6 秒讓彈窗消失
    print("   ⏳ 正在執行強制等待 (6 秒)...")
    time.sleep(6)
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.send_keys(Keys.ESCAPE)
        driver.execute_script("arguments[0].click();", body)
        time.sleep(1)
    except: pass

    # 提取總金額
    web_total = "Error"
    try:
        price_elem = WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.XPATH, "//span[contains(@class, 'priceSummary-totalPrice')]")))
        web_total = clean_price(price_elem.text)
    except: 
        print("      ❌ 無法從頁面抓取總金額")

    # 截圖存檔 (檔名前加上時間)
    driver.save_screenshot(f"{folder_name}/{ts_file}_Result_{main_sku}.png")
    
    return web_total, driver.current_url, create_zip_evidence(main_sku, folder_name), missing_skus, actual_strategy_display

# ================= 主程式與報表發送 =================
def run_mix_match_task(client, driver):
    data_list = sync_mix_match_data(client)
    sheet = client.open(SPREADSHEET_FILE_NAME).worksheet(WORKSHEET_MIX)
    results_for_mail = []
    attachments = []
    all_match = True

    for i, row in enumerate(data_list[1:], start=2):
        main_sku = safe_get(row, 0)
        target_qty = int(row[3]) if row[3] else 0
        expected = float(row[5]) if row[5] else 0.0
        
        if "⚠️" in safe_get(row, 7):
            results_for_mail.append([main_sku, row[1], row[7], get_taiwan_time_display()])
            continue
        
        print(f"\n🚀 正在測試: {main_sku} (第 {i-1}/{len(data_list)-1} 項)...")
        
        # 執行購買流程
        web_p, link, zip_file, missing, actual_strat = process_mix_case_dynamic(driver, row[4], target_qty, main_sku)
        
        # 更新實際購買組合到表格 E 欄
        sheet.update_cell(i, 5, actual_strat)
        
        # 比對結果
        res_text = "❌ 失敗"
        try:
            if abs(float(web_p) - expected) < 0.05:
                res_text = "✅ 相符"
            else:
                res_text = f"🔥 差異 (Exp:{expected} != Web:{web_p})"
                all_match = False
        except: 
            res_text = f"❌ 異常 ({web_p})"
            all_match = False
        
        if missing: res_text += f" (⚠️缺:{','.join(missing)})"
        
        # [需求修正] 無論成功或失敗，全數附上截圖
        if zip_file: attachments.append(zip_file)
        
        update_time = get_taiwan_time_display()
        sheet.update(values=[[web_p, res_text, update_time, link]], range_name=f"G{i}:J{i}")
        results_for_mail.append([main_sku, row[1], res_text, update_time])
        print(f"   🚩 結果: {res_text}")

    # 最後再刷一次顏色，確保沒掉色
    format_group_colors(sheet, data_list)
    
    # 發送郵件
    subject_prefix = "✅" if all_match else "🔥"
    subject = f"{get_taiwan_time_now().strftime('%m/%d')}{subject_prefix}[Ozio Mix&Match 旗艦報表]"
    send_notification_email(subject, results_for_mail, attachments)

def send_notification_email(subject, data, attachments):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        print("⚠️ 未設定 Email 帳密，跳過發信")
        return
        
    print(f"📧 正在發送郵件報表 (附件數: {len(attachments)})...")
    
    table_rows = ""
    for r in data:
        bg = "#ffffff"
        if "🔥" in str(r[2]): bg = "#ffebee" # 淺紅
        elif "⚠️" in str(r[2]): bg = "#fff3e0" # 淺橘
        table_rows += f"<tr style='background:{bg}'><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>"

    html_content = f"""
    <html><body>
        <h2 style='color:#333;'>{subject}</h2>
        <p>報告生成時間: {get_taiwan_time_display()}</p>
        <table border='1' style='border-collapse:collapse; width:100%; font-family: sans-serif; font-size: 13px;'>
            <tr style='background:#f2f2f2;'><th>SKU</th><th>產品名稱</th><th>比對結果 (包含補齊邏輯)</th><th>更新時間</th></tr>
            {table_rows}
        </table>
        <br>
        <p>📊 查看即時更新的表格: <a href='{SHEET_URL_FOR_MAIL}'>Google Sheets 連結</a></p>
        <p style='color: gray; font-size: 11px;'>此郵件由 Guardian Mix Match Bot 自動發送</p>
    </body></html>
    """
    
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = MAIL_USERNAME
    msg['To'] = ", ".join(MAIL_RECEIVER)
    msg.attach(MIMEText(html_content, 'html'))
    
    # 夾帶 25 份附件
    for fpath in attachments:
        try:
            with open(fpath, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(fpath))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(fpath)}"'
            msg.attach(part)
        except: pass

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
            server.send_message(msg)
        print("✅ 郵件發送成功")
    except Exception as e:
        print(f"❌ 郵件發送失敗: {e}")

def main():
    try:
        client = connect_google_sheet()
        if not client: return
        
        driver = init_driver()
        run_mix_match_task(client, driver)
        driver.quit()
        print("\n🏁 [Task 2] 混搭測試任務全數圓滿結束")
    except Exception as e:
        print(f"💥 發生重大崩潰: {e}")
        if 'driver' in locals(): driver.quit()

if __name__ == "__main__":
    main()
