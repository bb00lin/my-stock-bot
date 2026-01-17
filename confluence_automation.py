import os
import time
import json
import re
from datetime import date, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException

# --- 設定區 ---
# 注意：這次我們主要依賴 COOKIES，USER/PASS 僅作為備用或參考
URL = os.environ.get("CONF_URL")
COOKIES_JSON = os.environ.get("CONF_COOKIES")

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
    # 偽裝成一般瀏覽器
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def inject_cookies(driver):
    """注入 Cookies 以略過登入畫面"""
    print("正在處理 Cookies...")
    if not COOKIES_JSON:
        raise Exception("錯誤：找不到 CONF_COOKIES Secret，無法略過登入驗證！")
        
    try:
        cookies = json.loads(COOKIES_JSON)
    except json.JSONDecodeError:
        raise Exception("錯誤：CONF_COOKIES 格式不正確，請確認是從 EditThisCookie 匯出的 JSON 陣列。")

    # 必須先訪問目標網域，才能設定該網域的 Cookies
    # 我們先前往登入頁面，讓瀏覽器認得這個網域
    driver.get(URL)
    
    added_count = 0
    for cookie in cookies:
        # Selenium 對 Cookie 欄位很嚴格，需要過濾掉不支援的欄位
        new_cookie = {}
        
        # 必要的欄位
        if 'name' not in cookie or 'value' not in cookie:
            continue
            
        new_cookie['name'] = cookie['name']
        new_cookie['value'] = cookie['value']
        
        # 選擇性欄位 (如果有才加)
        if 'domain' in cookie:
            new_cookie['domain'] = cookie['domain']
        if 'path' in cookie:
            new_cookie['path'] = cookie['path']
        if 'secure' in cookie:
            new_cookie['secure'] = cookie['secure']
        if 'expiry' in cookie:
            new_cookie['expiry'] = cookie['expiry']
            
        # 【重要】Selenium 不支援 sameSite 屬性設定，必須移除，否則會報錯
        # 且必須確保 domain 正確
        try:
            driver.add_cookie(new_cookie)
            added_count += 1
        except Exception as e:
            # 忽略個別 Cookie 的錯誤 (有些跨網域的 cookie 會失敗是正常的)
            pass
            
    print(f"成功注入 {added_count} 個 Cookies。")
    
    # 注入完成後，重新整理頁面，這時候應該就會變成「已登入」狀態
    driver.refresh()
    time.sleep(5) # 等待重新整理後的載入

def update_jira_macros(driver, date_info):
    """更新 Jira 表格邏輯 (暫時保留空函式，待登入成功後啟用)"""
    pass

def main():
    dates = get_target_dates()
    print(f"=== 自動化任務開始 (Cookie 注入模式) ===")
    print(f"目標日期設定: {dates['monday_str']} ~ {dates['sunday_str']}")
    
    driver = init_driver()
    try:
        # 1. 執行 Cookie 注入 (取代原本的 login)
        inject_cookies(driver)
        
        # 2. 驗證是否成功進入系統 (檢查標題)
        print(f"當前頁面標題: {driver.title}")
        
        if "Log in" in driver.title or "Atlassian account" in driver.title:
            print("❌ 失敗：注入 Cookies 後仍然在登入畫面。")
            print("可能原因：")
            print("1. Cookies 已過期 (請重新匯出)")
            print("2. 匯出的 Cookies 不完整 (請確認是在 Atlassian 網域下匯出)")
            driver.save_screenshot("cookie_login_failed.png")
        else:
            print("✅ 成功：已繞過登入畫面，進入系統！")
            
            # ----------------------------------------------------
            # 測試成功後，我們可以在這裡解鎖下一步
            # ----------------------------------------------------
            # driver.get("您的目標頁面編輯網址")
            # update_jira_macros(driver, dates)
        
    except Exception as e:
        print(f"執行失敗: {str(e)}")
        driver.save_screenshot("fatal_error.png")
    finally:
        print("關閉瀏覽器...")
        driver.quit()

if __name__ == "__main__":
    main()
