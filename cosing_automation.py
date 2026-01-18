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
from selenium.common.exceptions import TimeoutException

# ================= 設定區 =================
SPREADSHEET_FILE_NAME = 'Guardian_Price_Check'
WORKSHEET_MAIN = '成分表'       # 主表名稱
WORKSHEET_RESTRICT = '限制成分'   # 詳細資料分頁
COSING_URL = "https://ec.europa.eu/growth/tools-databases/cosing/index.cfm?fuseaction=search.simple"

# ================= 輔助功能 =================
def get_taiwan_time_display():
    """取得台灣標準時間格式 (例如: 2026-01-18 22:24)"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

def connect_google_sheet():
    """連線至 Google Sheet"""
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
    """初始化 Chrome 驅動程式 (Headless 模式)"""
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
    wait = WebDriverWait(driver, 30)

    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        main_sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
        restrict_sheet = spreadsheet.worksheet(WORKSHEET_RESTRICT)
        restrict_gid = restrict_sheet.id

        # 1. 清理舊資料 (主表清理 C2:E100, 限制成分清理 A2:G1000)
        print(f"🧹 正在清理舊資料...")
        main_sheet.batch_clear(["C2:E100"]) 
        restrict_sheet.batch_clear(["A2:G1000"]) 

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
                # 填入搜尋關鍵字並點擊
                search_box = wait.until(EC.element_to_be_clickable((By.ID, "keyword")))
                search_box.clear()
                search_box.send_keys(search_name)
                
                search_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'].ecl-button--primary")
                driver.execute_script("arguments[0].click();", search_btn)
                
                # 等待結果載入
                try:
                    wait.until(lambda d: "No matching results found" in d.page_source or 
                                       len(d.find_elements(By.TAG_NAME, "table")) > 0)
                except TimeoutException:
                    pass

                if "No matching results found" in driver.page_source:
                    print(f"ℹ️ {search_name}: 無匹配結果。")
                    main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                      values=[["No matching results found", "", update_time]])
                else:
                    # 搜尋所有表格抓取資料
                    tables = driver.find_elements(By.TAG_NAME, "table")
                    scraped_batch = []
                    
                    for table in tables:
                        rows = table.find_elements(By.TAG_NAME, "tr")
                        for r in rows:
                            cols = r.find_elements(By.TAG_NAME, "td")
                            # 依照截圖，資料列結構需對應：A:搜尋名, B:更新日期, C:Type, D:INCI, E:CAS, F:EC, G:Annex
                            if len(cols) >= 5:
                                scraped_batch.append([
                                    search_name,           # A 欄
                                    update_time,           # B 欄 (修正: 填入更新日期)
                                    cols[0].text.strip(),  # C 欄
                                    cols[1].text.strip(),  # D 欄
                                    cols[2].text.strip(),  # E 欄
                                    cols[3].text.strip(),  # F 欄
                                    cols[4].text.strip()   # G 欄
                                ])
                    
                    if scraped_batch:
                        num_rows = len(scraped_batch)
                        end_range = current_restrict_row + num_rows - 1
                        # 寫入限制成分分頁 (A 至 G 欄)
                        restrict_sheet.update(range_name=f"A{current_restrict_row}:G{end_range}", values=scraped_batch)
                        
                        # 在「成分表」建立超連結 (指向限制成分對應的第一列)
                        link_formula = f'=HYPERLINK("#gid={restrict_gid}&range=A{current_restrict_row}", "{search_name}")'
                        main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                          values=[["Clicks with Link", link_formula, update_time]],
                                          value_input_option="USER_ENTERED")
                        
                        current_restrict_row += num_rows
                        print(f"✅ {search_name}: 抓取完成。")
                    else:
                        print(f"⚠️ {search_name}: 無法解析表格結構。")
                        main_sheet.update_acell(f"C{row_idx}", "Format Error")

            except TimeoutException:
                print(f"❌ {search_name}: 搜尋頁面載入逾時。")
                main_sheet.update_acell(f"C{row_idx}", "Timeout/Error")
            except Exception as e:
                print(f"❌ {search_name}: 發生錯誤 - {str(e)[:50]}")
                main_sheet.update_acell(f"C{row_idx}", "Runtime Error")

        print("🎉 任務執行結束")

    except Exception as main_e:
        print(f"💥 程式重大崩潰: {main_e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
