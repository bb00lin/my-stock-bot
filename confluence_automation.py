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
    計算本週的關鍵日期
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
        "friday_filename": friday.strftime("%Y%m%d")
    }

def init_driver():
    """初始化 Chrome Driver"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new") # 新版無頭模式
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled") # 移除自動化標記
    
    # 偽裝 User-Agent
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def login(driver):
    """執行 Atlassian 登入流程 (增強版)"""
    print(f"正在前往: {URL}")
    driver.get(URL)
    wait = WebDriverWait(driver, 30)
    
    try:
        print("等待輸入帳號...")
        # 【修正點】使用更寬鬆的選擇器，不只依賴 id="username"
        # 尋找任何 id 是 username, name 是 username 或 type 是 email 的欄位
        email_selector = (By.XPATH, "//input[@id='username' or @name='username' or @type='email']")
        
        email_field = wait.until(EC.element_to_be_clickable(email_selector))
        email_field.clear()
        email_field.send_keys(USERNAME)
        
        # 點擊「繼續」
        continue_btn = driver.find_element(By.ID, "login-submit")
        continue_btn.click()
        
        print("等待輸入密碼...")
        # 等待密碼欄位
        password_field = wait.until(EC.visibility_of_element_located((By.ID, "password")))
        password_field.clear()
        password_field.send_keys(PASSWORD)
        
        # 點擊登入
        login_btn = driver.find_element(By.ID, "login-submit")
        login_btn.click()
        
        # 驗證登入是否成功 (等待 URL 變化或標題變化)
        print("登入資訊已送出，等待頁面跳轉...")
        time.sleep(15)
        
        # 簡單檢查：如果標題還停留在 Login，可能失敗了
        if "Log in" in driver.title:
            print(f"警告：瀏覽器標題仍為 '{driver.title}'，可能卡在兩步驟驗證或載入過慢。")
        else:
            print(f"登入似乎成功，當前標題: {driver.title}")

    except TimeoutException:
        print("\n!!! 嚴重錯誤：登入逾時 !!!")
        print(f"當前瀏覽器標題: {driver.title}")
        # 嘗試印出頁面原始碼的一小部分，幫助除錯
        try:
            print("頁面原始碼片段 (前500字):")
            print(driver.page_source[:500])
        except:
            pass
        driver.save_screenshot("login_error_debug.png")
        raise

def update_jira_macros(driver, date_info):
    """
    更新 Jira 表格的核心邏輯
    注意：此函式在本次測試中不會被呼叫，以確保安全。
    """
    wait = WebDriverWait(driver, 20)
    macro_locator = (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    
    try:
        wait.until(EC.presence_of_element_located(macro_locator))
    except TimeoutException:
        print("找不到 Jira 表格，跳過。")
        return

    macros = driver.find_elements(*macro_locator)
    print(f"共發現 {len(macros)} 個 Jira 表格...")

    # (此處省略具體編輯代碼，因為目前不執行)
    pass

def main():
    dates = get_target_dates()
    print(f"=== 自動化任務開始 (安全模式: 僅測試登入) ===")
    print(f"目標日期設定: 本週一 {dates['monday_str']}, 本週日 {dates['sunday_str']}")
    
    driver = init_driver()
    try:
        # 1. 執行登入
        login(driver)
        
        # ----------------------------------------------------
        # 安全鎖：以下代碼已被註解，確保不會修改您的 Confluence 內容
        # ----------------------------------------------------
        
        # print("準備進入編輯模式...")
        # driver.get("您的目標頁面編輯網址")
        # update_jira_macros(driver, dates)
        
        print(">>> 測試結束：登入流程已執行，未進行任何編輯操作。 <<<")
        
    except Exception as e:
        print(f"執行過程中發生未預期的錯誤: {str(e)}")
        driver.save_screenshot("fatal_error.png")
    finally:
        print("關閉瀏覽器...")
        driver.quit()

if __name__ == "__main__":
    main()
