import time
import os
import json
import shutil
import zipfile
import smtplib
import gspread
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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ================= 設定區 =================
SPREADSHEET_FILE_NAME = 'Guardian_Price_Check'
WORKSHEET_NAME = 'Pokemon'  # 請確認這個名稱與分頁完全一致
POKEMON_URL = "https://www.pokemoncenter-online.com/"

# Email 設定
MAIL_RECEIVERS = ['bb00lin@gmail.com', 'helen.chen.168@gmail.com']

# ================= 輔助功能 =================
def get_taiwan_time_now():
    return datetime.now(timezone(timedelta(hours=8)))

def get_time_str_for_filename():
    return get_taiwan_time_now().strftime("%Y-%m-%d_%H-%M")

def get_display_time():
    return get_taiwan_time_now().strftime("%Y-%m-%d %H:%M")

def connect_google_sheet():
    print("📊 正在連線 Google Sheet...", flush=True)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 環境變數", flush=True)
        return None

    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key_str), scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ 連線失敗: {e}", flush=True)
        return None

def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

# 捲動截圖函式
def capture_scrolling_screenshots(driver, directory, base_filename):
    try:
        total_height = driver.execute_script("return document.body.scrollHeight")
        viewport_height = driver.execute_script("return window.innerHeight")
        if total_height == 0: total_height = viewport_height

        scroll_pos = 0
        part = 1
        
        while scroll_pos < total_height:
            driver.execute_script(f"window.scrollTo(0, {scroll_pos});")
            time.sleep(1) 
            
            file_path = f"{directory}/{base_filename}-{part}.png"
            driver.save_screenshot(file_path)
            
            scroll_pos += viewport_height
            part += 1
            if part > 8: break # 限制最多截 8 張避免過大
            
    except Exception as e:
        print(f"⚠️ 截圖失敗: {e}", flush=True)

def send_email(subject, body, attachment_path=None):
    mail_user = os.environ.get('MAIL_USERNAME')
    mail_pass = os.environ.get('MAIL_PASSWORD')
    
    if not mail_user or not mail_pass:
        print("⚠️ 未設定 Email 帳密，跳過寄信", flush=True)
        return

    print(f"📧 正在發送郵件: {subject}", flush=True)
    msg = MIMEMultipart()
    msg['From'] = mail_user
    msg['To'] = ", ".join(MAIL_RECEIVERS)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))

    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        except Exception as e:
            print(f"⚠️ 附件夾帶失敗: {e}", flush=True)

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(mail_user, mail_pass)
        server.send_message(msg)
        server.quit()
        print("✅ 郵件發送成功", flush=True)
    except Exception as e:
        print(f"❌ 郵件發送失敗: {e}", flush=True)

# ================= 主程式邏輯 =================
def main():
    client = connect_google_sheet()
    if not client: return

    driver = init_driver()
    wait = WebDriverWait(driver, 20)

    # 建立截圖目錄
    screenshot_dir = "pokemon_screenshots"
    if os.path.exists(screenshot_dir): shutil.rmtree(screenshot_dir)
    os.makedirs(screenshot_dir)

    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        
        # [修改] 增加防呆機制：如果找不到分頁，列出所有現有分頁
        try:
            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            print(f"❌ 錯誤：找不到名為 '{WORKSHEET_NAME}' 的分頁！", flush=True)
            print("📋 目前試算表中的所有分頁名稱如下 (請檢查大小寫/空白)：", flush=True)
            for ws in spreadsheet.worksheets():
                print(f"   👉 '{ws.title}'", flush=True)
            return

        print("🧹 清理舊資料 (C欄到H欄)...", flush=True)
        # 清除 C, D, E, F, G, H 欄位 (保留 B 欄)
        worksheet.batch_clear(["C2:H1000"])

        # 讀取 A 欄商品編號
        product_ids = worksheet.col_values(1)[1:] 
        
        # 統計
        total_items = 0
        success_items = 0
        not_found_items = 0
        summary_list = []

        for i, pid in enumerate(product_ids):
            row_idx = i + 2
            if not pid or not str(pid).strip(): continue
            
            clean_pid = str(pid).strip()
            total_items += 1
            print(f"🔍 [{i+1}] 搜尋商品編號: {clean_pid}", flush=True)
            
            driver.get(POKEMON_URL)
            update_time = get_display_time()
            
            try:
                # 1. 搜尋
                search_box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='text']")))
                search_box.clear()
                search_box.send_keys(clean_pid)
                search_box.send_keys(Keys.ENTER)
                
                time.sleep(3) 

                # 2. 判斷是否無結果
                page_source = driver.page_source
                if "該当する商品は見つかりませんでした" in page_source or "0件" in page_source:
                    print(f"ℹ️ {clean_pid}: 商品不存在 (Not Found)", flush=True)
                    # 依照需求：D欄寫 Not Found, H欄寫時間
                    worksheet.update(range_name=f"D{row_idx}", values=[["Not Found"]])
                    worksheet.update(range_name=f"H{row_idx}", values=[[update_time]])
                    not_found_items += 1
                    summary_list.append(f"{clean_pid}: Not Found")
                    continue

                # 3. 點擊第一個商品
                try:
                    first_product = driver.find_element(By.CSS_SELECTOR, "div.product-list a, .item-list a")
                    product_link = first_product.get_attribute("href")
                    driver.get(product_link)
                    time.sleep(3)
                except NoSuchElementException:
                    print(f"⚠️ 找不到商品連結", flush=True)
                    worksheet.update(range_name=f"D{row_idx}", values=[["Click Error"]])
                    continue

                # 4. 抓取資料
                current_url = driver.current_url
                
                # (1) 次分類
                sub_category = ""
                try:
                    sub_cat_elem = driver.find_element(By.CSS_SELECTOR, ".product-header__category, .category-tag, ul.breadcrumb li:last-child")
                    sub_category = sub_cat_elem.text.strip()
                except:
                    sub_category = "N/A"

                # (2) 商品名稱 (D欄)
                product_name = ""
                try:
                    name_elem = driver.find_element(By.TAG_NAME, "h1")
                    product_name = name_elem.text.strip()
                except:
                    product_name = "Unknown Name"

                # (3) 尺寸與重量 (E, F欄)
                size_val = ""
                weight_val = "未標示"
                
                try:
                    spec_td = driver.find_element(By.XPATH, "//th[contains(text(), 'サイズ') or contains(text(), '重量')]/following-sibling::td")
                    spec_text = spec_td.text.strip()
                    
                    if "\u3000" in spec_text:
                        parts = spec_text.split("\u3000")
                        size_val = parts[0].strip()
                        if len(parts) > 1:
                            weight_val = parts[1].strip()
                    else:
                        size_val = spec_text
                        
                except NoSuchElementException:
                    size_val = "規格未找到"

                # 5. 截圖
                capture_scrolling_screenshots(driver, screenshot_dir, clean_pid)

                # 6. 寫入 Google Sheet
                data_to_write = [
                    sub_category,   # C
                    product_name,   # D
                    size_val,       # E
                    weight_val,     # F
                    current_url,    # G
                    update_time     # H
                ]
                
                worksheet.update(range_name=f"C{row_idx}:H{row_idx}", values=[data_to_write])
                print(f"✅ {clean_pid}: 更新完成", flush=True)
                success_items += 1
                summary_list.append(f"{clean_pid}: {product_name}")

            except Exception as e:
                print(f"❌ {clean_pid} 處理失敗: {str(e)[:50]}", flush=True)
                worksheet.update(range_name=f"D{row_idx}", values=[["Error"]])

        # 7. 打包與寄信
        zip_filename = f"Pokemon_{get_time_str_for_filename()}.zip"
        print(f"📦 打包截圖: {zip_filename}", flush=True)
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(screenshot_dir):
                for file in files:
                    zipf.write(os.path.join(root, file), file)

        subject = f"Pokemon商品查詢結果-共{total_items}筆，成功{success_items}筆，未找到{not_found_items}筆"
        
        html_list = "".join([f"<li>{s}</li>" for s in summary_list])
        body = f"""
        <html><body>
            <h2>Pokemon Center 商品查詢報告</h2>
            <p><b>執行時間:</b> {get_display_time()}</p>
            <ul>
                <li>查詢總數: {total_items}</li>
                <li>成功抓取: {success_items}</li>
                <li>無此商品: {not_found_items}</li>
            </ul>
            <p><b>處理明細:</b></p>
            <ul>{html_list}</ul>
            <br>
            <p>截圖檔案請參閱附件。</p>
        </body></html>
        """
        
        send_email(subject, body, zip_filename)
        print("🎉 任務全部完成！", flush=True)

    except Exception as main_e:
        print(f"💥 程式崩潰: {main_e}", flush=True)
    finally:
        driver.quit()
        if os.path.exists(screenshot_dir): shutil.rmtree(screenshot_dir)

if __name__ == "__main__":
    main()
