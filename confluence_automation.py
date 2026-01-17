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
from urllib.parse import urlparse

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
    """注入 Cookies (Robots.txt 定錨版)"""
    print("正在處理 Cookies...")
    if not COOKIES_JSON:
        raise Exception("錯誤：找不到 CONF_COOKIES Secret！")
        
    try:
        cookies = json.loads(COOKIES_JSON)
    except json.JSONDecodeError:
        raise Exception("錯誤：CONF_COOKIES JSON 格式錯誤。")

    # 【關鍵修改】
    # 解析 URL 取得 Base URL (例如 https://qsiaiot.atlassian.net)
    parsed_url = urlparse(URL)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    # 技巧：前往 robots.txt，這是一個公開檔案，不會觸發 SSO 轉址
    # 這樣可以確保瀏覽器停在正確的網域 (qsiaiot.atlassian.net) 等待注入
    robots_url = f"{base_url}/robots.txt"
    print(f"前往定錨頁面以鎖定網域: {robots_url}")
    driver.get(robots_url)
    
    # 簡單等待一下確保頁面載入
    time.sleep(2)
    
    added_count = 0
    error_count = 0
    
    print("開始注入 Cookies...")
    for cookie in cookies:
        new_cookie = {}
        
        # 複製必要欄位
        if 'name' not in cookie or 'value' not in cookie:
            continue
            
        new_cookie['name'] = cookie['name']
        new_cookie['value'] = cookie['value']
        
        # 轉換過期時間
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

        # 【策略】
        # 因為我們現在就在正確的網域 (robots.txt)，所以可以直接移除 domain 屬性，
        # 讓它自動變成當前網域 (Host-only) 的 Cookie。
        # 這樣可以避免 .atlassian.net vs qsiaiot.atlassian.net 的衝突。
        
        try:
            driver.add_cookie(new_cookie)
            added_count += 1
        except Exception as e:
            error_count += 1
            pass
            
    print(f"注入結果: 成功 {added_count} 個 / 失敗 {error_count} 個")
    
    if added_count == 0:
        raise Exception("Cookies 注入失敗，沒有任何 Cookie 被瀏覽器接受。")

    # 注入完成後，前往真正的目標頁面
    print(f"Cookies 注入完成，前往目標頁面: {URL}")
    driver.get(URL)
    time.sleep(5)

def update_jira_macros(driver, date_info):
    """更新 Jira 表格邏輯 (暫位符)"""
    pass

def main():
    dates = get_target_dates()
    print(f"=== 自動化任務開始 (Robots.txt 定錨模式) ===")
    print(f"目標日期設定: {dates['monday_str']} ~ {dates['sunday_str']}")
    
    driver = init_driver()
    try:
        inject_cookies(driver)
        
        # 驗證
        print(f"當前頁面標題: {driver.title}")
        
        # 檢查是否還在登入頁面
        if "Log in" in driver.title or "Atlassian account" in driver.title:
            print("❌ 失敗：依然在登入畫面。")
            print("可能原因：Cookies 因 IP 變動被 Atlassian 強制失效 (Session Invalidation)。")
            driver.save_screenshot("cookie_final_fail.png")
        else:
            print("✅ 成功：已進入系統！(標題不包含 Log in)")
            # 成功後的後續動作...
            # driver.get(TARGET_EDIT_URL)
            # update_jira_macros(driver, dates)
        
    except Exception as e:
        print(f"執行失敗: {str(e)}")
        driver.save_screenshot("fatal_error.png")
    finally:
        print("關閉瀏覽器...")
        driver.quit()

if __name__ == "__main__":
    main()
