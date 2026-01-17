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
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException, ElementClickInterceptedException
from webdriver_manager.chrome import ChromeDriverManager

# --- 設定區 ---
URL = os.environ.get("CONF_URL")
COOKIES_JSON = os.environ.get("CONF_COOKIES")

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
    print("正在注入 Cookies...")
    if not COOKIES_JSON: raise Exception("錯誤：找不到 CONF_COOKIES Secret")
    
    cookies = json.loads(COOKIES_JSON)
    parsed_url = urlparse(URL)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    driver.get(f"{base_url}/robots.txt")
    time.sleep(1)
    
    added = 0
    for cookie in cookies:
        if 'name' not in cookie or 'value' not in cookie: continue
        new_cookie = {
            'name': cookie['name'], 'value': cookie['value'],
            'path': cookie.get('path', '/'), 'secure': cookie.get('secure', True)
        }
        if 'expirationDate' in cookie: new_cookie['expiry'] = int(cookie['expirationDate'])
        elif 'expiry' in cookie: new_cookie['expiry'] = int(cookie['expiry'])
        try: driver.add_cookie(new_cookie); added += 1
        except: pass
            
    print(f"成功注入 {added} 個 Cookies，前往目標頁面...")
    driver.get(URL)
    time.sleep(8)

def find_element_omnibus(driver, wait, description, locators):
    """廣域搜尋元素的通用函式"""
    print(f"正在搜尋: {description}...")
    for method, query in locators:
        try:
            element = wait.until(EC.presence_of_element_located((method, query)))
            # 嘗試捲動到元素確保可見
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            return element
        except: continue
    raise Exception(f"找不到元素: {description}")

def navigate_and_copy_latest(driver):
    print(f"正在搜尋側邊欄 (當前: {driver.title})...")
    wait = WebDriverWait(driver, 30)
    
    try:
        xpath_query = "//a[contains(., 'WeeklyReport_20')]"
        links = wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_query)))
        valid_links = []
        for l in links:
            try:
                if "Copy" not in l.text and "副本" not in l.text: valid_links.append(l)
            except: continue
        
        if not valid_links: raise Exception("找不到週報連結")
        valid_links.sort(key=lambda x: x.text)
        latest_link = valid_links[-1]
        print(f"最新週報: {latest_link.text}，進入中...")
        
        driver.execute_script("arguments[0].click();", latest_link)
        time.sleep(5)
        wait.until(EC.url_contains("pages"))
        
        match = re.search(r"/pages/(\d+)", driver.current_url)
        if not match: raise Exception("無法解析 Page ID")
        
        copy_url = f"{driver.current_url.split('/pages/')[0]}/pages/create?copyPageId={match.group(1)}"
        print(f"跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        
        # 等待編輯器載入 (檢查標題框)
        wait_long = WebDriverWait(driver, 60)
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform() # 關閉彈窗
        
        find_element_omnibus(driver, wait_long, "標題輸入框", [
            (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']"),
            (By.ID, "content-title"),
            (By.CSS_SELECTOR, "textarea[placeholder='Page title']"),
            (By.CSS_SELECTOR, "textarea[placeholder='頁面標題']")
        ])
        print("編輯器就緒！")
        
    except TimeoutException:
        print("導航逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    print(f"重命名為: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    
    title_area = find_element_omnibus(driver, wait, "標題輸入框", [
        (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']"),
        (By.ID, "content-title")
    ])
    
    try: title_area.click()
    except: driver.execute_script("arguments[0].click();", title_area)
    
    title_area.send_keys(Keys.CONTROL + "a")
    title_area.send_keys(Keys.BACK_SPACE)
    time.sleep(0.5)
    title_area.send_keys(f"WeeklyReport_{new_filename}")
    time.sleep(2) # 確保標題變更已生效

def scroll_to_wake_content(driver):
    """【關鍵新增】上下捲動頁面以觸發懶加載內容"""
    print("正在捲動頁面以載入內容 (Wake up lazy loading)...")
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight / 2);")
    time.sleep(1)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(2)

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 30)
    
    # 先執行捲動喚醒
    scroll_to_wake_content(driver)
    
    print("開始搜尋 Jira 表格...")
    macro_locator = (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    
    try:
        # 等待至少一個表格出現
        wait.until(EC.presence_of_element_located(macro_locator))
    except TimeoutException:
        print("警告：逾時仍找不到 Jira 表格。")
        # 印出 body 結構做最後掙扎的除錯
        # print(driver.find_element(By.TAG_NAME, "body").get_attribute("innerHTML")[:1000])
        return

    macros_count = len(driver.find_elements(*macro_locator))
    print(f"共發現 {macros_count} 個表格。")

    for i in range(macros_count):
        try:
            macros = driver.find_elements(*macro_locator)
            if i >= len(macros): break
            target = macros[i]
            
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
            time.sleep(1)
            target.click()
            
            edit_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[@aria-label='編輯'] | //button[@aria-label='Edit'] | //span[text()='編輯'] | //span[text()='Edit']")
            ))
            edit_btn.click()
            
            jql_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            old_jql = jql_input.text
            
            date_pattern = r"\d{4}-\d{1,2}-\d{1,2}"
            dates_found = re.findall(date_pattern, old_jql)
            
            if len(dates_found) >= 2:
                new_jql = re.sub(dates_found[0], date_info['monday_str'], old_jql, count=1)
                new_jql = re.sub(dates_found[1], date_info['sunday_str'], new_jql, count=1)
                
                print(f"[{i+1}/{macros_count}] 更新: {dates_found[0]}~{dates_found[1]} -> {date_info['monday_str']}~{date_info['sunday_str']}")
                
                jql_input.click()
                jql_input.send_keys(Keys.CONTROL + "a")
                jql_input.send_keys(Keys.BACK_SPACE)
                time.sleep(0.1)
                jql_input.send_keys(new_jql)
                jql_input.send_keys(Keys.ENTER)
                time.sleep(2)
                
                insert_btn = driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]")
                insert_btn.click()
                
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
                time.sleep(2)
            else:
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                
        except Exception as e:
            print(f"[{i+1}] 錯誤: {str(e)}")
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            continue

def publish_page(driver):
    print("準備發布頁面...")
    wait = WebDriverWait(driver, 15)
    
    # 定義發布按鈕的多種可能 (包含中文、英文、ID)
    publish_locators = [
        (By.CSS_SELECTOR, "[data-testid='publish-button']"),
        (By.ID, "publish-button"),
        (By.XPATH, "//button[contains(., '發布')]"),
        (By.XPATH, "//button[contains(., 'Publish')]"),
        (By.CSS_SELECTOR, "button[aria-label='發布']"),
        (By.CSS_SELECTOR, "button[aria-label='Publish']")
    ]
    
    try:
        btn = find_element_omnibus(driver, wait, "發布按鈕", publish_locators)
        btn.click()
        time.sleep(10)
        print("頁面發布動作已執行！")
    except Exception as e:
        print(f"發布失敗: {str(e)}")
        driver.save_screenshot("publish_error.png")
        raise

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 (v8.0 捲動喚醒版) ===")
    print(f"日期設定: {dates['monday_str']} ~ {dates['sunday_str']} | 檔名: {dates['friday_filename']}")
    
    driver = init_driver()
    try:
        inject_cookies(driver)
        navigate_and_copy_latest(driver)
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
