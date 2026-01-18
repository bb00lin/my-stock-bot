import time
import datetime
import gspread
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from webdriver_manager.chrome import ChromeDriverManager

# --- 設定區 ---
SHEET_NAME = "Guardian_Price_Check"  # 試算表名稱
CREDENTIALS_FILE = "credentials.json" # 金鑰檔名
COSING_URL = "https://ec.europa.eu/growth/tools-databases/cosing/index.cfm?fuseaction=search.simple"

# --- 初始化 Google Sheets ---
scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
client = gspread.authorize(creds)
spreadsheet = client.open(SHEET_NAME)
main_sheet = spreadsheet.worksheet("工作表1") # 請確認分頁名稱
restrict_sheet = spreadsheet.worksheet("限制成分")

# --- 初始化 Selenium (適合 GitHub Actions 的設定) ---
chrome_options = Options()
chrome_options.add_argument("--headless") # 無頭模式
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def run_automation():
    # 1. 初始化清理
    print("正在清理舊資料...")
    # 清空工作表1的 C, D, E 欄 (從第2列開始)
    main_sheet.batch_clear(["C2:E100"])
    # 清空限制成分分頁 (保留標題)
    restrict_sheet.batch_clear(["A2:G500"])

    # 2. 讀取成分列表 (B欄)
    ingredients = main_sheet.col_values(2)[1:] # 跳過標題列
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    current_restrict_row = 2

    for i, name in enumerate(ingredients):
        row_num = i + 2
        print(f"正在搜尋: {name}...")
        
        driver.get(COSING_URL)
        time.sleep(2)
        
        try:
            # 找到輸入框並搜尋
            search_input = driver.find_element(By.NAME, "search_simple") # 根據網站實際 ID/Name 調整
            search_input.clear()
            search_input.send_keys(name)
            search_input.send_keys(Keys.ENTER)
            time.sleep(3)
            
            # 檢查是否有結果
            if "No matching results found" in driver.page_source:
                main_sheet.update(range_name=f"C{row_num}:E{row_num}", values=[["No matching results found", "", today]])
            else:
                # 3. 擷取資料
                # 假設抓取結果表格的第一個表格列
                rows = driver.find_elements(By.CSS_SELECTOR, "table.table tr")[1:] # 跳過表頭
                
                results_data = []
                for r in rows:
                    cols = r.find_elements(By.TAG_NAME, "td")
                    if len(cols) >= 5:
                        data_row = [
                            name, # A: 搜尋名稱
                            today, # B: 更新日期
                            cols[0].text, # Type
                            cols[1].text, # INCI Name
                            cols[2].text, # CAS No.
                            cols[3].text, # EC No.
                            cols[4].text  # Annex/Ref
                        ]
                        results_data.append(data_row)
                
                if results_data:
                    # 更新限制成分表
                    num_results = len(results_data)
                    restrict_sheet.update(range_name=f"A{current_restrict_row}:G{current_restrict_row + num_results - 1}", values=results_data)
                    
                    # 更新主表，建立超連結指向限制成分表的對應行
                    link_formula = f'=HYPERLINK("#gid={restrict_sheet.id}&range=A{current_restrict_row}", "View Details")'
                    main_sheet.update(range_name=f"C{row_num}:E{row_num}", values=[["Clicks with Link", link_formula, today]], value_input_option="USER_ENTERED")
                    
                    current_restrict_row += num_results
                    
        except Exception as e:
            print(f"處理 {name} 時發生錯誤: {e}")
            main_sheet.update_acell(f"C{row_num}", "Error")

    driver.quit()
    print("任務完成！")

if __name__ == "__main__":
    run_automation()
