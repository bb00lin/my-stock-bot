import time
import gspread
import re 
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
    # 移除 SGD, $, 逗號, 換行符號, 空格
    return price_text.replace("SGD", "").replace("$", "").replace(",", "").replace("\n", "").replace(" ", "").strip()

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
    """ 強力清空購物車模式 (修正版：檢查金額是否歸零) """
    print("🧹 正在清空購物車...")
    max_retries = 6 # 增加重試次數
    
    if "cart" not in driver.current_url:
        driver.get("https://guardian.com.sg/cart")
        time.sleep(3)

    for i in range(max_retries):
        try:
            # 1. 嘗試尋找並點擊移除按鈕
            remove_btns = driver.find_elements(By.CSS_SELECTOR, 
                "button[aria-label='remove from cart'], button[aria-label='Remove item'], button.remove, button.action-delete")
            
            if remove_btns:
                print(f"   🗑️ 發現 {len(remove_btns)} 個移除按鈕，正在點擊第 1 個...")
                # 使用 JS 點擊避免被擋住
                driver.execute_script("arguments[0].click();", remove_btns[0])
                time.sleep(3)
                # 刪除後，直接進入下一次迴圈檢查
                continue

            # 2. 如果沒按鈕，檢查 Subtotal 金額是否真的為 0
            # (避免因為網頁延遲，按鈕還沒跑出來就以為空了)
            try:
                # 嘗試抓取 Cart Summary 文字
                summary_box = driver.find_element(By.CSS_SELECTOR, "div.cart-summary, div.cart-totals, div[class*='summary']")
                summary_text = summary_box.text
                
                # 如果還看得到 "Subtotal"，且金額不是 0.00
                if "Subtotal" in summary_text and "SGD 0.00" not in summary_text and "SGD 0 " not in summary_text:
                    print("   ⚠️ 偵測到金額不為 0，但找不到移除按鈕，嘗試刷新頁面...")
                    driver.refresh()
                    time.sleep(5)
                    continue
            except:
                # 如果找不到 Summary 區塊，通常代表購物車是全空的 (顯示 Empty Cart 圖片)
                pass

            # 3. 雙重檢查：確認是否有商品數量輸入框
            items = driver.find_elements(By.CSS_SELECTOR, "input.item-qty")
            if not items:
                print("   ✅ 購物車已確認清空")
                break
            else:
                print("   ⚠️ 仍偵測到商品輸入框，重試中...")
                driver.refresh()
                time.sleep(3)
                continue

        except Exception as e:
            print(f"   ⚠️ 清空過程重試中: {e}")
            time.sleep(2)
            continue

# ================= 核心邏輯 =================
def get_price_safely(driver):
    """ 使用 Regex 與多重策略抓取價格 """
    
    # === 策略 1: Regex 暴力搜尋 (最強) ===
    try:
        summary_box = driver.find_element(By.CSS_SELECTOR, "div.cart-summary, div.cart-totals, div[class*='summary']")
        box_text = summary_box.text.replace("\n", " ") 
        
        # 搜尋 "Subtotal" 附近是否有數字
        match = re.search(r'Subtotal.*?SGD\s*([\d\.]+)', box_text, re.IGNORECASE)
        if match:
            return clean_price(match.group(1))
    except:
        pass

    # === 策略 2: XPath 精準定位 (備用) ===
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

        time.sleep(5)

        # 2. 點擊商品進入內頁 (JS 強制點擊)
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
            driver.save_screenshot(f"debug_not_found_{sku}.png")
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
            driver.save_screenshot(f"error_cart_{sku}.png")
            return ["Add Fail"] * 5

        time.sleep(5)

        # 4. 調整數量與抓取價格
        for qty in range(1, 6):
            try:
                WebDriverWait(driver, 5).until_not(EC.presence_of_element_located((By.CSS_SELECTOR, ".loading-mask, .loader")))
            except:
                pass
            
            time.sleep(2)

            # === 抓取價格 ===
            current_price = get_price_safely(driver)
            
            if current_price:
                prices[qty] = current_price
                print(f"   💰 數量 {qty}: SGD {current_price}")
            else:
                print("   ⚠️ 找不到價格欄位")
                prices[qty] = "Error"
                driver.save_screenshot(f"error_price_{sku}_qty{qty}.png")

            # 增加數量
            if qty < 5:
                try:
                    plus_btn = driver.find_element(By.CSS_SELECTOR, "button[aria-label='Increase Quantity']")
                    driver.execute_script("arguments[0].click();", plus_btn)
                    time.sleep(4) 
                    
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
                    print("   ⚠️ 無法點擊 + 按鈕")
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
