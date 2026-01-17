import time
import gspread
import re
import os
import shutil
from datetime import datetime, timedelta, timezone
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ================= 設定區 =================
SHEET_NAME = 'Guardian_Price_Check'
CREDENTIALS_FILE = 'google_key.json'
URL = "https://guardian.com.sg/"

# ★★★ 您的 Google Drive 資料夾 ID ★★★
DRIVE_FOLDER_ID = '19ZAatbWczApRUMVbF0ZB6X-T36YY2w35'

# ================= 輔助功能 =================
def clean_price(price_text):
    if not price_text:
        return ""
    cleaned = str(price_text).replace("SGD", "").replace("$", "").replace(",", "").replace("\n", "").replace(" ", "").strip()
    return cleaned

def get_taiwan_time_str():
    """ 用於檔名，格式 YYYYMMDDHHMM """
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    return now.strftime("%Y%m%d%H%M")

def get_taiwan_time_display():
    """ 用於表格顯示，格式 YYYY-MM-DD HH:MM """
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M")

def safe_get(row_list, index):
    if index < len(row_list):
        return str(row_list[index])
    return ""

def compare_prices(user_prices, web_prices):
    mismatches = []
    match_count = 0
    valid_comparison_count = 0

    for i in range(5):
        u_raw = user_prices[i]
        w_raw = web_prices[i]
        u_val = clean_price(u_raw)
        w_val = clean_price(w_raw)

        if not u_val:
            continue
        valid_comparison_count += 1

        try:
            u_num = float(u_val)
            w_num = float(w_val) if w_val and w_val not in ["Error", "N/A", "Limit Reached"] else -999
            if abs(u_num - w_num) < 0.01: 
                match_count += 1
            else:
                mismatches.append(f"Q{i+1}:User({u_val})!=Web({w_val})")
        except:
            if u_val == w_val:
                match_count += 1
            else:
                mismatches.append(f"Q{i+1}:Diff")

    if valid_comparison_count == 0:
        return ""
    if not mismatches:
        return "均相符"
    else:
        return "; ".join(mismatches)

# ================= Google Service 連線 =================
def get_credentials():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    return ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)

def connect_google_sheet():
    print("📊 正在連線 Google Sheet...")
    creds = get_credentials()
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
    return sheet

def upload_to_drive(file_path, file_name):
    """ 上傳檔案到 Google Drive 並回傳連結 """
    print(f"☁️ 正在上傳 {file_name} 到 Google Drive...")
    try:
        creds = get_credentials()
        # 建立 Drive API 服務
        service = build('drive', 'v3', credentials=creds)
        
        file_metadata = {
            'name': file_name,
            'parents': [DRIVE_FOLDER_ID]
        }
        media = MediaFileUpload(file_path, mimetype='application/zip')
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink',
            supportsAllDrives=True # 嘗試支援共享雲端硬碟
        ).execute()
        
        print(f"   ✅ 上傳成功! File ID: {file.get('id')}")
        return file.get('webViewLink')
        
    except HttpError as error:
        # 特別處理空間不足的錯誤 (Error 403 reason: storageQuotaExceeded)
        if error.resp.status == 403 and 'storageQuotaExceeded' in str(error):
            print("   ⚠️ 上傳失敗：Service Account 儲存空間不足 (Google 限制)。請改用 GitHub Artifacts 下載。")
            return "上傳失敗(空間不足)"
        else:
            print(f"   ❌ 上傳 Google Drive 失敗: {error}")
            return "上傳失敗"
    except Exception as e:
        print(f"   ❌ 發生未預期錯誤: {e}")
        return "上傳失敗"

# ================= Selenium 功能 =================
def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def empty_cart(driver):
    print("🧹 正在執行核彈級清空 (刪除 Cookies)...")
    try:
        if "guardian.com.sg" not in driver.current_url:
             driver.get("https://guardian.com.sg/cart")
             time.sleep(2)
        driver.delete_all_cookies()
        driver.execute_script("window.localStorage.clear();")
        driver.execute_script("window.sessionStorage.clear();")
        driver.refresh()
        time.sleep(4) 
    except Exception as e:
        print(f"   ⚠️ 清空過程發生小錯誤: {e}")

def get_price_safely(driver):
    try:
        summary_box = driver.find_element(By.CSS_SELECTOR, "div.cart-summary, div.cart-totals, div[class*='summary']")
        box_text = summary_box.text.replace("\n", " ") 
        match = re.search(r'Subtotal.*?SGD\s*([\d\.]+)', box_text, re.IGNORECASE)
        if match:
            return clean_price(match.group(1))
    except:
        pass

    xpaths = [
        "//div[contains(text(), 'Subtotal')]/following-sibling::span",
        "//*[contains(text(), 'Subtotal')]/..//*[contains(text(), 'SGD')]",
        "//span[contains(@class, 'price')][contains(text(), '.')]"
    ]
    for xpath in xpaths:
        try:
            element = driver.find_element(By.XPATH, xpath)
            text = element.text.strip()
            cleaned = clean_price(text)
            if cleaned.replace(".", "").isdigit():
                return cleaned
        except:
            continue
    return None

def process_sku(driver, sku):
    print(f"\n🔍 開始搜尋 SKU: {sku}")
    prices = [] 
    product_url = "" # 初始化商品連結
    
    # === 建立暫存資料夾 ===
    sku_folder = str(sku)
    if os.path.exists(sku_folder):
        shutil.rmtree(sku_folder) 
    os.makedirs(sku_folder)
    
    try:
        driver.get(URL)
        time.sleep(3)

        # 1. 搜尋
        try:
            search_box = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder='Search for a products or brand']"))
            )
            search_box.clear()
            search_box.send_keys(sku)
            search_box.send_keys(Keys.RETURN)
        except TimeoutException:
            print("❌ 搜尋框載入超時")
            return ["Search Fail"] * 5, "", "URL Not Found"

        time.sleep(5)

        # 2. 點擊商品
        try:
            xpath_selectors = [
                "(//div[contains(@class, 'product')]//a)[1]", 
                "(//main//a[.//img])[1]", 
                "//div[data-testid='product-card']//a"
            ]
            first_product = None
            for xpath in xpath_selectors:
                try:
                    first_product = driver.find_element(By.XPATH, xpath)
                    break
                except:
                    continue
            
            if first_product:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", first_product)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", first_product)
                print("👉 (JS強制) 成功點擊商品，進入內頁")
                
                # === 新增：抓取商品連結 ===
                time.sleep(2) # 等待網址跳轉
                product_url = driver.current_url
                print(f"🔗 取得商品連結: {product_url}")
                # ========================
            else:
                raise NoSuchElementException("無法找到任何商品連結")

        except NoSuchElementException:
            print(f"⚠️ 搜尋不到 SKU {sku}")
            return ["Not Found"] * 5, "", "URL Not Found"

        time.sleep(4)

        # 3. 加入購物車
        try:
            add_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart']"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", add_btn)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", add_btn)
            print("🛒 已點擊加入購物車，等待處理...")
            time.sleep(5) 
            driver.get("https://guardian.com.sg/cart")
        except TimeoutException:
            print("❌ 加入購物車按鈕找不到")
            return ["Add Fail"] * 5, "", product_url

        time.sleep(5)

        # 4. 調整數量與抓取價格
        for qty in range(1, 6):
            try:
                WebDriverWait(driver, 5).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".loading-mask, .loader")))
            except:
                pass
            time.sleep(2)

            current_price = get_price_safely(driver)
            
            if current_price:
                prices.append(current_price)
                print(f"   💰 數量 {qty}: SGD {current_price}")
                driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}.png")
            else:
                print("   ⚠️ 找不到價格欄位")
                prices.append("Error")
                driver.save_screenshot(f"{sku_folder}/{sku}_qty{qty}_error.png")

            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    driver.execute_script("arguments[0].click();", plus_btn)
                    time.sleep(4) 
                    try:
                        error_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')]")
                        if error_msg.is_displayed():
                            print("   🛑 達到購買上限")
                            for _ in range(qty, 5):
                                prices.append("Limit Reached")
                            break
                    except:
                        pass
                except Exception:
                    print("   ⚠️ 無法點擊 + 按鈕")
                    break
        
        while len(prices) < 5:
            prices.append("Error")
        
        empty_cart(driver)

        # === 打包與上傳 ===
        print("📦 正在打包截圖...")
        timestamp = get_taiwan_time_str()
        zip_filename = f"{sku}_{timestamp}"
        zip_path = shutil.make_archive(zip_filename, 'zip', sku_folder)
        
        # 上傳到 Google Drive
        drive_link = upload_to_drive(zip_path, f"{zip_filename}.zip")
        
        # 清理暫存檔
        shutil.rmtree(sku_folder)
        os.remove(zip_path)

        return prices, drive_link, product_url

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        try:
            if 'sku_folder' in locals() and os.path.exists(sku_folder):
                 driver.save_screenshot(f"{sku_folder}/{sku}_exception.png")
            empty_cart(driver)
        except:
            pass
        return ["Error"] * 5, "上傳失敗", product_url

# ================= 主程式 =================
def main():
    try:
        sheet = connect_google_sheet()
        driver = init_driver()
        
        print("--- 初始化檢查 ---")
        empty_cart(driver)
        
        all_values = sheet.get_all_values()
        print(f"📋 共有 {len(all_values)-1} 筆資料待處理")

        # 從第 2 列開始
        for i, row_data in enumerate(all_values[1:], start=2):
            sku = safe_get(row_data, 0).strip()
            if not sku:
                continue
            
            user_prices = [
                safe_get(row_data, 2), # C
                safe_get(row_data, 3), # D
                safe_get(row_data, 4), # E
                safe_get(row_data, 5), # F
                safe_get(row_data, 6)  # G
            ]

            # 執行爬蟲，回傳 (價格, 雲端連結, 商品網址)
            web_prices, drive_link, product_url = process_sku(driver, sku)
            
            update_time = get_taiwan_time_display()
            comparison_result = compare_prices(user_prices, web_prices)
            
            # 寫入: H~L (Prices) + M (Time) + N (Result) + O (Drive Link) + P (Product URL)
            data_to_write = web_prices + [update_time, comparison_result, drive_link, product_url]
            
            # 寫入到 P 欄 (第16欄)
            cell_range = f"H{i}:P{i}"
            sheet.update(values=[data_to_write], range_name=cell_range)
            
            print(f"✅ SKU {sku} 完成 | 結果: {comparison_result} | Link: {drive_link} | URL: {product_url}")
            print("-" * 30)

        print("🎉 所有任務完成！")
        driver.quit()
        
    except Exception as main_e:
        print(f"💥 程式執行發生重大錯誤: {main_e}")
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    main()
