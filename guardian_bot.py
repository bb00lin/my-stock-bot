import time
import gspread
import re
import os
import shutil
import random
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
SHEET_NAME = 'Guardian_Price_Check'
CREDENTIALS_FILE = 'google_key.json'
URL = "https://guardian.com.sg/"

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

def connect_google_sheet():
    print("📊 正在連線 Google Sheet...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
    return sheet

# ================= Selenium 功能 =================
def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    # === 升級：反偵測設定 ===
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    options.add_argument("--disable-blink-features=AutomationControlled") 
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    
    # 防止 WebDriver 特徵被偵測
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    return driver

def handle_popups(driver):
    """ 嘗試關閉可能遮擋視線的彈窗 """
    try:
        # 這裡列出常見的彈窗關閉按鈕選擇器
        popups = [
            "button[aria-label='Close']", 
            "div.close-popup", 
            "button.align-right.secondary.slidedown-button", # 常見的 Cookie 同意按鈕
            "#onetrust-accept-btn-handler" # Cookie 同意
        ]
        for p in popups:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, p)
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    print("   👋 已關閉一個阻擋視窗")
                    time.sleep(1)
            except:
                pass
    except:
        pass

def empty_cart(driver):
    print("🧹 正在執行核彈級清空 (刪除 Cookies)...")
    try:
        # 確保在網域內才能清
        if "guardian.com.sg" not in driver.current_url:
             driver.get("https://guardian.com.sg/")
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
    product_url = "" 
    
    sku_folder = str(sku)
    if os.path.exists(sku_folder):
        shutil.rmtree(sku_folder) 
    os.makedirs(sku_folder)
    
    try:
        driver.get(URL)
        time.sleep(5)
        handle_popups(driver) # 嘗試關閉彈窗

        # 1. 搜尋 (增強版選擇器)
        try:
            search_input = None
            selectors = [
                "input[placeholder*='Search']", # 模糊比對 placeholder
                "input[name='q']", 
                "input[type='search']",
                "input.search-input"
            ]
            
            for selector in selectors:
                try:
                    search_input = WebDriverWait(driver, 5).until(
                        EC.visibility_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    if search_input:
                        break
                except:
                    continue
            
            if not search_input:
                raise TimeoutException("找不到搜尋框")

            search_input.clear()
            search_input.send_keys(sku)
            time.sleep(1)
            search_input.send_keys(Keys.RETURN)
        except TimeoutException:
            print("❌ 搜尋框載入超時 (可能網站載入慢或被阻擋)")
            driver.save_screenshot(f"{sku_folder}/{sku}_search_fail.png")
            return ["Search Fail"] * 5, "URL Not Found"

        time.sleep(5)
        handle_popups(driver)

        # 2. 點擊商品 (並確認是否進入內頁)
        try:
            xpath_selectors = [
                f"//a[contains(@href, '{sku}')]", # 最準：連結包含 SKU
                "(//div[contains(@class, 'product')]//a)[1]", 
                "(//main//a[.//img])[1]", 
                "//div[data-testid='product-card']//a"
            ]
            
            clicked = False
            for xpath in xpath_selectors:
                try:
                    product_link = driver.find_element(By.XPATH, xpath)
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", product_link)
                    time.sleep(1)
                    # 嘗試一般點擊
                    try:
                        product_link.click()
                    except:
                        # 失敗則用 JS 點擊
                        driver.execute_script("arguments[0].click();", product_link)
                    clicked = True
                    break
                except:
                    continue
            
            if not clicked:
                raise NoSuchElementException("無法找到任何商品連結")
            
            # === 關鍵：等待網址改變，確認離開搜尋頁 ===
            print("👉 已嘗試點擊商品，驗證跳轉中...")
            try:
                WebDriverWait(driver, 10).until(
                    lambda d: "search.html" not in d.current_url
                )
            except:
                print("   ⚠️ 警告：網址似乎仍停留在搜尋頁，可能點擊失敗")
            
            time.sleep(2) 
            product_url = driver.current_url
            print(f"🔗 取得目前連結: {product_url}")

            # 二次確認：如果還在搜尋頁，回傳失敗
            if "search.html" in product_url:
                print("❌ 點擊後仍停留在搜尋結果頁，視為失敗")
                driver.save_screenshot(f"{sku_folder}/{sku}_click_fail.png")
                return ["Click Fail"] * 5, product_url

        except NoSuchElementException:
            print(f"⚠️ 搜尋不到 SKU {sku}")
            driver.save_screenshot(f"{sku_folder}/{sku}_not_found.png")
            return ["Not Found"] * 5, "URL Not Found"

        time.sleep(4)
        handle_popups(driver)

        # 3. 加入購物車
        try:
            add_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart'], button.action.tocart"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", add_btn)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", add_btn)
            print("🛒 已點擊加入購物車，等待處理...")
            time.sleep(5) 
            driver.get("https://guardian.com.sg/cart")
        except TimeoutException:
            print("❌ 加入購物車按鈕找不到 (可能商品缺貨或未正確進入內頁)")
            driver.save_screenshot(f"{sku_folder}/{sku}_add_fail.png")
            return ["Add Fail"] * 5, product_url

        time.sleep(5)

        # 4. 調整數量與抓取價格
        for qty in range(1, 6):
            try:
                WebDriverWait(driver, 15).until_not(
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')] | //div[contains(@class, 'loading-mask')]"))
                )
            except:
                pass
            
            time.sleep(1) 

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
                    
                    print(f"   ⏳ 正在增加數量 ({qty}->{qty+1})...")
                    time.sleep(1) 
                    try:
                        WebDriverWait(driver, 20).until_not(
                            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'FETCHING CART')] | //div[contains(@class, 'loading-mask')]"))
                        )
                    except TimeoutException:
                        print("   ⚠️ 等待價格更新超時，嘗試繼續...")

                    time.sleep(2) 

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

        # === 打包截圖 ===
        print("📦 正在打包截圖...")
        timestamp = get_taiwan_time_str()
        zip_filename = f"{sku}_{timestamp}"
        shutil.make_archive(zip_filename, 'zip', sku_folder)
        shutil.rmtree(sku_folder) 

        return prices, product_url

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        try:
            if 'sku_folder' in locals() and os.path.exists(sku_folder):
                 driver.save_screenshot(f"{sku_folder}/{sku}_exception.png")
            empty_cart(driver)
        except:
            pass
        return ["Error"] * 5, product_url

# ================= 主程式 =================
def main():
    try:
        sheet = connect_google_sheet()
        driver = init_driver()
        
        print("--- 初始化檢查 ---")
        empty_cart(driver)
        
        all_values = sheet.get_all_values()
        print(f"📋 共有 {len(all_values)-1} 筆資料待處理")

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

            web_prices, product_url = process_sku(driver, sku)
            
            update_time = get_taiwan_time_display()
            comparison_result = compare_prices(user_prices, web_prices)
            
            data_to_write = web_prices + [update_time, comparison_result, product_url]
            
            cell_range = f"H{i}:O{i}"
            sheet.update(values=[data_to_write], range_name=cell_range)
            
            print(f"✅ SKU {sku} 完成 | 結果: {comparison_result} | URL: {product_url}")
            print("-" * 30)

        print("🎉 所有任務完成！")
        driver.quit()
        
    except Exception as main_e:
        print(f"💥 程式執行發生重大錯誤: {main_e}")
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    main()
