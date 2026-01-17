import os
import time
import re
from datetime import date, timedelta, datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

# --- 設定區 (從環境變數讀取，確保安全) ---
URL = os.environ.get("CONF_URL")  # Confluence 網址
USERNAME = os.environ.get("CONF_USER") # 您的 Email
PASSWORD = os.environ.get("CONF_PASS") # 您的密碼 或 API Token

# --- 日期計算邏輯 ---
def get_target_dates():
    today = date.today()
    # 取得本週一 (Monday = 0)
    monday = today - timedelta(days=today.weekday())
    # 取得本週日
    sunday = monday + timedelta(days=6)
    # 取得本週五 (用於檔名)
    friday = monday + timedelta(days=4)
    
    return {
        "monday_str": monday.strftime("%Y-%m-%d"),
        "sunday_str": sunday.strftime("%Y-%m-%d"),
        "friday_filename": friday.strftime("%Y%m%d") # 格式如 20260123
    }

def init_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # GitHub Actions 必須使用無頭模式
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # 本地測試時若想看畫面，可註解掉 "--headless"
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def login(driver):
    print("正在登入 Confluence...")
    driver.get(URL)
    wait = WebDriverWait(driver, 20)
    
    # 輸入 Email
    email_field = wait.until(EC.element_to_be_clickable((By.ID, "username")))
    email_field.send_keys(USERNAME)
    email_field.send_keys(Keys.RETURN)
    
    # 等待密碼欄位出現並輸入
    password_field = wait.until(EC.element_to_be_clickable((By.ID, "password")))
    password_field.send_keys(PASSWORD)
    password_field.send_keys(Keys.RETURN)
    
    # 確保登入完成，檢測是否進入首頁或指定頁面
    print("登入動作完成，等待頁面載入...")
    time.sleep(10) # 讓慢速的 Confluence 有時間跑完

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 15)
    
    # 定位所有的 Jira Macro 區塊
    # 根據提供的 HTML，我們抓取外層的 wrapper
    # 注意：因為修改後 DOM 會重繪，所以不能一次抓完 list loop，必須每次重新抓取 "還沒改過" 的或者用 index
    
    print("開始搜尋頁面上的 Jira 表格...")
    
    # 這裡採用一個策略：不斷尋找並修改，直到找不到未修改的日期，或是遍歷所有表格
    # 為了簡化，我們先假設依照順序修改
    
    # 進入 iframe (如果是 iframe) 或直接在頁面上編輯
    # 根據 HTML 結構，這些表格似乎直接嵌在 div 裡，不是 iframe，但編輯時會彈出 Modal
    
    # 抓取所有這類 div
    macro_locator = (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    macros = driver.find_elements(*macro_locator)
    print(f"共發現 {len(macros)} 個 Jira 表格。")

    for i in range(len(macros)):
        try:
            # 重新抓取元素，避免 StaleElementReferenceException
            current_macros = driver.find_elements(*macro_locator)
            if i >= len(current_macros):
                break
            
            target_macro = current_macros[i]
            
            # 1. 點擊表格以選取
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_macro)
            time.sleep(1)
            target_macro.click()
            
            # 2. 點擊下方的「編輯」按鈕 (通常是鉛筆圖示)
            # 這裡需要一個通用的 locator，通常選取後會浮現 toolbar
            # 嘗試尋找 Edit 按鈕 (這裡可能需要根據實際狀況調整 XPath)
            edit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Edit'] | //span[text()='Edit'] | //button[contains(., 'Edit')]")))
            edit_btn.click()
            print(f"正在編輯第 {i+1} 個表格...")

            # 3. 等待 JQL 編輯器出現 (利用您提供的 data-testid)
            jql_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            
            # 4. 取得目前 JQL 文字
            old_jql = jql_input.text
            
            # 5. 使用 Regex 替換日期
            # 目標：找到 "YYYY-M-D" 或 "YYYY-MM-DD"，替換成新的
            # 假設我們只換 `DURING` 裡面的日期
            
            new_jql = old_jql
            
            # 替換邏輯：尋找日期格式並強制換成本週一與週日
            # Regex 尋找: 4碼年-1或2碼月-1或2碼日
            date_pattern = r"\d{4}-\d{1,2}-\d{1,2}"
            
            dates_found = re.findall(date_pattern, old_jql)
            
            if len(dates_found) >= 2:
                # 假設第一個是開始日，第二個是結束日 (通常是這樣)
                # 這裡做得更細緻一點：保留原本的時間 (09:30)，只換日期
                # 但最簡單的方法是直接換掉整個日期字串
                
                # 替換第一個日期為本週一
                new_jql = re.sub(dates_found[0], date_info['monday_str'], new_jql, count=1)
                # 替換第二個日期為本週日
                new_jql = re.sub(dates_found[1], date_info['sunday_str'], new_jql, count=1)
                
                print(f"更新 JQL: {dates_found} -> {date_info['monday_str']} ~ {date_info['sunday_str']}")
                
                # 6. 輸入新的 JQL
                # 因為是 contenteditable，send_keys 容易出錯，建議先清空
                jql_input.click()
                
                # 全選並刪除 (Mac 用 Command, Windows/Linux 用 Control)
                # GitHub Actions 是 Linux 環境
                jql_input.send_keys(Keys.CONTROL + "a")
                jql_input.send_keys(Keys.BACK_SPACE)
                time.sleep(0.5)
                
                # 輸入新文字
                jql_input.send_keys(new_jql)
                jql_input.send_keys(Keys.ENTER) # 觸發驗證
                time.sleep(1)

                # 7. 點擊 Search (驗證) (利用您提供的 data-testid)
                search_btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='jql-editor-search']")
                search_btn.click()
                time.sleep(2) # 等待搜尋結果

                # 8. 點擊 Insert Results (儲存)
                # 這裡需要抓取 Insert 按鈕，通常在右下角
                insert_btn = driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save')]")
                insert_btn.click()
                
                # 等待 Modal 消失
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
                time.sleep(2) # 等待頁面更新
                
            else:
                print("未在 JQL 中發現足夠的日期格式，跳過此區塊。")
                # 按取消或 ESC 關閉視窗
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()

        except Exception as e:
            print(f"處理第 {i+1} 個表格時發生錯誤: {str(e)}")
            # 嘗試按 ESC 離開可能的 Modal，以免卡住下一個
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            continue

def main():
    dates = get_target_dates()
    print(f"計算目標日期: 本週一 {dates['monday_str']}, 本週日 {dates['sunday_str']}")
    
    driver = init_driver()
    try:
        login(driver)
        
        # --- 自動導航邏輯 ---
        # 1. 找到左側最新的 Report (假設已經在該 Space 下)
        # 這部分需要根據實際 URL 調整，若 URL 固定則直接 driver.get(TARGET_PAGE)
        
        # 模擬複製頁面邏輯 (需根據實際按鈕 ID 撰寫，以下為示意)
        # driver.find_element(By.ID, "more-actions-menu").click()
        # driver.find_element(By.ID, "copy-page").click()
        
        # 2. 進入編輯模式後...
        # 假設現在已經在新頁面的編輯模式
        
        update_jira_macros(driver, dates)
        
        # 3. 發布頁面
        # publish_btn = driver.find_element(By.ID, "publish-button")
        # publish_btn.click()
        
        print("自動化流程結束。")
        
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
