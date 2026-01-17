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
    time.sleep(10)

def navigate_and_copy_latest(driver):
    """在側邊欄找到最新的週報並複製 (修復版)"""
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
        
        driver.execute_script("arguments[0].click();", latest_link)
        
        # 【關鍵修改】不再等待「編輯」按鈕，改為等待頁面標題 (h1) 出現
        print("等待頁面標題載入...")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
        time.sleep(5) # 多等 5 秒讓頂部導航列載入
        print("頁面載入完成，準備尋找操作選單。")
        
        # 嘗試點擊「...」按鈕
        more_btn_locators = [
            (By.CSS_SELECTOR, "[data-testid='page-metadata-actions-more-button']"),
            (By.XPATH, "//button[@aria-label='更多動作']"),
            (By.XPATH, "//button//span[@aria-label='更多動作']/.."),
            (By.CSS_SELECTOR, "button[aria-label='More actions']")
        ]
        
        menu_opened = False
        for loc in more_btn_locators:
            try:
                btn = driver.find_element(*loc)
                btn.click()
                menu_opened = True
                print("成功點擊 '...' 按鈕。")
                break
            except: continue
        
        # 【保險方案】如果按鈕點不到，使用鍵盤快捷鍵 "." (句號) 呼叫選單
        if not menu_opened:
            print("按鈕點擊失敗，使用鍵盤快捷鍵 '.' (句號) 呼叫選單...")
            webdriver.ActionChains(driver).send_keys(".").perform()
            time.sleep(2) # 等待對話框
            
            # 輸入 "複製" 並按 Enter
            print("發送 '複製' 指令...")
            webdriver.ActionChains(driver).send_keys("複製").pause(1).send_keys(Keys.ENTER).perform()
        
        else:
            # 如果選單成功打開，點擊「複製」
            print("選單已開，點擊 '複製'...")
            try:
                copy_option = wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "//span[contains(text(), '複製') or contains(text(), 'Copy')]")
                ))
                copy_option.click()
            except TimeoutException:
                # 選單開了但找不到選項，可能是英文介面或需要滾動，嘗試快捷鍵補救
                print("選單中找不到選項，嘗試快捷鍵補救...")
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                webdriver.ActionChains(driver).send_keys(".").pause(1).send_keys("複製").pause(1).send_keys(Keys.ENTER).perform()

        print("已觸發複製，等待編輯器載入 (網址應包含 copy-page)...")
        wait.until(EC.url_contains("copy-page"))
        # 編輯器載入很重，給予充足時間
        time.sleep(15)
        
    except TimeoutException:
        print("操作逾時！請檢查截圖確認當前畫面狀態。")
        driver.save_screenshot("nav_error.png")
        raise

def rename_page(driver, new_filename):
    """修改頁面標題"""
    print(f"正在重命名為: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    
    try:
        title_area = wait.until(EC.visibility_of_element_located(
            (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")
        ))
        
        title_area.click()
        title_area.send_keys(Keys.CONTROL + "a")
        title_area.send_keys(Keys.BACK_SPACE)
        time.sleep(0.5)
        title_area.send_keys(f"WeeklyReport_{new_filename}")
        time.sleep(1)
    except TimeoutException:
        print("找不到標題輸入框，可能編輯器尚未載入完成。")
        raise

def update_jira_macros(driver, date_info):
    """迴圈更新所有 Jira 表格的日期"""
    wait = WebDriverWait(driver, 20)
    print("開始掃描並更新 Jira 表格...")
    
    macro_locator = (By.CSS_SELECTOR, "div[data-prosemirror-node-name='blockCard']")
    
    try:
        wait.until(EC.presence_of_element_located(macro_locator))
    except TimeoutException:
        print("警告：找不到 Jira 表格，可能頁面空白或載入不全。")
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
                
                print(f"[{i+1}/{macros_count}] 更新: {dates_found[0]}~{dates_found[1]} -> {date_info['monday_str']}~{date_info['sunday_str']}")
                
                jql_input.click()
                jql_input.send_keys(Keys.CONTROL + "a")
                jql_input.send_keys(Keys.BACK_SPACE)
                time.sleep(0.1)
                jql_input.send_keys(new_jql)
                jql_input.send_keys(Keys.ENTER)
                time.sleep(1)
                
                insert_btn = driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]")
                insert_btn.click()
                
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
                time.sleep(2)
            else:
                print(f"[{i+1}/{macros_count}] 跳過：未發現日期。")
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
        time.sleep(10)
        print("頁面發布成功！")
    except Exception as e:
        print(f"發布失敗: {str(e)}")
        driver.save_screenshot("publish_error.png")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 ===")
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
