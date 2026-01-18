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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException, StaleElementReferenceException
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

def ensure_editor_active(driver):
    try:
        driver.find_element(By.TAG_NAME, "body").click()
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    except: pass

def navigate_and_copy_latest(driver):
    print(f"正在搜尋側邊欄 (當前: {driver.title})...")
    wait = WebDriverWait(driver, 30)
    
    try:
        xpath_query = "//a[contains(., 'WeeklyReport_20')]"
        links = wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_query)))
        
        valid_links = []
        for l in links:
            try:
                txt = l.text
                if all(x not in txt for x in ["Copy", "副本", "Template", "草稿", "Draft"]):
                    valid_links.append(l)
            except: continue
        
        if not valid_links: raise Exception("找不到任何符合的已發布週報連結 (已過濾草稿)")
        
        valid_links.sort(key=lambda x: x.text)
        latest_link = valid_links[-1]
        print(f"選定基準週報: {latest_link.text}，進入中...")
        
        driver.execute_script("arguments[0].click();", latest_link)
        time.sleep(5)
        wait.until(EC.url_contains("pages"))
        
        match = re.search(r"pages(?:/.*?)?/(\d+)", driver.current_url)
        if not match: match = re.search(r"pageId=(\d+)", driver.current_url)
        if not match: raise Exception(f"無法解析 Page ID: {driver.current_url}")
            
        page_id = match.group(1)
        
        space_match = re.search(r"(/wiki/spaces/[^/]+)", driver.current_url)
        url_prefix = space_match.group(1) if space_match else driver.current_url.split('/pages/')[0]
        base = f"{urlparse(driver.current_url).scheme}://{urlparse(driver.current_url).netloc}"
        if not url_prefix.startswith('/'): url_prefix = '/' + url_prefix
            
        copy_url = f"{base}{url_prefix}/pages/create?copyPageId={page_id}"
        print(f"跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        
        wait_long = WebDriverWait(driver, 60)
        # 等待按鈕出現代表 DOM 載入 (不論是否為標題)
        wait_long.until(EC.presence_of_element_located((By.TAG_NAME, "button")))
        print("編輯器 DOM 已載入！")
        
    except TimeoutException:
        print("導航逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    print(f"=== 開始重命名流程 (v16.0 精準定位版) ===")
    print(f"目標檔名: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    time.sleep(3) 
    
    # 【關鍵修正】根據您的 HTML 提供的精確定位器
    title_locators = [
        (By.NAME, "editpages-title"),                     # 最穩定的定位
        (By.CSS_SELECTOR, "textarea[data-test-id='editor-title']"), # 注意 test-id 有連字號
        (By.CSS_SELECTOR, "textarea[placeholder='給此頁面一個標題']"), # 中文 Placeholder
        (By.CSS_SELECTOR, "textarea[aria-label='給此頁面一個標題']"),
        (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']") # 保留舊版備用
    ]

    target_input = None

    # 策略 1: 精準定位
    print("策略 1: 嘗試使用精確 Selector...")
    for method, query in title_locators:
        try:
            elem = wait.until(EC.visibility_of_element_located((method, query)))
            target_input = elem
            print(f"--> 成功定位標題框！ (策略: {query})")
            break
        except: continue

    # 策略 2: 遍歷 Textarea (如果策略 1 失敗)
    if not target_input:
        print("策略 1 失敗，切換至策略 2 (遍歷 Textarea)...")
        try:
            textareas = driver.find_elements(By.TAG_NAME, "textarea")
            for i, ta in enumerate(textareas):
                if ta.is_displayed():
                    # 印出屬性以供除錯
                    attrs = f"name={ta.get_attribute('name')}, test-id={ta.get_attribute('data-test-id')}"
                    print(f"檢查 Textarea [{i}]: {attrs}")
                    
                    if "title" in attrs or "editor" in attrs:
                        target_input = ta
                        print("--> 鎖定目標 Textarea！")
                        break
        except Exception as e:
            print(f"搜尋 Textarea 錯誤: {e}")

    if not target_input:
        raise Exception("找不到任何可編輯的標題輸入框！")

    # 執行輸入
    try:
        # 確保可點擊
        try: target_input.click()
        except: driver.execute_script("arguments[0].click();", target_input)
        
        # 清除舊標題
        target_input.send_keys(Keys.CONTROL + "a")
        target_input.send_keys(Keys.BACK_SPACE)
        time.sleep(0.5)
        
        # 輸入新標題
        target_input.send_keys(f"WeeklyReport_{new_filename}")
        time.sleep(1)
        
        # 觸發 React 更新
        target_input.send_keys(Keys.ENTER)
        target_input.send_keys(Keys.BACK_SPACE)
        print("重命名動作完成。")
        
    except Exception as e:
        print(f"重命名輸入失敗: {str(e)}")
        driver.save_screenshot("rename_fatal.png")
        raise

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 30)
    print("捲動頁面喚醒內容...")
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(3)
    driver.execute_script("window.scrollTo(0, 0);")
    ensure_editor_active(driver)
    
    print("搜尋 Jira 表格...")
    macro_locators = [
        (By.CSS_SELECTOR, "div.datasourceView-content-wrap"),
        (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    ]
    
    macros = []
    for attempt in range(5):
        for locator in macro_locators:
            found = driver.find_elements(*locator)
            if found:
                macros = found
                print(f"找到 {len(found)} 個表格")
                break
        if macros: break
        print(f"嘗試 {attempt+1}/5: 尚未找到表格，等待中...")
        time.sleep(3)
            
    if not macros:
        print("警告：逾時仍找不到 Jira 表格。")
        return

    for i, target in enumerate(macros):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", target)
            
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
                print(f"[{i+1}] 更新: {dates_found[0]} -> {date_info['monday_str']}")
                
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
            print(f"[{i+1}] 略過: {str(e)}")
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            continue

def publish_page(driver):
    print("準備發布頁面...")
    wait = WebDriverWait(driver, 15)
    ensure_editor_active(driver)
    
    # 根據您提供的 HTML，data-testid 是正確的
    publish_locators = [
        (By.CSS_SELECTOR, "[data-testid='publish-button']"),
        (By.XPATH, "//button[contains(., '發佈')]"), # 使用您提供的 '發佈'
        (By.XPATH, "//button[contains(., 'Publish')]"),
        (By.CSS_SELECTOR, "button[appearance='primary']")
    ]
    
    target_btn = None
    for method, query in publish_locators:
        try:
            element = wait.until(EC.presence_of_element_located((method, query)))
            target_btn = element
            print(f"找到發布按鈕 (策略: {query})")
            break
        except: continue

    if target_btn:
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_btn)
            time.sleep(1)
            target_btn.click()
        except Exception as e:
            print(f"點擊失敗，嘗試 JS 強制點擊: {e}")
            driver.execute_script("arguments[0].click();", target_btn)
            
        print("發布動作已觸發！等待跳轉...")
        time.sleep(10)
    else:
        print("!!! 找不到發布按鈕 !!!")
        raise Exception("發布失敗")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 (v16.0 精準定位版) ===")
    print(f"日期: {dates['monday_str']} ~ {dates['sunday_str']}")
    
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
