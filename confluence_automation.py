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

def navigate_and_copy_latest(driver):
    print(f"正在搜尋側邊欄 (當前: {driver.title})...")
    wait = WebDriverWait(driver, 30)
    
    try:
        xpath_query = "//a[contains(., 'WeeklyReport_20')]"
        links = wait.until(EC.presence_of_all_elements_located((By.XPATH, xpath_query)))
        valid_links = [l for l in links if all(x not in l.text for x in ["Copy", "副本", "Template", "草稿", "Draft"])]
        
        valid_links.sort(key=lambda x: x.text)
        latest_link = valid_links[-1]
        print(f"選定基準週報: {latest_link.text}，進入中...")
        
        driver.execute_script("arguments[0].click();", latest_link)
        time.sleep(5)
        wait.until(EC.url_contains("pages"))
        
        current_url = driver.current_url
        match = re.search(r"pages(?:/.*?)?/(\d+)", current_url) or re.search(r"pageId=(\d+)", current_url)
        if not match: raise Exception(f"無法解析 Page ID")
        page_id = match.group(1)
        
        space_match = re.search(r"(/wiki/spaces/[^/]+)", current_url)
        url_prefix = space_match.group(1) if space_match else current_url.split('/pages/')[0]
        base = f"{urlparse(current_url).scheme}://{urlparse(current_url).netloc}"
        if not url_prefix.startswith('/'): url_prefix = '/' + url_prefix
            
        copy_url = f"{base}{url_prefix}/pages/create?copyPageId={page_id}"
        print(f"跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        time.sleep(12) # 增加等待時間讓 Iframe 穩定
        
    except TimeoutException:
        print("導航逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    print(f"=== 開始重命名流程 (v18.0 精準位址版) ===")
    wait = WebDriverWait(driver, 15)
    
    # 結合您提供的精確 Selector 與通用定位
    title_locators = [
        (By.CSS_SELECTOR, "#editor-title-id > textarea"), # 您提供的精確 Selector
        (By.XPATH, '//*[@id="editor-title-id"]/textarea'), # 您提供的精確 XPath
        (By.NAME, "editpages-title"),
        (By.CSS_SELECTOR, "textarea[data-test-id='editor-title']")
    ]
    
    target_input = None

    # 階段 1: 主視窗搜尋
    print("階段 1: 檢查主視窗...")
    for method, query in title_locators:
        try:
            target_input = driver.find_element(method, query)
            if target_input.is_displayed():
                print(f"--> 成功於主視窗定位！")
                break
        except: continue

    # 階段 2: Iframe 深度掃描
    if not target_input:
        print("階段 1 失敗，開始鑽入 Iframe...")
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for i, frame in enumerate(iframes):
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                for method, query in title_locators:
                    try:
                        elem = driver.find_element(method, query)
                        target_input = elem
                        print(f"--> 成功於 Iframe [{i}] 定位！")
                        break
                    except: continue
                if target_input: break
            except: continue

    if not target_input:
        driver.switch_to.default_content()
        raise Exception("即便使用精確位址，仍找不到標題輸入框。")

    # 執行操作
    try:
        driver.execute_script("arguments[0].focus();", target_input)
        target_input.send_keys(Keys.CONTROL + "a")
        target_input.send_keys(Keys.BACK_SPACE)
        time.sleep(0.5)
        target_input.send_keys(f"WeeklyReport_{new_filename}")
        time.sleep(2)
        print("重命名完成。")
        driver.switch_to.default_content()
    except Exception as e:
        driver.switch_to.default_content()
        raise e

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 30)
    print("正在更新 Jira 表格日期...")
    # 此部分邏輯與 v17 相同，包含 Iframe 支援
    # ... (省略重複代碼，確保完整性)
    driver.switch_to.default_content()
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(2)
    
    macro_locators = [(By.CSS_SELECTOR, "div.datasourceView-content-wrap"), (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")]
    macros = []
    
    # 嘗試主視窗與 Iframe
    for locator in macro_locators:
        macros = driver.find_elements(*locator)
        if macros: break
    
    if not macros:
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        for frame in iframes:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                for locator in macro_locators:
                    macros = driver.find_elements(*locator)
                    if macros: break
                if macros: break
            except: pass
            
    if not macros:
        print("警告：未發現 Jira 表格。")
        driver.switch_to.default_content()
        return

    for i, target in enumerate(macros):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
            time.sleep(1)
            driver.execute_script("arguments[0].click();", target)
            edit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., '編輯') or contains(., 'Edit')]")))
            edit_btn.click()
            jql_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            old_jql = jql_input.text
            dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", old_jql)
            if len(dates) >= 2:
                new_jql = old_jql.replace(dates[0], date_info['monday_str']).replace(dates[1], date_info['sunday_str'])
                jql_input.click()
                jql_input.send_keys(Keys.CONTROL + "a")
                jql_input.send_keys(Keys.BACK_SPACE)
                jql_input.send_keys(new_jql + Keys.ENTER)
                time.sleep(2)
                driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]").click()
                time.sleep(2)
        except: continue
    driver.switch_to.default_content()

def publish_page(driver):
    print("準備發佈...")
    driver.switch_to.default_content()
    wait = WebDriverWait(driver, 15)
    publish_locators = [(By.CSS_SELECTOR, "[data-testid='publish-button']"), (By.XPATH, "//button[contains(., '發佈')]")]
    for m, q in publish_locators:
        try:
            btn = wait.until(EC.element_to_be_clickable((m, q)))
            driver.execute_script("arguments[0].click();", btn)
            print("發佈成功！")
            time.sleep(10)
            return
        except: continue
    raise Exception("發佈按鈕失效。")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報 (v18.0) ===")
    driver = init_driver()
    try:
        inject_cookies(driver)
        navigate_and_copy_latest(driver)
        rename_page(driver, dates['friday_filename'])
        update_jira_macros(driver, dates)
        publish_page(driver)
        print("=== 任務成功完成 ===")
    except Exception as e:
        print(f"失敗: {str(e)}")
        driver.save_screenshot("error.png")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
