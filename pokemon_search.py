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
WORKSHEET_NAME = 'Pokemon'
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
    
    # [反偵測設定] 模擬真實瀏覽器
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # 加入語系設定，讓網站認為我們是正常用戶
    options.add_argument("--lang=ja-JP")
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

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
            driver.save_screenshot(f"{directory}/{base_filename}-{part}.png")
            scroll_pos += viewport_height
            part += 1
            if part > 8: break
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
        except: pass
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(mail_user, mail_pass)
        server.send_message(msg)
        server.quit()
        print("✅ 郵件發送成功", flush=True)
    except Exception as e:
        print(f"❌ 郵件發送失敗: {e}", flush=True)

def find_search_input(driver, wait):
    # [修正] 針對 Pokemon Center 的 name="q"
    selectors = [
        (By.NAME, "q"),
        (By.CSS_SELECTOR, "input.search-field"),
        (By.CSS_SELECTOR, "input[type='text']")
    ]
    for by_type, selector_str in selectors:
        try:
            element = wait.until(EC.element_to_be_clickable((by_type, selector_str)))
            return element
        except: continue
    return None

def main():
    client = connect_google_sheet()
    if not client: return

    driver = init_driver()
    wait = WebDriverWait(driver, 25)

    screenshot_dir = "pokemon_screenshots"
    if os.path.exists(screenshot_dir): shutil.rmtree(screenshot_dir)
    os.makedirs(screenshot_dir)

    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        try:
            worksheet = spreadsheet.worksheet(WORKSHEET_NAME)
        except gspread.WorksheetNotFound:
            print(f"❌ 錯誤：找不到名為 '{WORKSHEET_NAME}' 的分頁！", flush=True)
            return

        print("🧹 清理舊資料...", flush=True)
        worksheet.batch_clear(["C2:H1000"])

        product_ids = worksheet.col_values(1)[1:] 
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
            
            # 強制清除 Cookies 以避免被追蹤
            driver.delete_all_cookies()
            driver.get(POKEMON_URL)
            update_time = get_display_time()
            
            print(f"   👉 頁面標題: {driver.title}", flush=True)
            
            try:
                search_box = find_search_input(driver, wait)
                
                if not search_box:
                    if "Restricted" in driver.title or "Access Denied" in driver.page_source:
                        print(f"🚫 嚴重警告：IP 被封鎖 (Restricted access)。請確認已切換至 macOS runner。", flush=True)
                        break 
                    raise Exception("Search Box Not Found")

                search_box.clear()
                search_box.send_keys(clean_pid)
                search_box.send_keys(Keys.ENTER)
                time.sleep(5) 

                if "該当する商品は見つかりませんでした" in driver.page_source or "0件" in driver.page_source:
                    print(f"ℹ️ {clean_pid}: Not Found", flush=True)
                    worksheet.update(range_name=f"D{row_idx}", values=[["Not Found"]])
                    worksheet.update(range_name=f"H{row_idx}", values=[[update_time]])
                    not_found_items += 1
                    summary_list.append(f"{clean_pid}: Not Found")
                    continue

                # [修正] 點擊商品邏輯 (li.product a)
                try:
                    first_product = driver.find_element(By.CSS_SELECTOR, "li.product a, div.product-list a")
                    product_link = first_product.get_attribute("href")
                    driver.get(product_link)
                    time.sleep(3)
                except NoSuchElementException:
                    print(f"⚠️ 找不到商品連結", flush=True)
                    worksheet.update(range_name=f"D{row_idx}", values=[["Click Error"]])
                    continue

                current_url = driver.current_url
                
                # [修正] 標題與分類抓取邏輯
                # 您的截圖: <h1>標題<span>分類</span></h1>
                product_name = ""
                sub_category = "N/A"
                try:
                    h1_elem = driver.find_element(By.CSS_SELECTOR, "h1.lead")
                    # 嘗試抓取內部的 span (分類)
                    try:
                        span_elem = h1_elem.find_element(By.TAG_NAME, "span")
                        sub_category = span_elem.text.strip()
                        # 商品名稱 = 完整文字 - 分類文字
                        full_text = h1_elem.text.strip()
                        product_name = full_text.replace(sub_category, "").strip()
                    except:
                        # 如果沒有 span，則整串都是名稱
                        product_name = h1_elem.text.strip()
                except:
                    product_name = "Unknown Name"

                # 尺寸與重量
                size_val = ""
                weight_val = "未標示"
                try:
                    spec_td = driver.find_element(By.XPATH, "//th[contains(text(), 'サイズ') or contains(text(), '重量')]/following-sibling::td")
                    spec_text = spec_td.text.strip()
                    if "\u3000" in spec_text:
                        parts = spec_text.split("\u3000")
                        size_val = parts[0].strip()
                        if len(parts) > 1: weight_val = parts[1].strip()
                    else:
                        size_val = spec_text
                except:
                    size_val = "規格未找到"

                capture_scrolling_screenshots(driver, screenshot_dir, clean_pid)

                data_to_write = [
                    sub_category,   # C
                    product_name,   # D
                    size_val,       # E
                    weight_val,     # F
                    current_url,    # G
                    update_time     # H
                ]
                worksheet.update(range_name=f"C{row_idx}:H{row_idx}", values=[data_to_write])
                print(f"✅ {clean_pid}: 更新完成 ({product_name})", flush=True)
                success_items += 1
                summary_list.append(f"{clean_pid}: {product_name}")

            except Exception as e:
                print(f"❌ {clean_pid} 失敗: {str(e)[:100]}", flush=True)
                driver.save_screenshot(f"{screenshot_dir}/error_{clean_pid}.png")
                worksheet.update(range_name=f"D{row_idx}", values=[["Error"]])

        zip_filename = f"Pokemon_{get_time_str_for_filename()}.zip"
        print(f"📦 打包截圖: {zip_filename}", flush=True)
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(screenshot_dir):
                for file in files:
                    zipf.write(os.path.join(root, file), file)

        subject = f"Pokemon商品查詢結果-共{total_items}筆，成功{success_items}筆"
        html_list = "".join([f"<li>{s}</li>" for s in summary_list])
        body = f"""<html><body><h2>Pokemon Center 商品查詢報告</h2>
            <ul><li>查詢總數: {total_items}</li><li>成功: {success_items}</li><li>未找到: {not_found_items}</li></ul>
            <p><b>明細:</b></p><ul>{html_list}</ul></body></html>"""
        send_email(subject, body, zip_filename)
        print("🎉 任務完成！", flush=True)

    except Exception as main_e:
        print(f"💥 程式崩潰: {main_e}", flush=True)
    finally:
        driver.quit()
        if os.path.exists(screenshot_dir): shutil.rmtree(screenshot_dir)

if __name__ == "__main__":
    main()
