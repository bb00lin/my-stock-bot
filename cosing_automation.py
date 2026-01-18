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
SPREADSHEET_FILE_NAME = 'Guardian_Price_Check'
WORKSHEET_MAIN = '工作表1'       # 放置成分清單的分頁
WORKSHEET_RESTRICT = '限制成分'   # 放置爬取結果的分頁
COSING_URL = "https://ec.europa.eu/growth/tools-databases/cosing/index.cfm?fuseaction=search.simple"

# ================= 輔助功能 =================
def get_taiwan_time_display():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")

def connect_google_sheet():
    print("📊 正在連線 Google Sheet (使用 Secrets)...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
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
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return driver

# ================= 核心邏輯 =================
def main():
    client = connect_google_sheet()
    if not client: return

    driver = init_driver()
    wait = WebDriverWait(driver, 10)

    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        main_sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
        restrict_sheet = spreadsheet.worksheet(WORKSHEET_RESTRICT)
        restrict_gid = restrict_sheet.id  # 取得分頁 ID 用於建立超連結

        # 1. 初始化清理
        print("🧹 正在清理舊資料...")
        main_sheet.batch_clear(["C2:E100"]) # 清理主表結果、Link、Update
        restrict_sheet.batch_clear(["A2:G500"]) # 清理限制成分表

        # 2. 取得搜尋清單 (從 B 欄第 2 列開始)
        ingredients = main_sheet.col_values(2)[1:] 
        update_time = get_taiwan_time_display()
        
        current_restrict_row = 2 # 限制成分表從第 2 列開始寫入

        for i, name in enumerate(ingredients):
            row_idx = i + 2
            if not name.strip(): continue

            print(f"🔍 搜尋中 ({i+1}/{len(ingredients)}): {name}")
            driver.get(COSING_URL)
            
            try:
                # 輸入搜尋名稱
                search_box = wait.until(EC.presence_of_element_located((By.NAME, "name")))
                search_box.clear()
                search_box.send_keys(name)
                search_box.send_keys(Keys.ENTER)
                
                time.sleep(2) # 等待頁面跳轉

                # 檢查是否有結果
                if "No matching results found" in driver.page_source:
                    main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                      values=[["No matching results found", "", update_time]])
                else:
                    # 擷取表格資料 (排除 Header)
                    rows = driver.find_elements(By.CSS_SELECTOR, "table.table tbody tr")
                    if not rows: # 有些情況可能沒有 tbody 但有 tr
                        rows = driver.find_elements(By.CSS_SELECTOR, "table.table tr")[1:]

                    scraped_data = []
                    for r in rows:
                        cols = r.find_elements(By.TAG_NAME, "td")
                        if len(cols) >= 5:
                            # 格式: [Ingredients List, Type, INCI/Substance Name, CAS No., EC No., Annex/Ref]
                            # 這裡依照影片：A 欄填搜尋名, B 欄填 Type, C 欄填 INCI...
                            scraped_data.append([
                                name,
                                cols[0].text.strip(), # Type
                                cols[1].text.strip(), # INCI Name
                                cols[2].text.strip(), # CAS
                                cols[3].text.strip(), # EC
                                cols[4].text.strip()  # Annex
                            ])
                    
                    if scraped_data:
                        # 寫入「限制成分」分頁
                        num_new_rows = len(scraped_data)
                        end_row = current_restrict_row + num_new_rows - 1
                        restrict_sheet.update(range_name=f"A{current_restrict_row}:F{end_row}", values=scraped_data)
                        
                        # 在「工作表1」建立超連結，指向「限制成分」對應的起始列
                        # 格式: =HYPERLINK("#gid=分頁ID&range=A列號", "顯示名稱")
                        link_formula = f'=HYPERLINK("#gid={restrict_gid}&range=A{current_restrict_row}", "Mica")'
                        
                        main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                          values=[["Clicks with Link", link_formula, update_time]],
                                          value_input_option="USER_ENTERED")
                        
                        current_restrict_row += num_new_rows

            except Exception as e:
                print(f"⚠️ 處理 {name} 時發生錯誤: {e}")
                main_sheet.update_acell(f"C{row_idx}", "Error")

        print("🎉 所有成分處理完成！")

    except Exception as main_e:
        print(f"💥 程式執行發生重大錯誤: {main_e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
