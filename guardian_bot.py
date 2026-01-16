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

# ================= 設定區 (請確認這裡) =================
# 您的 Google Sheet 名稱
SHEET_NAME = 'Guardian_Price_Check' 
# JSON 金鑰檔名
CREDENTIALS_FILE = 'credentials.json'
# Guardian 網站網址
URL = "https://guardian.com.sg/"

# ================= 輔助功能 =================
def clean_price(price_text):
    """ 清理價格字串，移除 'SGD'、'$' 和空格，只留數字 """
    if not price_text:
        return "N/A"
    return price_text.replace("SGD", "").replace("$", "").replace(",", "").strip()

def init_driver():
    """ 啟動 Chrome 瀏覽器 """
    options = webdriver.ChromeOptions()
    options.add_argument('--start-maximized') # 視窗最大化
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

def connect_google_sheet():
    """ 連線到 Google Sheet """
    print("📊 正在連線 Google Sheet...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
    return sheet

def empty_cart(driver):
    """ 專門用來清空購物車的函式 (基於您的最新截圖) """
    print("🧹 正在清空購物車...")
    max_retries = 10 # 避免無窮迴圈
    
    for _ in range(max_retries):
        try:
            # 1. 確保在購物車頁面
            if "cart" not in driver.current_url:
                driver.get("https://guardian.com.sg/cart")
                time.sleep(3)

            # 2. 尋找移除按鈕 (使用 aria-label="remove from cart")
            remove_btns = driver.find_elements(By.CSS_SELECTOR, "button[aria-label='remove from cart']")
            
            if not remove_btns:
                print("   ✅ 購物車已清空")
                break
            
            # 3. 點擊第一個移除按鈕
            print(f"   🗑️ 發現 {len(remove_btns)} 個商品，正在移除...")
            remove_btns[0].click()
            
            # 4. 等待讀取畫面消失 (Loading Spinner)
            time.sleep(2)
            try:
                WebDriverWait(driver, 5).until_not(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".loading-mask, .loader"))
                )
            except:
                time.sleep(2) # 如果沒抓到 spinner 就硬等一下

        except (StaleElementReferenceException, TimeoutException):
            continue # 頁面刷新了，重跑迴圈再找一次
        except Exception as e:
            print(f"   ⚠️ 清空購物車時發生小錯誤: {e}")
            break

# ================= 核心邏輯 =================
def process_sku(driver, sku):
    """ 針對單一 SKU 執行完整流程 """
    print(f"\n🔍 開始搜尋 SKU: {sku}")
    prices = {} 
    
    try:
        driver.get(URL)
        time.sleep(2)

        # 1. 搜尋商品
        try:
            search_box = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder='Search for a products or brand']"))
            )
            search_box.clear()
            search_box.send_keys(sku)
            search_box.send_keys(Keys.RETURN)
        except TimeoutException:
            print("❌ 搜尋框載入超時")
            return ["Search Fail"] * 5

        time.sleep(4) 

        # 2. 點擊商品進入內頁
        try:
            first_product = driver.find_element(By.CSS_SELECTOR, "div.product-item a, a.product-item-link")
            first_product.click()
            print("👉 進入商品頁面")
        except NoSuchElementException:
            print(f"⚠️ 搜尋不到 SKU {sku}")
            return ["Not Found"] * 5

        time.sleep(3)

        # 3. 加入購物車 (兩段式)
        try:
            # 3-1. 點擊 Add to Cart
            add_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='Add to Cart']"))
            )
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", add_btn)
            time.sleep(1)
            add_btn.click()
            print("🛒 已點擊加入購物車")

            # 3-2. 點擊 GO TO CART
            go_cart_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button[aria-label='GO TO CART']"))
            )
            go_cart_btn.click()
            print("🚀 前往結帳頁面...")
            
        except TimeoutException:
            print("❌ 加入購物車失敗")
            return ["Add Fail"] * 5

        time.sleep(5) # 等待購物車載入

        # 4. 在購物車頁面：調整數量並抓價格
        for qty in range(1, 6):
            # 等待讀取轉圈圈消失
            try:
                WebDriverWait(driver, 5).until_not(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".loading-mask, .loader"))
                )
            except:
                pass

            # 4-1. 抓取 Subtotal
            try:
                # 依據截圖抓取 Subtotal 數字
                subtotal_element = driver.find_element(By.XPATH, "//div[contains(text(), 'Subtotal')]/following-sibling::span")
                current_price = clean_price(subtotal_element.text)
                prices[qty] = current_price
                print(f"   💰 數量 {qty}: SGD {current_price}")
            except NoSuchElementException:
                print("   ⚠️ 找不到價格欄位")
                prices[qty] = "Error"

            # 4-2. 增加數量 (1->5)
            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    plus_btn.click()
                    time.sleep(3) # 等待價格更新
                    
                    # 檢查限購訊息
                    try:
                        error_msg = driver.find_element(By.XPATH, "//*[contains(text(), 'maximum purchase quantity')]")
                        if error_msg.is_displayed():
                            print("   🛑 達到購買上限")
                            for r in range(qty + 1, 6):
                                prices[r] = "Limit Reached"
                            break
                    except:
                        pass
                except Exception as e:
                    print(f"   ⚠️ 無法增加數量: {e}")
                    break

        # 5. 執行清空購物車 (使用新截圖邏輯)
        empty_cart(driver)

    except Exception as e:
        print(f"❌ 發生錯誤: {e}")
        # 即使出錯也要嘗試清空購物車，以免影響下一個
        try:
            empty_cart(driver)
        except:
            pass
        return ["Error"] * 5

    return [prices.get(i, "N/A") for i in range(1, 6)]

# ================= 主程式執行 =================
def main():
    sheet = connect_google_sheet()
    driver = init_driver()
    
    # 確保一開始購物車是空的
    driver.get("https://guardian.com.sg/cart")
    time.sleep(3)
    empty_cart(driver)
    
    records = sheet.get_all_records()
    print(f"📋 共有 {len(records)} 筆 SKU 待處理")

    # 從第 2 行開始 (視您的 Sheet 標題列而定)
    for i, row in enumerate(records, start=2):
        sku = str(row.get('SKU', '')).strip()
        if not sku:
            continue
            
        price_data = process_sku(driver, sku)
        
        # 寫回 Google Sheet (C 到 G 欄)
        cell_range = f"C{i}:G{i}"
        sheet.update(cell_range, [price_data])
        
        print(f"✅ SKU {sku} 更新完畢")
        print("-" * 30)

    print("🎉 所有任務完成！")
    driver.quit()

if __name__ == "__main__":
    main()
