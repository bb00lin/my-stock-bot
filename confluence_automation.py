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
    
    # 前往 robots.txt 避免 SSO 轉址
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
    time.sleep(5)

def navigate_and_copy_latest(driver):
    """在側邊欄找到最新的週報並複製"""
    print("正在搜尋側邊欄的最新週報...")
    wait = WebDriverWait(driver, 15)
    
    # 1. 尋找側邊欄連結 (包含 'WeeklyReport_20' 文字的連結)
    # 使用寬鬆的 XPath 確保能抓到
    links = wait.until(EC.presence_of_all_elements_located(
        (By.XPATH, "//a[contains(text(), 'WeeklyReport_20')]")
    ))
    
    # 過濾掉可能是 'Copy of' 的連結，只留原本的日期格式，並排序找最新的
    valid_links = [l for l in links if "Copy" not in l.text and "副本" not in l.text]
    valid_links.sort(key=lambda x: x.text)
    
    if not valid_links:
        raise Exception("找不到任何符合 'WeeklyReport_20...' 格式的報告，請確認側邊欄是否展開。")
        
    latest_link = valid_links[-1]
    latest_name = latest_link.text
    print(f"找到最新週報: {latest_name}，正在進入...")
    
    # 點擊進入頁面
    driver.execute_script("arguments[0].click();", latest_link)
    time.sleep(5) # 等待頁面載入
    
    # 2. 點擊「更多動作 (...)」按鈕
    print("準備複製頁面...")
    # 嘗試多種可能的 selector (aria-label 可能是中文或英文)
    more_btn_locators = [
        (By.CSS_SELECTOR, "button[aria-label='更多動作']"),
        (By.CSS_SELECTOR, "button[aria-label='More actions']"),
        (By.CSS_SELECTOR, "[data-testid='page-metadata-actions-more-button']")
    ]
    
    more_btn = None
    for loc in more_btn_locators:
        try:
            more_btn = wait.until(EC.element_to_be_clickable(loc))
            break
        except: continue
            
    if not more_btn:
        raise Exception("找不到頁面右上角的 '...' (更多動作) 按鈕")
        
    more_btn.click()
    
    # 3. 點擊選單中的「複製」
    copy_btn = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//span[contains(text(), '複製') or contains(text(), 'Copy')]")
    ))
    copy_btn.click()
    
    print("已點擊複製，正在等待編輯器載入 (這可能需要一點時間)...")
    # 編輯器載入通常比較久，給它 15 秒
    time.sleep(15)

def rename_page(driver, new_filename):
    """修改頁面標題"""
    print(f"正在重命名為: WeeklyReport_{new_filename}")
    wait = WebDriverWait(driver, 20)
    
    # 定位標題輸入框 (Confluence Cloud 標準 ID)
    title_area = wait.until(EC.visibility_of_element_located(
        (By.CSS_SELECTOR, "textarea[data-testid='page-title-text-area']")
    ))
    
    title_area.click()
    # 全選刪除 (Linux 環境用 Control+a)
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
    
    # 確保頁面上有表格
    try:
        wait.until(EC.presence_of_element_located(macro_locator))
    except TimeoutException:
        print("警告：新頁面上找不到任何 Jira 表格，可能還在載入或結構不同。")
        return

    # 先計算有幾個表格，然後用 index 迴圈處理 (避免 DOM 更新後元素失效)
    macros_count = len(driver.find_elements(*macro_locator))
    print(f"共發現 {macros_count} 個表格。")

    for i in range(macros_count):
        try:
            # 每次重新抓取列表
            macros = driver.find_elements(*macro_locator)
            if i >= len(macros): break
            
            target = macros[i]
            # 捲動到該表格
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
            time.sleep(1)
            target.click()
            
            # 尋找浮動工具列上的「編輯」按鈕 (鉛筆圖示)
            # 支援中文「編輯」與英文「Edit」
            edit_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//button[@aria-label='編輯'] | //button[@aria-label='Edit'] | //span[text()='編輯'] | //span[text()='Edit']")
            ))
            edit_btn.click()
            
            # 等待 JQL 輸入框
            jql_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            
            # 讀取舊 JQL 並替換日期
            old_jql = jql_input.text
            date_pattern = r"\d{4}-\d{1,2}-\d{1,2}" # 格式 2026-1-12
            dates_found = re.findall(date_pattern, old_jql)
            
            if len(dates_found) >= 2:
                new_jql = old_jql
                # 替換前兩個日期為本週一與本週日
                new_jql = re.sub(dates_found[0], date_info['monday_str'], new_jql, count=1)
                new_jql = re.sub(dates_found[1], date_info['sunday_str'], new_jql, count=1)
                
                print(f"[{i+1}/{macros_count}] 更新日期: {dates_found[0]}~{dates_found[1]} -> {date_info['monday_str']}~{date_info['sunday_str']}")
                
                # 輸入新 JQL
                jql_input.click()
                jql_input.send_keys(Keys.CONTROL + "a")
                jql_input.send_keys(Keys.BACK_SPACE)
                time.sleep(0.1)
                jql_input.send_keys(new_jql)
                jql_input.send_keys(Keys.ENTER)
                time.sleep(1) # 等待驗證
                
                # 點擊「Insert / 插入」按鈕 (通常是右下角藍色按鈕)
                insert_btn = driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]")
                insert_btn.click()
                
                # 等待編輯框消失，確保儲存完成
                wait.until(EC.invisibility_of_element_located((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
                time.sleep(2)
            else:
                print(f"[{i+1}/{macros_count}] 跳過：未發現日期格式。")
                webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                
        except Exception as e:
            print(f"[{i+1}/{macros_count}] 處理失敗: {str(e)}")
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            continue

def publish_page(driver):
    """發布頁面"""
    print("準備發布頁面...")
    wait = WebDriverWait(driver, 10)
    try:
        # 發布按鈕 (通常右上角)
        publish_btn = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "[data-testid='publish-button']")
        ))
        publish_btn.click()
        
        # 等待發布完成 (通常會跳轉回檢視模式，URL 會變)
        time.sleep(10)
        print("頁面發布成功！")
    except Exception as e:
        print(f"發布失敗: {str(e)}")
        # 嘗試截圖
        driver.save_screenshot("publish_error.png")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報腳本 ===")
    print(f"設定: 本週區間 {dates['monday_str']} ~ {dates['sunday_str']} | 新檔名日期: {dates['friday_filename']}")
    
    driver = init_driver()
    try:
        # 1. 登入 (Cookie 注入)
        inject_cookies(driver)
        
        # 2. 導航至最新週報並複製
        navigate_and_copy_latest(driver)
        
        # 3. 重新命名 (新檔名為本週五)
        rename_page(driver, dates['friday_filename'])
        
        # 4. 批量更新 Jira 表格日期
        update_jira_macros(driver, dates)
        
        # 5. 發布
        publish_page(driver)
        
        print("=== 所有任務執行完畢 ===")
        
    except Exception as e:
        print(f"\n[CRITICAL ERROR] 執行中斷: {str(e)}")
        driver.save_screenshot("workflow_error.png")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
