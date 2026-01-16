import time
import gspread
import re
import os
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
SHEET_NAME = 'Guardian_Price_Check'
CREDENTIALS_FILE = 'google_key.json'
URL = "https://guardian.com.sg/"

# ================= 輔助功能 =================
def clean_price(price_text):
    """ 清理價格字串，移除貨幣符號、逗號、空格，只留數字 """
    if not price_text:
        return ""
    cleaned = str(price_text).replace("SGD", "").replace("$", "").replace(",", "").replace("\n", "").replace(" ", "").strip()
    return cleaned

def get_taiwan_time():
    """ 取得台灣時間 (UTC+8) 的字串 """
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    return now.strftime("%Y-%m-%d %H:%M")

def safe_get(row_list, index):
    """ 安全取得 List 中的值，防止 Index 超出範圍 """
    if index < len(row_list):
        return str(row_list[index])
    return ""

def compare_prices(user_prices, web_prices):
    """ 比對使用者輸入價格與網頁抓取價格 """
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
            w_num = float(w_val) if w_val and w_val != "Error" and w_val != "N/A" and w_val != "Limit Reached" else -999
            
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
        return "無使用者數據"
    if not mismatches:
        return "均相符"
    else:
        return "; ".join(mismatches)

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

def connect_google_sheet():
    print("📊 正在連線 Google Sheet...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
    return sheet

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
        print("   ✅ 瀏覽器記憶已清除，購物車已歸零")
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
            return ["Search Fail"] * 5

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
            else:
                raise NoSuchElementException("無法找到任何商品連結")

        except NoSuchElementException:
            print(f"⚠️ 搜尋不到 SKU {sku}")
            return ["Not Found"] * 5

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
            print("🚀 直接跳轉至購物車頁面...")
            driver.get("https://guardian.com.sg/cart")
            
        except TimeoutException:
            print("❌ 加入購物車按鈕找不到")
            return ["Add Fail"] * 5

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
            else:
                print("   ⚠️ 找不到價格欄位")
                prices.append("Error")

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

        print(f"📸 正在儲存 SKU {sku} 的價格截圖...")
        driver.save_screenshot(f"proof_{sku}.png")

        empty_cart(driver)

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        driver.save_screenshot(f"error_{sku}.png")
        try:
            empty_cart(driver)
        except:
            pass
        return ["Error"] * 5

    return prices

# ================= 主程式 =================
def main():
    try:
        sheet = connect_google_sheet()
        driver = init_driver()
        
        print("--- 初始化檢查 ---")
        empty_cart(driver)
        
        # === 關鍵修改：改用 get_all_values() 避免標題重複錯誤 ===
        all_values = sheet.get_all_values()
        print(f"📋 共有 {len(all_values)-1} 筆資料待處理")

        # 從索引 1 開始（即第 2 列），all_values[0] 是標題列
        for i, row_data in enumerate(all_values[1:], start=2):
            # A欄是 SKU (Index 0)
            sku = safe_get(row_data, 0).strip()
            if not sku:
                continue
            
            # C~G欄是 User Price (Index 2~6)
            user_prices = [
                safe_get(row_data, 2), # C
                safe_get(row_data, 3), # D
                safe_get(row_data, 4), # E
                safe_get(row_data, 5), # F
                safe_get(row_data, 6)  # G
            ]

            # 執行爬蟲
            web_prices = process_sku(driver, sku)
            
            # 時間
            update_time = get_taiwan_time()

            # 比對
            comparison_result = compare_prices(user_prices, web_prices)
            
            # 寫入 H~N 欄
            data_to_write = web_prices + [update_time, comparison_result]
            
            cell_range = f"H{i}:N{i}"
            sheet.update(values=[data_to_write], range_name=cell_range)
            
            print(f"✅ SKU {sku} 完成 | 結果: {comparison_result}")
            print("-" * 30)

        print("🎉 所有任務完成！")
        driver.quit()
        
    except Exception as main_e:
        print(f"💥 程式執行發生重大錯誤: {main_e}")
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    main()
