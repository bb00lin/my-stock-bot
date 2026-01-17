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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
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
    print(f"正在搜尋: {description}...")
    for method, query in locators:
        try:
            element = wait.until(EC.presence_of_element_located((method, query)))
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
            print(f"--> 成功定位: {description} (策略: {query})")
            return element
        except: continue
    return None

def ensure_editor_active(driver):
    try:
        driver.find_element(By.TAG_NAME, "body").click()
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    except: pass

def navigate_and_copy_latest(driver):
    print(f"正在搜尋側邊欄 (當前: {driver.title})...")
    wait = WebDriverWait(driver, 30)
    
    try:
        # 抓取包含 "WeeklyReport_20" 的連結
        xpath_query = "//a[contains(., 'WeeklyReport_20')]"
        links = wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_query)))
        
        valid_links = []
        for l in links:
            try:
                txt = l.text
                # 【修正點】排除「副本」、「Copy」以及「草稿」、「Draft」
                if all(x not in txt for x in ["Copy", "副本", "Template", "草稿", "Draft"]):
                    valid_links.append(l)
            except: continue
        
        if not valid_links: raise Exception("找不到任何符合的已發布週報連結 (已過濾草稿)")
        
        valid_links.sort(key=lambda x: x.text)
        latest_link = valid_links[-1]
        print(f"選定基準週報: {latest_link.text}，進入中...")
        
        driver.execute_script("arguments[0].click();", latest_link)
        time.sleep(5)
        # 等待網址包含 pages，代表已進入內容頁
        wait.until(EC.url_contains("pages"))
        
        current_url = driver.current_url
        print(f"當前網址: {current_url}")
        
        # 【修正點】更強大的 ID 解析 Regex
        # 支援: /pages/123, /pages/edit/123, /pages/viewpage.action?pageId=123
        match = re.search(r"pages(?:/.*?)?/(\d+)", current_url)
        if not match:
            # 嘗試另一種 Query String 格式 (pageId=123)
            match = re.search(r"pageId=(\d+)", current_url)
            
        if not match:
            raise Exception(f"無法解析 Page ID，網址格式不符: {current_url}")
            
        page_id = match.group(1)
        print(f"Page ID 解析成功: {page_id}")
        
        # 這裡需要小心：如果網址本身包含 /wiki/spaces/... 我們要保留前面這段
        # 安全做法：直接組建標準複製網址，假設前面是 /wiki/spaces/SPACE_KEY/...
        # 我們試著從 current_url 擷取 /wiki/spaces/xxx/ 這一段
        
        space_match = re.search(r"(/wiki/spaces/[^/]+)", current_url)
        if space_match:
            url_prefix = space_match.group(1)
            # 組合出標準 Cloud 複製網址
            copy_url = f"{parsed_base_url(driver)}{url_prefix}/pages/create?copyPageId={page_id}"
        else:
            # 如果抓不到 Space，退回到相對路徑替換法
            # 假設結構是 .../pages/...
            url_part = current_url.split('/pages/')[0]
            copy_url = f"{url_part}/pages/create?copyPageId={page_id}"

        print(f"跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        
        wait_long = WebDriverWait(driver, 60)
        title_box = find_element_omnibus(driver, wait_long, "標題輸入框", [
            (By.ID, "content-title"),
            (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']"),
            (By.CSS_SELECTOR, "textarea[placeholder='Page title']")
        ])
        
        if not title_box: raise Exception("編輯器無法載入 (找不到標題框)")
        print("編輯器就緒！")
        
    except TimeoutException:
        print("導航逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def parsed_base_url(driver):
    """取得當前網址的 Scheme + Netloc (例如 https://site.atlassian.net)"""
    u = urlparse(driver.current_url)
    return f"{u.scheme}://{u.netloc}"

def rename_page(driver, new_filename):
    print(f"重命名為: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    title_area = find_element_omnibus(driver, wait, "標題輸入框", [
        (By.ID, "content-title"),
        (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")
    ])
    try: title_area.click()
    except: driver.execute_script("arguments[0].click();", title_area)
    
    title_area.send_keys(Keys.CONTROL + "a")
    title_area.send_keys(Keys.BACK_SPACE)
    time.sleep(0.5)
    title_area.send_keys(f"WeeklyReport_{new_filename}")
    time.sleep(2)

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 30)
    print("正在捲動頁面以載入內容...")
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    driver.execute_script("window.scrollTo(0, 0);")
    ensure_editor_active(driver)
    
    print("開始搜尋 Jira 表格...")
    macro_locators = [
        (By.CSS_SELECTOR, "div.datasourceView-content-wrap"),
        (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    ]
    
    macros = []
    for _ in range(3):
        for locator in macro_locators:
            found = driver.find_elements(*locator)
            if found:
                macros = found
                print(f"找到 {len(found)} 個表格")
                break
        if macros: break
        time.sleep(2)
            
    if not macros:
        print("警告：找不到 Jira 表格，可能載入失敗。")
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
    
    publish_locators = [
        (By.CSS_SELECTOR, "[data-testid='publish-button']"),
        (By.ID, "publish-button"),
        (By.XPATH, "//button[contains(., '發布')]"),
        (By.CSS_SELECTOR, "button[appearance='primary']")
    ]
    
    btn = find_element_omnibus(driver, wait, "發布按鈕", publish_locators)
    if btn:
        try: btn.click()
        except: driver.execute_script("arguments[0].click();", btn)
        print("發布動作已觸發！")
        time.sleep(10)
    else:
        print("!!! 找不到發布按鈕，傾印按鈕文字 !!!")
        buttons = driver.find_elements(By.TAG_NAME, "button")
        print([b.text for b in buttons if b.is_displayed()])
        raise Exception("發布失敗")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 (v12.0 穩定版) ===")
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
