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
    print(f"選定基準: {latest_link.text}")
    driver.execute_script("arguments[0].click();", latest_link)
    time.sleep(5)
    
    match = re.search(r"pages(?:/.*?)?/(\d+)", driver.current_url) or re.search(r"pageId=(\d+)", driver.current_url)
    page_id = match.group(1)
    space_prefix = re.search(r"(/wiki/spaces/[^/]+)", driver.current_url).group(1)
    copy_url = f"{urlparse(driver.current_url).scheme}://{urlparse(driver.current_url).netloc}{space_prefix}/pages/create?copyPageId={page_id}"
    print(f"前往複製網址: {copy_url}")
    driver.get(copy_url)
    # 這裡給予充分時間讓 React 元件載入
    time.sleep(25) 

def rename_page_js_force(driver, new_title):
    print(f"=== 執行 JS 強制重命名: WeeklyReport_{new_title} ===")
    js_script = """
    function setVal(el, val) {
        el.value = val;
        ['input', 'change', 'blur', 'keydown', 'keyup'].forEach(ev => {
            el.dispatchEvent(new Event(ev, { bubbles: true }));
        });
    }
    function scan(val) {
        let found = false;
        const searchDocs = [document];
        document.querySelectorAll('iframe').forEach(f => {
            try { searchDocs.push(f.contentDocument || f.contentWindow.document); } catch(e) {}
        });
        
        searchDocs.forEach(doc => {
            doc.querySelectorAll('textarea').forEach(ta => {
                // 根據您的 HTML 結構，匹配 data-test-id 或 editor-title
                if (ta.getAttribute('data-test-id') === 'editor-title' || ta.name === 'editpages-title' || ta.placeholder.includes('標題')) {
                    setVal(ta, val);
                    found = true;
                }
            });
        });
        return found;
    }
    return scan(arguments[0]);
    """
    # 執行多次嘗試以應對非同步載入
    for i in range(3):
        success = driver.execute_script(js_script, f"WeeklyReport_{new_title}")
        if success:
            print(f"✅ 第 {i+1} 次嘗試：標題設定成功")
            return
        time.sleep(3)
        
    print("⚠️ JS 標題設定失敗，執行盲打補救...")
    driver.execute_script("window.scrollTo(0,0);")
    webdriver.ActionChains(driver).send_keys(Keys.TAB).pause(0.5).send_keys(Keys.CONTROL + "a").send_keys(Keys.BACK_SPACE).send_keys(f"WeeklyReport_{new_title}").perform()

def update_jira_macros(driver, date_info):
    print("開始自動更新頁面所有 Jira 表格日期...")
    # 增加捲動次數確保觸發內容渲染
    for _ in range(2):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(2)
    
    # 循環嘗試搜尋表格
    macros = []
    for i in range(5):
        macros = driver.find_elements(By.CSS_SELECTOR, "div.datasourceView-content-wrap, div[data-prosemirror-node-name='blockCard']")
        if macros:
            break
        print(f"第 {i+1} 次嘗試尋找表格中...")
        time.sleep(3)

    print(f"發現 {len(macros)} 個 Jira 表格")
    
    for i in range(len(macros)):
        try:
            current_macros = driver.find_elements(By.CSS_SELECTOR, "div.datasourceView-content-wrap, div[data-prosemirror-node-name='blockCard']")
            target = current_macros[i]
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", target)
            target.click()
            time.sleep(1.5)
            
            # 使用 JS 搜尋並點擊編輯按鈕
            driver.execute_script("""
                var btns = document.querySelectorAll('button');
                for(var b of btns) { 
                    if(b.innerText.includes('編輯') || b.innerText.includes('Edit')) { b.click(); break; } 
                }
            """)
            
            wait = WebDriverWait(driver, 20)
            jql_input = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-testid='jql-editor-input']")))
            old_jql = jql_input.text
            
            dates = re.findall(r"\d{4}-\d{1,2}-\d{1,2}", old_jql)
            if len(dates) >= 2:
                new_jql = old_jql.replace(dates[0], date_info['monday_str']).replace(dates[1], date_info['sunday_str'])
                jql_input.click()
                jql_input.send_keys(Keys.CONTROL + "a" + Keys.BACK_SPACE)
                jql_input.send_keys(new_jql + Keys.ENTER)
                time.sleep(3)
                driver.find_element(By.XPATH, "//button[contains(., 'Insert') or contains(., 'Save') or contains(., '插入')]").click()
                print(f"✅ 表格 {i+1} 更新完成")
            time.sleep(3)
        except Exception as e:
            print(f"❌ 表格 {i+1} 更新跳過: {str(e)}")
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()

def publish_page(driver):
    print("執行發佈流程...")
    driver.execute_script("window.scrollTo(0,0);")
    time.sleep(3)
    publish_js = """
    var found = false;
    document.querySelectorAll('button').forEach(btn => {
        if (btn.getAttribute('data-testid') === 'publish-button' || btn.innerText.includes('發佈') || btn.innerText.includes('Publish')) {
            btn.click();
            found = true;
        }
    });
    return found;
    """
    if driver.execute_script(publish_js):
        print("✅ 已點擊發佈按鈕")
        time.sleep(15) 
    else:
        print("❌ 找不到按鈕，嘗試快捷鍵發佈")
        webdriver.ActionChains(driver).key_down(Keys.CONTROL).send_keys(Keys.ENTER).key_up(Keys.CONTROL).perform()

def main():
    dates = get_target_dates()
    print(f"=== Confluence 自動週報 (v21.0 穩定強化版) ===")
    driver = init_driver()
    try:
        inject_cookies(driver)
        navigate_and_copy_latest(driver)
        rename_page_js_force(driver, dates['friday_filename'])
        update_jira_macros(driver, dates)
        publish_page(driver)
        print("=== 任務執行結束 ===")
    except Exception as e:
        print(f"發生嚴重錯誤: {str(e)}")
        driver.save_screenshot("v21_final_error.png")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
