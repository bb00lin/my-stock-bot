import os
import time
import json
import re
from datetime import date, timedelta
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager

# --- 設定區 ---
URL = os.environ.get("CONF_URL")
COOKIES_JSON = os.environ.get("CONF_COOKIES")

def get_target_dates():
    """計算本週一、本週日、本週五(檔名用)"""
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
    """使用 robots.txt 定錨策略注入 Cookies"""
    print("正在注入 Cookies...")
    if not COOKIES_JSON:
        raise Exception("錯誤：找不到 CONF_COOKIES Secret")
    
    cookies = json.loads(COOKIES_JSON)
    parsed_url = urlparse(URL)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    
    driver.get(f"{base_url}/robots.txt")
    time.sleep(1)
    
    added = 0
    for cookie in cookies:
        if 'name' not in cookie or 'value' not in cookie: continue
        new_cookie = {
            'name': cookie['name'],
            'value': cookie['value'],
            'path': cookie.get('path', '/'),
            'secure': cookie.get('secure', True)
        }
        if 'expirationDate' in cookie: new_cookie['expiry'] = int(cookie['expirationDate'])
        elif 'expiry' in cookie: new_cookie['expiry'] = int(cookie['expiry'])
        try:
            driver.add_cookie(new_cookie)
            added += 1
        except: pass
            
    print(f"成功注入 {added} 個 Cookies，前往目標頁面...")
    driver.get(URL)
    time.sleep(8)

def navigate_and_copy_latest(driver):
    """
    找到最新週報，解析 URL 取得 Page ID，然後直接跳轉到複製頁面。
    """
    print(f"正在搜尋側邊欄的最新週報 (當前頁面: {driver.title})...")
    wait = WebDriverWait(driver, 30)
    
    try:
        # 1. 尋找側邊欄連結
        xpath_query = "//a[contains(., 'WeeklyReport_20')]"
        links = wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_query)))
        
        valid_links = []
        for l in links:
            try:
                txt = l.text
                if "Copy" not in txt and "副本" not in txt and "Template" not in txt:
                    valid_links.append(l)
            except StaleElementReferenceException: continue
        
        if not valid_links:
            raise Exception("找不到任何符合 'WeeklyReport_20...' 格式的報告連結。")
            
        valid_links.sort(key=lambda x: x.text)
        latest_link = valid_links[-1]
        latest_name = latest_link.text
        print(f"找到最新週報: {latest_name}，正在進入...")
        
        # 點擊進入頁面
        driver.execute_script("arguments[0].click();", latest_link)
        
        # 等待 URL 更新，確保進入了該頁面
        print("等待頁面跳轉以獲取 Page ID...")
        time.sleep(5)
        wait.until(EC.url_contains("pages"))
        
        current_url = driver.current_url
        print(f"當前週報網址: {current_url}")
        
        # 2. 解析網址取得 Page ID
        # 典型網址: https://.../wiki/spaces/SPACE/pages/12345678/Title
        # Regex: 尋找 /pages/ 後面的數字
        match = re.search(r"/pages/(\d+)", current_url)
        if not match:
            raise Exception(f"無法從網址中解析出 Page ID: {current_url}")
            
        page_id = match.group(1)
        print(f"解析成功！Page ID: {page_id}")
        
        # 3. 建構「複製頁面」的網址
        # 格式通常為: .../wiki/spaces/SPACE/pages/copy/PAGE_ID
        # 我們利用 split 切割出前半段
        url_prefix = current_url.split('/pages/')[0]
        copy_url = f"{url_prefix}/pages/copy/{page_id}"
        
        print(f"直接跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        
        # 4. 等待編輯器載入
        print("等待編輯器載入 (尋找標題輸入框)...")
        # 只要能找到標題輸入框，就代表複製成功且編輯器就緒
        wait.until(EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")
        ))
        print("編輯器已就緒！")
        
    except TimeoutException:
        print("導航或載入逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    """修改頁面標題"""
    print(f"正在重命名為: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    
    title_area = driver.find_element(By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")
    title_area.click()
    # 清空標題
    title_area.send_keys(Keys.CONTROL + "a")
    title_area.send_keys(Keys.BACK_SPACE)
    time.sleep(0.5)
    # 輸入新標題
    title_area.send_keys(f"WeeklyReport_{new_filename}")
    time.sleep(1)

def update_jira_macros(driver, date_info):
    """迴圈更新所有 Jira 表格的日期"""
    wait = WebDriverWait(driver, 20)
    print("開始掃描並更新 Jira 表格...")
    
    macro_locator = (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    
    # 等待至少一個表格出現
    try:
        wait.until(EC.presence_of_element_located(macro_locator))
    except TimeoutException:
        print("警告：找不到 Jira 表格。")
        return

    macros_count = len(driver.find_elements(*macro_locator))
    print(f"共發現 {macros_count} 個表格。")

    for i in range(macros_count):
        try:
            # 重新抓取元素避免 Stale
            macros = driver.find_elements(*macro_locator)
            if i >= len(macros): break
            
            target = macros[i]
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
            time.sleep(1)
            target.click()
            
            # 尋找編輯按鈕
            edit_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[@aria-label='編輯'] | //button[@aria-label='Edit'] | //span[text()='編輯'] | //span[text()='Edit']")
            ))
            edit_btn.click()
            
            # 等待 JQL 輸入框
            jql_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            
            old_jql = jql_input.text
            date_pattern = r"\d{4}-\d{1,2}-\d{1,2}"
            dates_found = re.findall(date_pattern, old_jql)
            
            if len(dates_found) >= 2:
                new_jql = old_jql
                new_jql = re.sub(dates_found[0], date_info['monday_str'], new_jql, count=1)
                new_jql = re.sub(dates_found[1], date_info['sunday_str'], new_jql, count=1)
                
                print(f"[{i+1}/{macros_count}] 更新日期: {dates_found[0]}~{dates_found[1]} -> {date_info['monday_str']}~{date_info['sunday_str']}")
                
                jql_input.click()
                jql_input.send_keys(Keys.CONTROL + "a")
                jql_input.send_keys(Keys.BACK_SPACE)
                time.sleep(0.1)
                jql_input.send_keys(new_jql)
                jql_input.send_keys(Keys.ENTER)
                time.sleep(2) # 等待驗證
                
                # 點擊插入/儲存
                insert_btn = driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]")
                insert_btn.click()
                
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
                time.sleep(2)
            else:
                print(f"[{i+1}/{macros_count}] 跳過：無日期格式。")
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                
        except Exception as e:
            print(f"[{i+1}/{macros_count}] 失敗: {str(e)}")
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            continue

def publish_page(driver):
    """發布頁面"""
    print("準備發布頁面...")
    wait = WebDriverWait(driver, 10)
    try:
        publish_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "[data-testid='publish-button']")
        ))
        publish_btn.click()
        # 等待發布完成的跳轉
        time.sleep(10)
        print("頁面發布成功！")
    except Exception as e:
        print(f"發布失敗: {str(e)}")
        driver.save_screenshot("publish_error.png")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 (v5.0 直接網址版) ===")
    print(f"本週區間: {dates['monday_str']} ~ {dates['sunday_str']} | 檔名: {dates['friday_filename']}")
    
    driver = init_driver()
    try:
        inject_cookies(driver)
        navigate_and_copy_latest(driver) # 這裡改用了直接跳轉法
        rename_page(driver, dates['friday_filename'])
        update_jira_macros(driver, dates)
        publish_page(driver)
        print("=== 任務成功完成 ===")
        
    except Exception as e:
        print(f"\n[CRITICAL ERROR] 執行中斷: {str(e)}")
        driver.save_screenshot("workflow_error.png")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
