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
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

def inject_cookies(driver):
    """注入 Cookies (修復版：移除網域限制與欄位轉換)"""
    print("正在處理 Cookies...")
    if not COOKIES_JSON:
        raise Exception("錯誤：找不到 CONF_COOKIES Secret！")
        
    try:
        cookies = json.loads(COOKIES_JSON)
    except json.JSONDecodeError:
        raise Exception("錯誤：CONF_COOKIES JSON 格式錯誤。")

    # 1. 必須先前往目標網域，Selenium 才能注入 Cookies
    print(f"前往目標網域以準備注入: {URL}")
    driver.get(URL)
    
    added_count = 0
    error_count = 0
    
    for cookie in cookies:
        new_cookie = {}
        
        # 2. 複製必要欄位
        if 'name' not in cookie or 'value' not in cookie:
            continue
            
        new_cookie['name'] = cookie['name']
        new_cookie['value'] = cookie['value']
        
        # 3. 【關鍵修復】轉換欄位名稱
        # EditThisCookie 輸出的是 'expirationDate' (float)，但 Selenium 需要 'expiry' (int)
        if 'expirationDate' in cookie:
            new_cookie['expiry'] = int(cookie['expirationDate'])
        elif 'expiry' in cookie:
            new_cookie['expiry'] = int(cookie['expiry'])
            
        if 'path' in cookie:
            new_cookie['path'] = cookie['path']
        
        if 'secure' in cookie:
            new_cookie['secure'] = cookie['secure']
            
        if 'httpOnly' in cookie:
            new_cookie['httpOnly'] = cookie['httpOnly']

        # 4. 【關鍵修復】不要設定 'domain'
        # 強制讓瀏覽器將 Cookie 視為當前網址的 Host-only cookie。
        # 這能解決 ".atlassian.net" 與 "qsiaiot.atlassian.net" 之間的網域衝突。
        
        # 5. 移除 SameSite (Selenium 不支援設定此屬性)
        # (已透過建立新 dict 達成，因為我們只複製了需要的欄位)

        try:
            driver.add_cookie(new_cookie)
            added_count += 1
        except Exception as e:
            error_count += 1
            # 只在除錯時印出，避免 Log 太多
            # print(f"注入失敗 ({cookie['name']}): {str(e)}")
            pass
            
    print(f"注入結果: 成功 {added_count} 個 / 失敗 {error_count} 個 (總數 {len(cookies)})")
    
    if added_count == 0:
        raise Exception("沒有任何 Cookie 注入成功，請確認 CONF_URL 與 Cookies 的來源網域是否匹配。")

    # 6. 重新整理以套用 Cookies
    print("重新整理頁面以套用登入狀態...")
    driver.refresh()
    time.sleep(5)

def update_jira_macros(driver, date_info):
    """更新 Jira 表格邏輯 (暫位符)"""
    pass

def main():
    dates = get_target_dates()
    print(f"=== 自動化任務開始 (Cookie 修復版) ===")
    print(f"目標日期設定: {dates['monday_str']} ~ {dates['sunday_str']}")
    
    driver = init_driver()
    try:
        inject_cookies(driver)
        
        # 驗證
        print(f"當前頁面標題: {driver.title}")
        
        if "Log in" in driver.title or "Atlassian account" in driver.title:
            print("❌ 失敗：注入後仍在登入畫面。")
            # 印出部分 Cookie 資訊幫助除錯
            print("瀏覽器內目前的 Cookies:")
            print(driver.get_cookies())
            driver.save_screenshot("cookie_failed.png")
        else:
            print("✅ 成功：已進入系統！(標題不包含 Log in)")
            # 成功後的後續動作...
        
    except Exception as e:
        print(f"執行失敗: {str(e)}")
    finally:
        print("關閉瀏覽器...")
        driver.quit()

if __name__ == "__main__":
    main()
