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

def ensure_editor_active(driver):
    """點擊頁面空白處以喚醒編輯器工具列"""
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        body.click()
        # 嘗試按一下 ESC 關閉任何干擾視窗
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
        
        wait_long = WebDriverWait(driver, 60)
        # 等待標題框出現，代表編輯器核心已載入
        wait_long.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")))
        print("編輯器核心已就緒！")
        
    except TimeoutException:
        print("導航逾時！")
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    print(f"重命名為: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    
    title_area = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")))
    title_area.click()
    title_area.send_keys(Keys.CONTROL + "a")
    title_area.send_keys(Keys.BACK_SPACE)
    time.sleep(0.5)
    title_area.send_keys(f"WeeklyReport_{new_filename}")
    time.sleep(2)

def update_jira_macros(driver, date_info):
    wait = WebDriverWait(driver, 20)
    ensure_editor_active(driver)
    
    print("開始搜尋 Jira 表格...")
    # 增加一個備用定位器 (datasourceView-content-wrap)
    macro_locators = [
        (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']"),
        (By.CSS_SELECTOR, "div.datasourceView-content-wrap")
    ]
    
    macros = []
    for locator in macro_locators:
        try:
            found = driver.find_elements(*locator)
            if found:
                print(f"使用定位器 {locator[1]} 找到 {len(found)} 個表格")
                macros = found
                break
        except: continue
            
    if not macros:
        print("警告：找不到任何 Jira 表格。可能還在載入中。")
        return

    print(f"準備更新 {len(macros)} 個表格...")
    # 這裡因為 DOM 結構複雜，我們使用 JS 來觸發點擊，避免被覆蓋
    for i, target in enumerate(macros):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
            time.sleep(1)
            # 使用 JS 點擊避免 ElementClickInterceptedException
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
                
                print(f"[{i+1}] 更新日期: {dates_found[0]} -> {date_info['monday_str']}")
                
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
    wait = WebDriverWait(driver, 30) # 給予 30 秒等待工具列出現
    ensure_editor_active(driver) # 再次喚醒頁面
    
    try:
        # 使用您提供的確切 data-testid
        print("正在尋找 [data-testid='publish-button']...")
        publish_btn = wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "[data-testid='publish-button']")
        ))
        
        # 確保按鈕可見並捲動到視線內
        driver.execute_script("arguments[0].scrollIntoView(true);", publish_btn)
        time.sleep(1)
        
        # 嘗試點擊
        try:
            publish_btn.click()
        except ElementClickInterceptedException:
            print("標準點擊被阻擋，嘗試 JS 強制點擊...")
            driver.execute_script("arguments[0].click();", publish_btn)
            
        print("已觸發發布動作！等待頁面跳轉確認...")
        time.sleep(10)
        print("流程結束。")
        
    except Exception as e:
        print(f"發布失敗: {str(e)}")
        
        # 【關鍵除錯】儲存頁面原始碼，讓我們看看為什麼找不到
        print("正在儲存頁面 HTML 結構以供除錯 (debug_page_source.html)...")
        with open("debug_page_source.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
            
        driver.save_screenshot("publish_error.png")
        raise

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 (v9.0 焦點喚醒版) ===")
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
