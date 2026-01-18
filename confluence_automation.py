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
from selenium.common.exceptions import TimeoutException
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
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def inject_cookies(driver):
    print("正在注入 Cookies...")
    if not COOKIES_JSON: raise Exception("找不到 COOKIES")
    cookies = json.loads(COOKIES_JSON)
    base_url = f"{urlparse(URL).scheme}://{urlparse(URL).netloc}"
    driver.get(f"{base_url}/robots.txt")
    for c in cookies:
        try:
            driver.add_cookie({
                'name': c['name'], 'value': c['value'],
                'path': c.get('path', '/'), 'secure': c.get('secure', True),
                'expiry': int(c.get('expirationDate', c.get('expiry', time.time()+86400)))
            })
        except: pass
    driver.get(URL)
    time.sleep(8)

def navigate_and_copy_latest(driver):
    wait = WebDriverWait(driver, 30)
    print("正在搜尋基準週報...")
    links = wait.until(EC.presence_of_all_elements_located((By.XPATH, "//a[contains(., 'WeeklyReport_20')]")))
    valid = sorted([l for l in links if all(x not in l.text for x in ["Copy", "副本", "草稿"])], key=lambda x: x.text)
    latest_link = valid[-1]
    print(f"選定: {latest_link.text}")
    driver.execute_script("arguments[0].click();", latest_link)
    time.sleep(5)
    
    match = re.search(r"pages(?:/.*?)?/(\d+)", driver.current_url) or re.search(r"pageId=(\d+)", driver.current_url)
    page_id = match.group(1)
    url_prefix = re.search(r"(/wiki/spaces/[^/]+)", driver.current_url).group(1)
    copy_url = f"{urlparse(driver.current_url).scheme}://{urlparse(driver.current_url).netloc}{url_prefix}/pages/create?copyPageId={page_id}"
    print(f"前往複製網址: {copy_url}")
    driver.get(copy_url)
    time.sleep(15)

def rename_page_js_force(driver, new_title):
    """
    使用 JS 強制在全域(含 Iframe) 尋找標題框並賦值
    """
    print(f"=== 執行 JS 強制重命名: {new_title} ===")
    
    # 強力 JS 腳本：遍歷所有 textarea 並檢查父層 ID
    js_script = """
    function forceRename(val) {
        var found = false;
        // 1. 檢查主視窗
        var tas = document.querySelectorAll('textarea');
        for(var i=0; i<tas.length; i++) {
            if(tas[i].closest('#editor-title-id') || tas[i].name == 'editpages-title') {
                tas[i].value = val;
                tas[i].dispatchEvent(new Event('input', { bubbles: true }));
                tas[i].dispatchEvent(new Event('blur', { bubbles: true }));
                found = true;
            }
        }
        // 2. 檢查所有 Iframe
        var frames = document.querySelectorAll('iframe');
        for(var j=0; j<frames.length; j++) {
            try {
                var doc = frames[j].contentDocument || frames[j].contentWindow.document;
                var ftas = doc.querySelectorAll('textarea');
                for(var k=0; k<ftas.length; k++) {
                    if(ftas[k].closest('#editor-title-id') || ftas[k].name == 'editpages-title') {
                        ftas[k].value = val;
                        ftas[k].dispatchEvent(new Event('input', { bubbles: true }));
                        ftas[k].dispatchEvent(new Event('blur', { bubbles: true }));
                        found = true;
                    }
                }
            } catch(e) {}
        }
        return found;
    }
    return forceRename(arguments[0]);
    """
    
    success = driver.execute_script(js_script, f"WeeklyReport_{new_title}")
    if success:
        print("✅ JS 回報標題修改成功")
    else:
        print("❌ JS 未能找到標題框，嘗試盲打補救...")
        driver.execute_script("window.scrollTo(0,0);")
        webdriver.ActionChains(driver).send_keys(Keys.TAB).send_keys(Keys.CONTROL + "a").send_keys(Keys.BACK_SPACE).send_keys(f"WeeklyReport_{new_title}").perform()

def update_jira_macros(driver, date_info):
    print("正在處理 Jira 表格日期...")
    time.sleep(5)
    # 捲動以觸發載入
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
    time.sleep(2)
    
    # 簡單點擊「編輯」按鈕的 JS 版 (更穩定)
    js_click_edit = """
    var btns = document.querySelectorAll('button');
    for(var i=0; i<btns.length; i++) {
        if(btns[i].innerText.includes('編輯') || btns[i].innerText.includes('Edit')) {
            btns[i].click(); return true;
        }
    }
    return false;
    """
    
    # 這裡我們只處理第一個表格作為範例，若有多個可迴圈
    macros = driver.find_elements(By.CSS_SELECTOR, "div.datasourceView-content-wrap")
    if macros:
        driver.execute_script("arguments[0].click();", macros[0])
        time.sleep(1)
        driver.execute_script(js_click_edit)
        time.sleep(3)
        try:
            jql_input = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            old_jql = jql_input.text
            dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", old_jql)
            if len(dates) >= 2:
                new_jql = old_jql.replace(dates[0], date_info['monday_str']).replace(dates[1], date_info['sunday_str'])
                jql_input.click()
                jql_input.send_keys(Keys.CONTROL + "a" + Keys.BACK_SPACE)
                jql_input.send_keys(new_jql + Keys.ENTER)
                time.sleep(2)
                driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]").click()
        except: print("表格更新跳過")

def publish_page(driver):
    print("執行發佈...")
    driver.execute_script("window.scrollTo(0,0);")
    time.sleep(2)
    publish_js = """
    var buttons = document.querySelectorAll('button');
    for(var i=0; i<buttons.length; i++) {
        if(buttons[i].getAttribute('data-testid') == 'publish-button' || buttons[i].innerText.includes('發佈')) {
            buttons[i].click(); return true;
        }
    }
    return false;
    """
    if driver.execute_script(publish_js):
        print("✅ 已觸發發佈按鈕")
        time.sleep(10)
    else:
        print("❌ 找不到發佈按鈕")

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報 (v19.0 JS 威力加強版) ===")
    driver = init_driver()
    try:
        inject_cookies(driver)
        navigate_and_copy_latest(driver)
        rename_page_js_force(driver, dates['friday_filename'])
        update_jira_macros(driver, dates)
        publish_page(driver)
        print("=== 任務執行結束 ===")
    except Exception as e:
        print(f"發生錯誤: {str(e)}")
        driver.save_screenshot("v19_error.png")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
