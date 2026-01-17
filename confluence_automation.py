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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# --- 設定區 (從環境變數讀取，確保安全) ---
URL = os.environ.get("CONF_URL")  # Confluence 網址
USERNAME = os.environ.get("CONF_USER") # 您的 Email
PASSWORD = os.environ.get("CONF_PASS") # 【注意】這裡必須是「真正的登入密碼」，不能是 API Token

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
    """執行 Atlassian 登入流程 (含錯誤偵測)"""
    print(f"正在前往: {URL}")
    driver.get(URL)
    wait = WebDriverWait(driver, 30)
    
    try:
        print("等待輸入帳號...")
        # 寬鬆選擇器：尋找 id="username", name="username" 或 type="email"
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
        
        print("登入資訊已送出，等待頁面跳轉 (最多等待 20 秒)...")
        time.sleep(5) # 先等一下讓後端處理
        
        # 檢查是否成功跳轉 (標題不再包含 "Log in")
        # 這裡用一個小迴圈每秒檢查一次
        for i in range(15):
            if "Log in" not in driver.title and "Atlassian account" not in driver.title:
                print(f"登入成功！當前標題: {driver.title}")
                return
            
            # 檢查是否有錯誤訊息出現
            try:
                # 嘗試抓取常見的錯誤提示元素 (Atlassian 通常用 form-error 或 error-summary)
                error_elements = driver.find_elements(By.XPATH, "//*[contains(@id, 'error') or contains(@class, 'error')]")
                for err in error_elements:
                    if err.is_displayed() and len(err.text) > 5:
                        print(f"!!! 偵測到登入錯誤提示: {err.text} !!!")
                        print("如果是 'Incorrect email or password'，請確認您的 CONF_PASS 是否為真正的密碼 (非 API Token)。")
                        raise Exception("Login Failed with Error Message")
            except NoSuchElementException:
                pass
                
            time.sleep(1)
            
        # 如果跑完迴圈還沒跳轉
        print(f"警告：等待逾時，瀏覽器標題仍為 '{driver.title}'")
        print("可能原因：1. 密碼錯誤  2. 需要兩步驟驗證 (2FA)  3. 網頁載入極慢")
        
    except TimeoutException:
        print("\n!!! 嚴重錯誤：找不到登入欄位或操作逾時 !!!")
        print(f"當前瀏覽器標題: {driver.title}")
        driver.save_screenshot("login_timeout.png")
        raise

def update_jira_macros(driver, date_info):
    """
    更新 Jira 表格的核心邏輯 (目前不執行)
    """
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
        # 安全鎖：編輯功能暫時關閉
        # ----------------------------------------------------
        # driver.get("您的目標頁面編輯網址")
        # update_jira_macros(driver, dates)
        
        print(">>> 測試結束 <<<")
        
    except Exception as e:
        print(f"執行失敗: {str(e)}")
        # 截圖以供檢查
        driver.save_screenshot("fatal_error.png")
    finally:
        print("關閉瀏覽器...")
        driver.quit()

if __name__ == "__main__":
    main()
