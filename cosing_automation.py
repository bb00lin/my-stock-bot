import time
import os
import json
import shutil
import zipfile
import smtplib
import gspread
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
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
WORKSHEET_MAIN = '成分表'
WORKSHEET_RESTRICT = '限制成分'
# 請填入您的 Google Sheet 網址，用於郵件內容
SHEET_URL = "https://docs.google.com/spreadsheets/d/1pqa6DU-qo3lR84QYgpoiwGE7tO-QSY2-kC_ecf868cY/edit"
COSING_URL = "https://ec.europa.eu/growth/tools-databases/cosing/index.cfm?fuseaction=search.simple"

# Email 設定
MAIL_RECEIVERS = ['bb00lin@gmail.com', 'helen.chen.168@gmail.com']

# ================= 輔助功能 =================
def get_taiwan_time_now():
    return datetime.now(timezone(timedelta(hours=8)))

def get_time_str_for_filename():
    return get_taiwan_time_now().strftime("%Y-%m-%d_%H-%M")

def get_display_time():
    return get_taiwan_time_now().strftime("%Y-%m-%d %H:%M")

def connect_google_sheet():
    print("📊 正在連線 Google Sheet...")
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    json_key_str = os.environ.get('GOOGLE_SHEETS_JSON')
    if not json_key_str:
        print("❌ 錯誤：找不到 GOOGLE_SHEETS_JSON 環境變數")
        return None
    try:
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(json_key_str), scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        print(f"❌ 連線失敗: {e}")
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

def send_email(subject, body, attachment_path=None):
    mail_user = os.environ.get('MAIL_USERNAME')
    mail_pass = os.environ.get('MAIL_PASSWORD')
    
    if not mail_user or not mail_pass:
        print("⚠️ 未設定 Email 帳密，跳過寄信")
        return

    print(f"📧 正在發送郵件: {subject}")
    msg = MIMEMultipart()
    msg['From'] = mail_user
    msg['To'] = ", ".join(MAIL_RECEIVERS)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))

    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, 'rb') as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            part['Content-Disposition'] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        except Exception as e:
            print(f"⚠️ 附件夾帶失敗: {e}")

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(mail_user, mail_pass)
        server.send_message(msg)
        server.quit()
        print("✅ 郵件發送成功")
    except Exception as e:
        print(f"❌ 郵件發送失敗: {e}")

# ================= 核心邏輯 =================
def main():
    client = connect_google_sheet()
    if not client: return

    driver = init_driver()
    wait = WebDriverWait(driver, 30)

    # 建立截圖暫存資料夾
    screenshot_dir = "screenshots"
    if os.path.exists(screenshot_dir): shutil.rmtree(screenshot_dir)
    os.makedirs(screenshot_dir)

    try:
        spreadsheet = client.open(SPREADSHEET_FILE_NAME)
        main_sheet = spreadsheet.worksheet(WORKSHEET_MAIN)
        restrict_sheet = spreadsheet.worksheet(WORKSHEET_RESTRICT)
        restrict_sheet_id = restrict_sheet.id
        restrict_gid = restrict_sheet.id

        print(f"🧹 清理舊資料...")
        main_sheet.batch_clear(["C2:E100"]) 
        restrict_sheet.batch_clear(["A2:G1000"]) 

        ingredients = main_sheet.col_values(2)[1:] 
        current_restrict_row = 2 
        
        # 統計數據
        total_checked = 0
        total_restricted = 0 # 只要有找到資料就算有限制/規範
        found_list = [] # 紀錄找到的成分名稱
        
        # 上色請求列表
        formatting_requests = []
        is_yellow_bg = True # 起始顏色控制 (True=黃, False=白)

        for i, name in enumerate(ingredients):
            row_idx = i + 2
            if not name or not str(name).strip(): continue
            
            clean_name = str(name).strip()
            total_checked += 1
            print(f"🔍 [{i+1}] 搜尋: {clean_name}")
            
            driver.get(COSING_URL)
            update_time = get_display_time()
            
            try:
                # 搜尋動作
                search_box = wait.until(EC.element_to_be_clickable((By.ID, "keyword")))
                search_box.clear()
                search_box.send_keys(clean_name)
                
                search_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit'].ecl-button--primary")
                driver.execute_script("arguments[0].click();", search_btn)
                
                # 等待結果
                try:
                    wait.until(lambda d: "No matching results found" in d.page_source or 
                                       len(d.find_elements(By.TAG_NAME, "table")) > 0)
                except TimeoutException: pass

                # === 截圖 ===
                safe_filename = "".join([c for c in clean_name if c.isalpha() or c.isdigit() or c==' ']).strip()
                screenshot_path = f"{screenshot_dir}/{safe_filename}.png"
                driver.save_screenshot(screenshot_path)

                if "No matching results found" in driver.page_source:
                    print(f"ℹ️ {clean_name}: 無結果")
                    main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                      values=[["No matching results found", "", update_time]])
                else:
                    # 抓取表格
                    tables = driver.find_elements(By.TAG_NAME, "table")
                    scraped_batch = []
                    
                    for table in tables:
                        rows = table.find_elements(By.TAG_NAME, "tr")
                        for r in rows:
                            cols = r.find_elements(By.TAG_NAME, "td")
                            if len(cols) >= 5:
                                # [重要修復] 使用 textContent 解決 Type 欄位空白問題
                                type_text = cols[0].get_attribute("textContent").strip()
                                
                                scraped_batch.append([
                                    clean_name,           # A
                                    update_time,          # B
                                    type_text,            # C (Type)
                                    cols[1].text.strip(), # D (INCI)
                                    cols[2].text.strip(), # E (CAS)
                                    cols[3].text.strip(), # F (EC)
                                    cols[4].text.strip()  # G (Annex)
                                ])
                    
                    if scraped_batch:
                        total_restricted += 1
                        found_list.append(clean_name)
                        
                        num_rows = len(scraped_batch)
                        end_range = current_restrict_row + num_rows - 1
                        restrict_sheet.update(range_name=f"A{current_restrict_row}:G{end_range}", values=scraped_batch)
                        
                        # 連結回主表
                        link_val = f'=HYPERLINK("#gid={restrict_gid}&range=A{current_restrict_row}", "{clean_name}")'
                        main_sheet.update(range_name=f"C{row_idx}:E{row_idx}", 
                                          values=[["Clicks with Link", link_val, update_time]],
                                          value_input_option="USER_ENTERED")
                        
                        # === 準備上色指令 (Batch Update) ===
                        # 定義顏色：黃色 (1, 1, 0) 或 白色 (1, 1, 1)
                        bg_color = {"red": 1, "green": 1, "blue": 0} if is_yellow_bg else {"red": 1, "green": 1, "blue": 1}
                        
                        formatting_requests.append({
                            "repeatCell": {
                                "range": {
                                    "sheetId": restrict_sheet_id,
                                    "startRowIndex": current_restrict_row - 1, # API 是 0-based index
                                    "endRowIndex": end_range,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": 7
                                },
                                "cell": {
                                    "userEnteredFormat": {
                                        "backgroundColor": bg_color
                                    }
                                },
                                "fields": "userEnteredFormat.backgroundColor"
                            }
                        })
                        
                        # 切換下一次的顏色
                        is_yellow_bg = not is_yellow_bg
                        current_restrict_row += num_rows
                        print(f"✅ {clean_name}: 抓取 {num_rows} 筆")
                    else:
                        main_sheet.update_acell(f"C{row_idx}", "Format Error")

            except Exception as e:
                print(f"❌ {clean_name} 錯誤: {str(e)[:50]}")
                main_sheet.update_acell(f"C{row_idx}", "Error")

        # 3. 執行批次上色 (如果有的話)
        if formatting_requests:
            print("🎨 正在執行表格上色...")
            spreadsheet.batch_update({"requests": formatting_requests})

        # 4. 打包截圖
        zip_filename = f"Search_{get_time_str_for_filename()}.zip"
        print(f"📦 正在打包截圖: {zip_filename}")
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(screenshot_dir):
                for file in files:
                    zipf.write(os.path.join(root, file), file)

        # 5. 發送郵件
        subject = f"限制成分查詢結果-查詢{total_checked}種成分有{total_restricted}個種成分限制"
        
        # 產生摘要 HTML
        found_html_list = "".join([f"<li>{item}</li>" for item in found_list])
        body = f"""
        <html><body>
            <h2>限制成分自動查詢報告</h2>
            <p><b>執行時間:</b> {get_display_time()}</p>
            <p><b>統計結果:</b></p>
            <ul>
                <li>總共查詢成分數: {total_checked}</li>
                <li>發現限制/規範成分數: {total_restricted}</li>
            </ul>
            <p><b>有限制的成分清單:</b></p>
            <ul>{found_html_list}</ul>
            <br>
            <p>👉 <a href="{SHEET_URL}">點擊查看完整 Google Sheet 報表</a></p>
            <p><i>截圖檔案請參閱附件。</i></p>
        </body></html>
        """
        
        send_email(subject, body, zip_filename)

        print("🎉 所有任務完成！")

    except Exception as main_e:
        print(f"💥 程式崩潰: {main_e}")
    finally:
        driver.quit()
        # 清理暫存
        if os.path.exists("screenshots"): shutil.rmtree("screenshots")

if __name__ == "__main__":
    main()
