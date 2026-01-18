import time
import os
import json
import gspread
from datetime import datetime, timedelta, timezone
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ================= 設定區 =================
# 試算表名稱與分頁設定
SPREADSHEET_FILE_NAME = 'Guardian_Price_Check'
WORKSHEET_MAIN = '成分表'       # 修正後的名稱
WORKSHEET_RESTRICT = '限制成分'   
COSING_URL = "https://ec.europa.eu/growth/tools-databases/cosing/index.cfm?fuseaction=search.simple"

# ================= 輔助功能 =================
def get_taiwan_time_display():
    """取得台灣時間顯示字串"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

def connect_google_sheet():
    """透過 GitHub Secrets 連線 Google Sheet"""
    print("📊 正在連線 Google Sheet (使用 Secrets)...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    # 讀取 GitHub Actions 中設定的 Secret
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 環境變數！")
        return None

    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ 解析金鑰或連線失敗: {e}")
        return None

def init_driver():
    """初始化 Chrome WebDriver (適配 GitHub Actions)"""
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

# ================= 核心邏輯 =================
def main():
    client = connect_google_sheet()
    if not client: return

    driver = init_driver()
    wait = WebDriverWait(driver, 25) # 提高等待時長應對網路波動

    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        main_sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
        restrict_sheet = spreadsheet.worksheet(WORKSHEET_RESTRICT)
        restrict_gid = restrict_sheet.id

        # 1. 初始化清理
        print(f"🧹 正在清理「{WORKSHEET_MAIN}」與「{WORKSHEET_RESTRICT}」舊資料...")
        main_sheet.batch_clear(["C2:E100"]) 
        restrict_sheet.batch_clear(["A2:G500"]) 

        # 2. 讀取搜尋清單 (從 B 欄讀取成分名稱)
        ingredients = main_sheet.col_values(2)[1:] 
        update_time = get_taiwan_time_display()
        current_restrict_row = 2 

        for i, name in enumerate(ingredients):
            row_idx = i + 2
            if not name or not str(name).strip(): continue

            search_name = str(name).strip()
            print(f"🔍 搜尋中 ({i+1}/{len(ingredients)}): {search_name}")
            
            driver.get(COSING_URL)
            
            try:
                # 定位搜尋框 (依據官方網站 ID: name)
                search_box = wait.until(EC.element_to_be_clickable((By.ID, "name")))
                search_box.clear()
                search_box.send_keys(search_name)
                search_box.send_keys(Keys.ENTER)
                
                # 等待載入結果
                time.sleep(5)

                if "No matching results found" in driver.page_source:
                    print(f"ℹ️ {search_name}: 無匹配結果")
                    main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                      values=[["No matching results found", "", update_time]])
                else:
                    # 擷取表格內容
                    rows = driver.find_elements(By.CSS_SELECTOR, "table.table tr")
                    content_rows = [r for r in rows if r.find_elements(By.TAG_NAME, "td")]

                    scraped_data = []
                    for r in content_rows:
                        cols = r.find_elements(By.TAG_NAME, "td")
                        if len(cols) >= 5:
                            scraped_data.append([
                                search_name,           # A: 原始成分
                                cols[0].text.strip(),  # B: Type
                                cols[1].text.strip(),  # C: INCI Name
                                cols[2].text.strip(),  # D: CAS No.
                                cols[3].text.strip(),  # E: EC No.
                                cols[4].text.strip()   # F: Annex/Ref
                            ])
                    
                    if scraped_data:
                        num_new_rows = len(scraped_data)
                        end_row = current_restrict_row + num_new_rows - 1
                        restrict_sheet.update(range_name=f"A{current_restrict_row}:F{end_row}", values=scraped_data)
                        
                        # 建立超連結公式回主表
                        link_formula = f'=HYPERLINK("#gid={restrict_gid}&range=A{current_restrict_row}", "{search_name}")'
                        main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                          values=[["Clicks with Link", link_formula, update_time]],
                                          value_input_option="USER_ENTERED")
                        
                        current_restrict_row += num_new_rows
                        print(f"✅ {search_name}: 抓取成功 ({num_new_rows} 筆資料)")
                    else:
                        main_sheet.update_acell(f"C{row_idx}", "No Data Found")

            except Exception as e:
                print(f"⚠️ 處理 {search_name} 時發生錯誤: {str(e)[:100]}")
                main_sheet.update_acell(f"C{row_idx}", "Timeout/Error")

        print("🎉 任務執行完畢")

    except Exception as main_e:
        print(f"💥 程式執行發生重大錯誤: {main_e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
