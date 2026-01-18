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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException, ElementNotInteractableException
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
        
        if not valid_links: raise Exception("找不到任何符合的已發布週報連結")
        
        valid_links.sort(key=lambda x: x.text)
        latest_link = valid_links[-1]
        print(f"選定基準週報: {latest_link.text}，進入中...")
        
        driver.execute_script("arguments[0].click();", latest_link)
        time.sleep(5)
        wait.until(EC.url_contains("pages"))
        
        current_url = driver.current_url
        print(f"當前網址: {current_url}")
        
        match = re.search(r"pages(?:/.*?)?/(\d+)", current_url)
        if not match: match = re.search(r"pageId=(\d+)", current_url)
        if not match: raise Exception(f"無法解析 Page ID: {current_url}")
            
        page_id = match.group(1)
        
        space_match = re.search(r"(/wiki/spaces/[^/]+)", current_url)
        if space_match:
            copy_url = f"{parsed_base_url(driver)}{space_match.group(1)}/pages/create?copyPageId={page_id}"
        else:
            copy_url = f"{current_url.split('/pages/')[0]}/pages/create?copyPageId={page_id}"

        print(f"跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        
        # 等待標題容器載入 (這是我們目前唯一確定存在的東西)
        wait_long = WebDriverWait(driver, 60)
        wait_long.until(EC.presence_of_element_located((By.ID, "content-title")))
        print("編輯器容器已載入！")
        
    except TimeoutException:
        print("導航逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def parsed_base_url(driver):
    u = urlparse(driver.current_url)
    return f"{u.scheme}://{u.netloc}"

def rename_page(driver, new_filename):
    print(f"=== 開始重命名流程 (v14.0 結構診斷版) ===")
    print(f"目標檔名: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    
    # 1. 先抓取標題容器
    try:
        container = wait.until(EC.presence_of_element_located((By.ID, "content-title")))
        print("已定位標題容器 (content-title)。")
        
        # 【診斷】印出容器內部的 HTML，這行 Log 會救我們一命
        print("--- DEBUG: 標題容器內部 HTML ---")
        print(container.get_attribute('innerHTML'))
        print("--------------------------------")
        
        # 2. 嘗試在容器內尋找任何看起來像輸入框的東西
        input_element = None
        try:
            # 優先找 textarea
            input_element = container.find_element(By.TAG_NAME, "textarea")
            print("策略 A: 找到 textarea")
        except:
            try:
                # 其次找 input
                input_element = container.find_element(By.TAG_NAME, "input")
                print("策略 B: 找到 input")
            except:
                try:
                    # 最後找 contenteditable div
                    input_element = container.find_element(By.CSS_SELECTOR, "[contenteditable='true']")
                    print("策略 C: 找到 contenteditable")
                except:
                    print("策略失敗: 容器內找不到明顯的輸入元件。")

        # 3. 執行輸入
        if input_element:
            print("正在對找到的元件執行輸入...")
            try:
                input_element.click()
            except:
                driver.execute_script("arguments[0].click();", input_element)
            
            input_element.send_keys(Keys.CONTROL + "a")
            input_element.send_keys(Keys.BACK_SPACE)
            time.sleep(0.5)
            input_element.send_keys(f"WeeklyReport_{new_filename}")
            time.sleep(1)
        else:
            # 【盲打策略】如果找不到輸入框，嘗試點擊容器並對「當前焦點」打字
            print("啟動【盲打模式】: 點擊容器並發送按鍵...")
            driver.execute_script("arguments[0].click();", container)
            time.sleep(1)
            
            # 使用 ActionChains 對 active element 打字
            actions = webdriver.ActionChains(driver)
            actions.send_keys(Keys.CONTROL + "a")
            actions.send_keys(Keys.BACK_SPACE)
            actions.pause(0.5)
            actions.send_keys(f"WeeklyReport_{new_filename}")
            actions.perform()
            
            print("盲打指令發送完畢。")

        time.sleep(2) # 等待生效
        
    except Exception as e:
        print(f"重命名流程嚴重錯誤: {str(e)}")
        driver.save_screenshot("rename_fatal.png")
        raise

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 30)
    print("捲動頁面喚醒內容...")
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)
    driver.execute_script("window.scrollTo(0, 0);")
    ensure_editor_active(driver)
    
    print("搜尋 Jira 表格...")
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
        print("警告：找不到 Jira 表格。")
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
    
    btn = None
    for method, query in publish_locators:
        try:
            element = wait.until(EC.presence_of_element_located((method, query)))
            btn = element
            break
        except: continue

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
    print(f"=== Confluence 自動週報腳本 (v14.0 結構診斷版) ===")
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
