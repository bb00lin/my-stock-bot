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

def find_title_element(driver, wait):
    """【廣域搜尋】嘗試多種方式尋找標題輸入框"""
    print("正在尋找標題輸入框...")
    
    # 定義所有可能的標題定位器
    locators = [
        (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']"), # Cloud 標準
        (By.CSS_SELECTOR, "textarea[aria-label='Page title']"),           # 輔助標籤
        (By.CSS_SELECTOR, "textarea[placeholder='Page title']"),          # 英文 Placeholder
        (By.CSS_SELECTOR, "textarea[placeholder='頁面標題']"),             # 中文 Placeholder
        (By.ID, "content-title"),                                         # 舊版 ID
        (By.CSS_SELECTOR, "h1 textarea")                                  # 結構化定位
    ]
    
    for method, query in locators:
        try:
            # 使用 presence (存在即可)，因為 visibility 有時會被彈窗誤判
            element = wait.until(EC.presence_of_element_located((method, query)))
            print(f"成功定位標題框！使用策略: {query}")
            return element
        except:
            continue
            
    raise Exception("遍歷所有已知策略後，仍找不到標題輸入框。")

def navigate_and_copy_latest(driver):
    """網址解析跳轉法 (v7.0)"""
    print(f"正在搜尋側邊欄 (當前頁面: {driver.title})...")
    wait = WebDriverWait(driver, 30)
    
    try:
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
        print(f"找到最新週報: {latest_name}，進入中...")
        
        driver.execute_script("arguments[0].click();", latest_link)
        
        print("等待頁面載入以獲取 Page ID...")
        time.sleep(5)
        wait.until(EC.url_contains("pages"))
        
        current_url = driver.current_url
        match = re.search(r"/pages/(\d+)", current_url)
        if not match:
            raise Exception(f"無法解析 Page ID: {current_url}")
            
        page_id = match.group(1)
        print(f"Page ID: {page_id}")
        
        # 建構複製網址
        url_prefix = current_url.split('/pages/')[0]
        copy_url = f"{url_prefix}/pages/create?copyPageId={page_id}"
        
        print(f"跳轉至複製頁面: {copy_url}")
        driver.get(copy_url)
        
        # 延長等待，並嘗試移除遮擋物
        print("等待編輯器載入...")
        wait_long = WebDriverWait(driver, 60)
        
        # 嘗試按 ESC 關閉可能存在的「新功能介紹」彈窗
        webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(1)
        
        # 使用廣域搜尋確認編輯器是否就緒
        find_title_element(driver, wait_long)
        print("編輯器確認就緒！")
        
    except TimeoutException:
        print(f"導航或載入逾時！當前標題: {driver.title}")
        # 印出頁面結構幫助除錯
        try:
            body = driver.find_element(By.TAG_NAME, "body").get_attribute('innerHTML')
            print(f"DEBUG: 頁面 HTML 片段 (前 500 字): {body[:500]}")
        except: pass
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    """修改頁面標題 (使用廣域搜尋)"""
    print(f"正在重命名為: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    
    # 再次尋找標題框
    title_area = find_title_element(driver, wait)
    
    # 確保元素可見並可點擊
    driver.execute_script("arguments[0].scrollIntoView(true);", title_area)
    time.sleep(1)
    
    try:
        title_area.click()
    except ElementClickInterceptedException:
        print("標題框被遮擋，嘗試用 JS 強制點擊...")
        driver.execute_script("arguments[0].click();", title_area)
    
    # 清空並輸入
    title_area.send_keys(Keys.CONTROL + "a")
    title_area.send_keys(Keys.BACK_SPACE)
    time.sleep(0.5)
    title_area.send_keys(f"WeeklyReport_{new_filename}")
    time.sleep(1)

def update_jira_macros(driver, date_info):
    """迴圈更新所有 Jira 表格的日期"""
    wait = WebDriverWait(driver, 20)
    print("開始掃描並更新 Jira 表格...")
    
    macro_locator = (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    
    try:
        wait.until(EC.presence_of_element_located(macro_locator))
    except TimeoutException:
        print("警告：找不到 Jira 表格。")
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
                new_jql = old_jql
                new_jql = re.sub(dates_found[0], date_info['monday_str'], new_jql, count=1)
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
        # 發布按鈕
        publish_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "[data-testid='publish-button']")
        ))
        publish_btn.click()
        time.sleep(10)
        print("頁面發布成功！")
    except Exception as e:
        print(f"發布失敗: {str(e)}")
        driver.save_screenshot("publish_error.png")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 (v7.0 廣域搜尋版) ===")
    print(f"本週區間: {dates['monday_str']} ~ {dates['sunday_str']} | 檔名: {dates['friday_filename']}")
    
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
