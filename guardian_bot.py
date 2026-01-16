import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

# ================= 設定區 =================
SHEET_NAME = 'Guardian_Price_Check'
CREDENTIALS_FILE = 'google_key.json'
URL = "https://guardian.com.sg/"

# ================= 輔助功能 =================
def clean_price(price_text):
    if not price_text:
        return "N/A"
    return price_text.replace("SGD", "").replace("$", "").replace(",", "").strip()

def init_driver():
    options = webdriver.ChromeOptions()
    # === GitHub Actions 設定 ===
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
    print("🧹 正在清空購物車...")
    max_retries = 5 # 減少重試次數加快速度
    
    for _ in range(max_retries):
        try:
            if "cart" not in driver.current_url:
                driver.get("https://guardian.com.sg/cart")
                time.sleep(3)

            remove_btns = driver.find_elements(By.CSS_SELECTOR, "button[aria-label='remove from cart']")
            if not remove_btns:
                print("   ✅ 購物車已清空")
                break
            
            print(f"   🗑️ 發現 {len(remove_btns)} 個商品，正在移除...")
            remove_btns[0].click()
            time.sleep(2)
            try:
                WebDriverWait(driver, 5).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".loading-mask, .loader")))
            except:
                time.sleep(2)
        except Exception:
            break

# ================= 核心邏輯 =================
def process_sku(driver, sku):
    print(f"\n🔍 開始搜尋 SKU: {sku}")
    prices = {} 
    
    try:
        driver.get(URL)
        time.sleep(3)

        # 1. 搜尋商品
        try:
            search_box = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder='Search for a products or brand']"))
            )
            search_box.clear()
            search_box.send_keys(sku)
            search_box.send_keys(Keys.RETURN)
        except TimeoutException:
            print("❌ 搜尋框載入超時")
            driver.save_screenshot(f"error_search_{sku}.png")
            return ["Search Fail"] * 5

        time.sleep(5) # 給多一點時間載入搜尋結果

        # 2. 點擊商品進入內頁 (修正版：更通用的抓取邏輯)
        try:
            # 嘗試抓取搜尋結果區域中的第一個連結
            # 邏輯：找任何包含圖片的連結，或是商品卡片連結
            xpath_selectors = [
                "(//div[contains(@class, 'product')]//a)[1]", # 嘗試找商品區塊的連結
                "(//main//a[.//img])[1]", # 嘗試找主內容區第一個有圖片的連結
                "//div[data-testid='product-card']//a" # 嘗試 data-testid
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
                first_product.click()
                print("👉 成功點擊商品，進入內頁")
            else:
                raise NoSuchElementException("無法找到任何商品連結")

        except NoSuchElementException:
            print(f"⚠️ 搜尋不到 SKU {sku} (或找不到連結)")
            # === 關鍵：拍下截圖以便除錯 ===
            driver.save_screenshot(f"debug_not_found_{sku}.png")
            print(f"📸 已儲存截圖: debug_not_found_{sku}.png")
            return ["Not Found"] * 5

        time.sleep(4)

        # 3. 加入購物車
        try:
            add_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart']"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", add_btn)
            time.sleep(1)
            add_btn.click()
            print("🛒 已點擊加入購物車")

            go_cart_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='GO TO CART']"))
            )
            go_cart_btn.click()
            print("🚀 前往結帳頁面...")
            
        except TimeoutException:
            print("❌ 加入購物車失敗")
            driver.save_screenshot(f"error_cart_{sku}.png")
            return ["Add Fail"] * 5

        time.sleep(5)

        # 4. 調整數量與抓取價格
        for qty in range(1, 6):
            try:
                WebDriverWait(driver, 5).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".loading-mask, .loader")))
            except:
                pass

            try:
                # 抓取 Subtotal
                subtotal_element = driver.find_element(By.XPATH, "//div[contains(text(), 'Subtotal')]/following-sibling::span")
                current_price = clean_price(subtotal_element.text)
                prices[qty] = current_price
                print(f"   💰 數量 {qty}: SGD {current_price}")
            except NoSuchElementException:
                print("   ⚠️ 找不到價格欄位")
                prices[qty] = "Error"

            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    plus_btn.click()
                    time.sleep(3)
                    
                    try:
                        error_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')]")
                        if error_msg.is_displayed():
                            print("   🛑 達到購買上限")
                            for r in range(qty + 1, 6):
                                prices[r] = "Limit Reached"
                            break
                    except:
                        pass
                except Exception:
                    break

        empty_cart(driver)

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        driver.save_screenshot(f"error_exception_{sku}.png")
        try:
            empty_cart(driver)
        except:
            pass
        return ["Error"] * 5

    return [prices.get(i, "N/A") for i in range(1, 6)]

# ================= 主程式 =================
def main():
    try:
        sheet = connect_google_sheet()
        driver = init_driver()
        
        print("--- 初始化檢查 ---")
        empty_cart(driver)
        
        records = sheet.get_all_records()
        print(f"📋 共有 {len(records)} 筆 SKU 待處理")

        for i, row in enumerate(records, start=2):
            sku = str(row.get('SKU', '')).strip()
            if not sku:
                continue
            
            price_data = process_sku(driver, sku)
            
            # === 修正後的 gspread 寫法 (解決黃色警告) ===
            cell_range = f"C{i}:G{i}"
            sheet.update(values=[price_data], range_name=cell_range)
            
            print(f"✅ SKU {sku} 更新完畢: {price_data}")
            print("-" * 30)

        print("🎉 所有任務完成！")
        driver.quit()
        
    except Exception as main_e:
        print(f"💥 程式執行發生重大錯誤: {main_e}")
        if 'driver' in locals():
            driver.quit()

if __name__ == "__main__":
    main()
