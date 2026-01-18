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
        
        # 組建複製網址
        space_match = re.search(r"(/wiki/spaces/[^/]+)", driver.current_url)
        url_prefix = space_match.group(1) if space_match else driver.current_url.split('/pages/')[0]
        base = f"{urlparse(driver.current_url).scheme}://{urlparse(driver.current_url).netloc}"
        
        # 確保 prefix 開頭有 /
        if not url_prefix.startswith('/'): url_prefix = '/' + url_prefix
            
        copy_url = f"{base}{url_prefix}/pages/create?copyPageId={page_id}"
        print(f"跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        
        # 等待頁面核心載入 (檢測 publish-button 是否存在於 DOM，即使不可見)
        wait_long = WebDriverWait(driver, 60)
        wait_long.until(EC.presence_of_element_located((By.TAG_NAME, "button")))
        print("編輯器 DOM 已載入！")
        
    except TimeoutException:
        print("導航逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    print(f"=== 開始重命名流程 (v15.0 全域搜尋版) ===")
    print(f"目標檔名: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    time.sleep(5) # 讓編輯器完全 render 出來
    
    target_input = None
    
    # 【策略 1】尋找頁面上第一個可見的 Textarea
    # 標題通常是頁面上第一個且最大的 textarea
    try:
        textareas = driver.find_elements(By.TAG_NAME, "textarea")
        print(f"頁面上共發現 {len(textareas)} 個 Textarea。")
        
        for i, ta in enumerate(textareas):
            if ta.is_displayed():
                # 簡單過濾：標題的高度通常比較高，或者 placeholder 包含 Title/標題
                ph = ta.get_attribute("placeholder") or ""
                tid = ta.get_attribute("data-testid") or ""
                
                print(f"Textarea [{i}]: id={ta.get_attribute('id')}, testid={tid}, ph={ph}")
                
                if "title" in tid or "title" in ph.lower() or "標題" in ph or i == 0:
                    target_input = ta
                    print("--> 鎖定目標 Textarea！")
                    break
    except Exception as e:
        print(f"搜尋 Textarea 錯誤: {e}")

    # 【策略 2】如果策略 1 失敗，嘗試 CSS Selector
    if not target_input:
        print("策略 1 失敗，嘗試 CSS Selector...")
        try:
            target_input = wait.until(EC.visibility_of_element_located(
                (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")
            ))
        except: pass

    if not target_input:
        raise Exception("找不到任何可編輯的標題輸入框！")

    # 執行輸入
    try:
        try: target_input.click()
        except: driver.execute_script("arguments[0].click();", target_input)
        
        target_input.send_keys(Keys.CONTROL + "a")
        target_input.send_keys(Keys.BACK_SPACE)
        time.sleep(0.5)
        target_input.send_keys(f"WeeklyReport_{new_filename}")
        time.sleep(1)
        # 按一下 Enter 確保 React 狀態更新
        target_input.send_keys(Keys.ENTER)
        target_input.send_keys(Keys.BACK_SPACE)
        
    except Exception as e:
        print(f"重命名輸入失敗: {str(e)}")
        driver.save_screenshot("rename_fatal.png")
        raise

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 30)
    print("捲動頁面喚醒內容...")
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(3) # 增加等待時間
    driver.execute_script("window.scrollTo(0, 0);")
    ensure_editor_active(driver)
    
    print("搜尋 Jira 表格...")
    macro_locators = [
        (By.CSS_SELECTOR, "div.datasourceView-content-wrap"),
        (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    ]
    
    macros = []
    # 增加重試次數到 5 次，每次間隔 3 秒
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
    
    # 【修正點】加入正確的 '發佈' (佈)
    publish_locators = [
        (By.CSS_SELECTOR, "[data-testid='publish-button']"),
        (By.XPATH, "//button[@data-testid='publish-button']"), # XPATH 版的 testid
        (By.XPATH, "//button[contains(., '發佈')]"), # 正確的中文字
        (By.XPATH, "//button[contains(., 'Publish')]"),
        (By.CSS_SELECTOR, "button[appearance='primary']")
    ]
    
    target_btn = None
    
    # 策略 A: 依序尋找 Locator
    for method, query in publish_locators:
        try:
            element = wait.until(EC.presence_of_element_located((method, query)))
            target_btn = element
            print(f"找到發布按鈕 (策略: {query})")
            break
        except: continue

    # 策略 B: 暴力遍歷所有按鈕
    if not target_btn:
        print("策略 A 失敗，開始暴力搜尋按鈕文字...")
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            txt = btn.text
            if "Publish" in txt or "發佈" in txt or "發布" in txt:
                target_btn = btn
                print(f"暴力搜尋找到按鈕: {txt}")
                break

    if target_btn:
        try:
            # 捲動到按鈕
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target_btn)
            time.sleep(1)
            target_btn.click()
        except Exception as e:
            print(f"點擊失敗，嘗試 JS 強制點擊: {e}")
            driver.execute_script("arguments[0].click();", target_btn)
            
        print("發布動作已觸發！等待跳轉...")
        time.sleep(10)
    else:
        print("!!! 找不到發布按鈕，傾印按鈕文字 !!!")
        buttons = driver.find_elements(By.TAG_NAME, "button")
        print([b.text for b in buttons if b.is_displayed()])
        raise Exception("發布失敗")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 (v15.0 最終修正版) ===")
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
