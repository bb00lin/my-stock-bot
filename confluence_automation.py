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
PASSWORD = os.environ.get("CONF_PASS") # 您的 API Token

# --- 日期計算邏輯 ---
def get_target_dates():
    """
    計算本週的關鍵日期：
    1. 本週一 (用於 JQL Start Date)
    2. 本週日 (用於 JQL End Date)
    3. 本週五 (用於檔案命名)
    """
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
    """初始化 Chrome Driver，包含反偵測設定"""
    chrome_options = Options()
    # 使用新版無頭模式，比舊版更難被偵測
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # 【關鍵修復】加入 User-Agent 偽裝成真實的電腦瀏覽器，解決 Timeout 問題
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def login(driver):
    """執行 Atlassian 登入流程"""
    print(f"正在前往: {URL}")
    driver.get(URL)
    wait = WebDriverWait(driver, 30) # 延長等待時間至 30 秒
    
    try:
        print("等待輸入帳號...")
        # 1. 輸入 Email (改用 visibility_of_element_located 確保元素可見)
        email_field = wait.until(EC.visibility_of_element_located((By.ID, "username")))
        email_field.clear()
        email_field.send_keys(USERNAME)
        
        # 顯式點擊「繼續」按鈕，比按 Enter 更穩定
        continue_btn = driver.find_element(By.ID, "login-submit")
        continue_btn.click()
        
        print("等待輸入密碼...")
        # 2. 等待密碼欄位出現
        password_field = wait.until(EC.visibility_of_element_located((By.ID, "password")))
        password_field.clear()
        password_field.send_keys(PASSWORD)
        
        # 點擊登入按鈕
        login_btn = driver.find_element(By.ID, "login-submit")
        login_btn.click()
        
        # 3. 確保登入完成
        print("登入資訊已送出，正在等待頁面跳轉...")
        time.sleep(15) # 給予充裕的時間讓 Confluence 載入複雜的首頁

    except TimeoutException:
        print("\n!!! 嚴重錯誤：登入逾時 !!!")
        print(f"當前瀏覽器標題: {driver.title}")
        print("這通常代表 Atlassian 阻擋了自動化登入，或者頁面還在轉圈圈。")
        # 儲存截圖以便除錯
        driver.save_screenshot("login_error_debug.png")
        raise

def update_jira_macros(driver, date_info):
    """搜尋並更新頁面上的 Jira 表格日期"""
    wait = WebDriverWait(driver, 20)
    
    # 根據提供的 HTML 結構，定位 Jira Macro 區塊
    macro_locator = (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    
    # 先等待頁面元素載入
    try:
        wait.until(EC.presence_of_element_located(macro_locator))
    except TimeoutException:
        print("頁面上找不到 Jira 表格 (blockCard)，請確認是否已進入編輯模式或頁面是否正確。")
        return

    macros = driver.find_elements(*macro_locator)
    print(f"共發現 {len(macros)} 個 Jira 表格，準備開始更新...")

    for i in range(len(macros)):
        try:
            # 重新抓取元素列表，避免 StaleElementReferenceException (DOM 變更後舊元素失效)
            current_macros = driver.find_elements(*macro_locator)
            if i >= len(current_macros):
                break
            
            target_macro = current_macros[i]
            
            # 1. 捲動到目標並點擊選取
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_macro)
            time.sleep(1)
            target_macro.click()
            
            # 2. 點擊下方的「編輯」按鈕 (鉛筆圖示)
            # 嘗試使用通用的 Edit 按鈕定位
            try:
                edit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@aria-label='Edit'] | //span[text()='Edit'] | //button[contains(., 'Edit')]")))
                edit_btn.click()
            except TimeoutException:
                print(f"第 {i+1} 個表格無法點擊編輯按鈕，跳過。")
                continue

            print(f"正在編輯第 {i+1} 個表格...")

            # 3. 等待 JQL 編輯器輸入框出現 (使用 data-testid)
            jql_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            
            # 4. 取得目前 JQL 文字並計算新日期
            old_jql = jql_input.text
            new_jql = old_jql
            
            # Regex 尋找: 4碼年-1或2碼月-1或2碼日 (例如 2026-1-12)
            date_pattern = r"\d{4}-\d{1,2}-\d{1,2}"
            dates_found = re.findall(date_pattern, old_jql)
            
            if len(dates_found) >= 2:
                # 替換第一個日期為本週一
                new_jql = re.sub(dates_found[0], date_info['monday_str'], new_jql, count=1)
                # 替換第二個日期為本週日
                new_jql = re.sub(dates_found[1], date_info['sunday_str'], new_jql, count=1)
                
                print(f"更新日期區間: {dates_found[0]}~{dates_found[1]} -> {date_info['monday_str']}~{date_info['sunday_str']}")
                
                # 6. 輸入新的 JQL
                jql_input.click()
                # 全選並刪除 (Linux 環境用 Control+a)
                jql_input.send_keys(Keys.CONTROL + "a")
                jql_input.send_keys(Keys.BACK_SPACE)
                time.sleep(0.5)
                
                # 輸入新文字並按 Enter
                jql_input.send_keys(new_jql)
                jql_input.send_keys(Keys.ENTER)
                time.sleep(1)

                # 7. 點擊 Search 按鈕驗證 (使用 data-testid)
                search_btn = driver.find_element(By.CSS_SELECTOR, "[data-testid='jql-editor-search']")
                search_btn.click()
                time.sleep(2) # 等待搜尋結果刷新

                # 8. 點擊 Insert/Save 按鈕
                # 尋找含有 Insert 或 Save 文字的按鈕
                insert_btn = driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]")
                insert_btn.click()
                
                # 等待編輯框消失，代表儲存成功
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
                time.sleep(2) 
                
            else:
                print("JQL 中未發現足夠的日期格式，跳過此區塊。")
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()

        except Exception as e:
            print(f"處理第 {i+1} 個表格時發生錯誤: {str(e)}")
            # 嘗試按 ESC 離開可能的 Modal，以免卡住下一個
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            continue

def main():
    dates = get_target_dates()
    print(f"=== 自動化任務開始 ===")
    print(f"目標日期設定: 本週一 {dates['monday_str']}, 本週日 {dates['sunday_str']}, 檔名日期 {dates['friday_filename']}")
    
    driver = init_driver()
    try:
        login(driver)
        
        # --- 自動化流程佔位符 ---
        # 目前代碼會登入並進入首頁。
        # 因為「複製頁面」與「重新命名」需要複雜的導航邏輯，
        # 我們先測試能否成功登入。
        
        # 如果您已經有目標頁面的編輯網址，可以解除下方註解並填入：
        # TARGET_EDIT_URL = "https://qsiaiot.atlassian.net/wiki/spaces/YourSpace/pages/edit/YourPageID"
        # driver.get(TARGET_EDIT_URL)
        # time.sleep(5)
        # update_jira_macros(driver, dates)
        
        print("登入測試完成。若要執行編輯，請設定目標網址。")
        
    except Exception as e:
        print(f"執行過程中發生未預期的錯誤: {str(e)}")
        # 截圖以供檢查
        driver.save_screenshot("fatal_error.png")
    finally:
        print("關閉瀏覽器...")
        driver.quit()

if __name__ == "__main__":
    main()
