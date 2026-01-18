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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ================= 設定區 =================
SPREADSHEET_FILE_NAME = 'Guardian_Price_Check'
WORKSHEET_MAIN = '成分表'       # 確保與試算表分頁名稱完全一致
WORKSHEET_RESTRICT = '限制成分'   
COSING_URL = "https://ec.europa.eu/growth/tools-databases/cosing/index.cfm?fuseaction=search.simple"

# ================= 輔助功能 =================
def get_taiwan_time_display():
    """取得台灣標準時間格式"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

def connect_google_sheet():
    """連線至 Google Sheet 並回傳 client"""
    print("📊 正在嘗試連線 Google Sheet (使用 Secrets)...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 環境變數。")
        return None

    try:
        creds_dict = json.loads(json_key_str)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ 解析金鑰或連線試算表失敗: {e}")
        return None

def init_driver():
    """初始化適合 GitHub Actions 環境的 Chrome 驅動程式"""
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')  # 強制無頭模式
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
    # 設置顯性等待時間 30 秒
    wait = WebDriverWait(driver, 30)

    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        main_sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
        restrict_sheet = spreadsheet.worksheet(WORKSHEET_RESTRICT)
        restrict_gid = restrict_sheet.id

        # 1. 初始化清理
        print(f"🧹 正在清理「{WORKSHEET_MAIN}」結果欄位與「{WORKSHEET_RESTRICT}」內容...")
        main_sheet.batch_clear(["C2:E100"]) 
        restrict_sheet.batch_clear(["A2:G1000"]) 

        # 2. 讀取待搜尋成分 (B 欄)
        ingredients = main_sheet.col_values(2)[1:] 
        update_time = get_taiwan_time_display()
        current_restrict_row = 2 

        for i, name in enumerate(ingredients):
            row_idx = i + 2
            if not name or not str(name).strip(): continue

            clean_name = str(name).strip()
            print(f"🔍 搜尋中 ({i+1}/{len(ingredients)}): {clean_name}")
            
            driver.get(COSING_URL)
            
            try:
                # 使用您提供的 HTML 代碼中的 ID 定位搜尋框
                search_box = wait.until(EC.element_to_be_clickable((By.ID, "keyword")))
                search_box.clear()
                search_box.send_keys(clean_name)
                
                # 使用您提供的 HTML 代碼中的選取器定位搜尋按鈕並點擊
                search_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'].ecl-button--primary")
                driver.execute_script("arguments[0].click();", search_btn)
                
                # 給予網頁處理時間
                time.sleep(5)

                # 判斷結果
                if "No matching results found" in driver.page_source:
                    print(f"ℹ️ {clean_name}: 無匹配結果。")
                    main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                      values=[["No matching results found", "", update_time]])
                else:
                    # 抓取表格
                    rows = driver.find_elements(By.CSS_SELECTOR, "table.table tr")
                    actual_data_rows = [r for r in rows if r.find_elements(By.TAG_NAME, "td")]

                    scraped_batch = []
                    for r in actual_data_rows:
                        cols = r.find_elements(By.TAG_NAME, "td")
                        if len(cols) >= 5:
                            scraped_batch.append([
                                clean_name,            # A: 原始搜尋名稱
                                cols[0].text.strip(),  # B: Type
                                cols[1].text.strip(),  # C: INCI Name
                                cols[2].text.strip(),  # D: CAS No.
                                cols[3].text.strip(),  # E: EC No.
                                cols[4].text.strip()   # F: Annex/Ref
                            ])
                    
                    if scraped_batch:
                        # 批量寫入「限制成分」
                        num_rows = len(scraped_batch)
                        end_range = current_restrict_row + num_rows - 1
                        restrict_sheet.update(range_name=f"A{current_restrict_row}:F{end_range}", values=scraped_batch)
                        
                        # 在成分表建立超連結
                        link_val = f'=HYPERLINK("#gid={restrict_gid}&range=A{current_restrict_row}", "{clean_name}")'
                        main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                          values=[["Clicks with Link", link_val, update_time]],
                                          value_input_option="USER_ENTERED")
                        
                        current_restrict_row += num_rows
                        print(f"✅ {clean_name}: 抓取成功。")
                    else:
                        main_sheet.update_acell(f"C{row_idx}", "Format Error")

            except TimeoutException:
                print(f"❌ {clean_name}: 頁面載入逾時。")
                main_sheet.update_acell(f"C{row_idx}", "Timeout/Error")
            except Exception as e:
                print(f"❌ {clean_name}: 執行出錯 - {str(e)[:50]}")
                main_sheet.update_acell(f"C{row_idx}", "Runtime Error")

        print("🎉 任務執行完畢")

    except Exception as main_e:
        print(f"💥 重大錯誤: {main_e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
