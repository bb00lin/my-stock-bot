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

# --- 設定區 ---
URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
PASSWORD = os.environ.get("CONF_PASS")

# --- 日期計算邏輯 ---
def get_target_dates():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    friday = monday + timedelta(days=4)
    return {
        "monday_str": monday.strftime("%Y-%m-%d"),
        "sunday_str": sunday.strftime("%Y-%m-%d"),
        "friday_filename": friday.strftime("%Y%m%d")
    }

def init_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def login(driver):
    print(f"正在前往: {URL}")
    driver.get(URL)
    wait = WebDriverWait(driver, 30)
    
    try:
        print("步驟 1/3: 輸入帳號...")
        email_selector = (By.XPATH, "//input[@id='username' or @name='username' or @type='email']")
        email_field = wait.until(EC.element_to_be_clickable(email_selector))
        email_field.clear()
        email_field.send_keys(USERNAME)
        
        continue_btn = driver.find_element(By.ID, "login-submit")
        continue_btn.click()
        
        print("步驟 2/3: 輸入密碼...")
        password_field = wait.until(EC.visibility_of_element_located((By.ID, "password")))
        password_field.clear()
        password_field.send_keys(PASSWORD)
        
        login_btn = driver.find_element(By.ID, "login-submit")
        login_btn.click()
        
        print("步驟 3/3: 等待跳轉 (檢查是否卡在 2FA)...")
        time.sleep(8) # 等待頁面反應
        
        # --- 診斷關鍵點 ---
        # 檢查是否進入首頁
        for i in range(10):
            current_title = driver.title
            if "Log in" not in current_title and "Atlassian account" not in current_title:
                print(f"✅ 登入成功！標題: {current_title}")
                return
            
            # 檢查常見的阻擋關鍵字
            page_text = driver.find_element(By.TAG_NAME, "body").text
            
            if "verify your identity" in page_text or "verification code" in page_text or "authenticator app" in page_text:
                print("\n🔴 偵測到【兩步驟驗證 (2FA)】阻擋！")
                print("系統正在要求輸入手機驗證碼，這導致自動化失敗。")
                print("解決方案：您需要設定 TOTP Secret (請將 Log 截圖給 AI 尋求協助)。")
                raise Exception("2FA_BLOCK")
            
            if "CAPTCHA" in page_text or "robot" in page_text:
                print("\n🔴 偵測到【機器人驗證 (CAPTCHA)】阻擋！")
                raise Exception("CAPTCHA_BLOCK")
                
            if "Incorrect email or password" in page_text:
                print("\n🔴 偵測到【密碼錯誤】！請檢查 GitHub Secrets。")
                raise Exception("WRONG_PASSWORD")

            time.sleep(1)
            
        print(f"⚠️ 警告：頁面標題仍為 '{driver.title}'，未偵測到明確錯誤，但無法進入首頁。")
        # 印出頁面上的文字幫助除錯
        print("--- 頁面可見文字快照 (前 300 字) ---")
        print(driver.find_element(By.TAG_NAME, "body").text[:300])
        print("--------------------------------")
        
    except TimeoutException:
        print("\n!!! 網頁載入逾時 !!!")
        raise

def update_jira_macros(driver, date_info):
    pass

def main():
    dates = get_target_dates()
    print(f"=== 自動化任務開始 (診斷模式) ===")
    
    driver = init_driver()
    try:
        login(driver)
        print(">>> 登入測試通過 <<<")
        
    except Exception as e:
        print(f"執行失敗: {str(e)}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
