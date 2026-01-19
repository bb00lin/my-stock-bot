from atlassian import Confluence
from bs4 import BeautifulSoup, Tag
import re
import os

# --- 1. 設定與連線 ---
url = 'YOUR_CONFLUENCE_URL'
username = 'YOUR_USERNAME'
api_token = 'YOUR_API_TOKEN'
page_id = 'YOUR_PAGE_ID'

confluence = Confluence(url=url, username=username, password=api_token)

# --- 2. 定義切割邏輯 (沿用之前的 Regex) ---
def split_content(text, n=5):
    # 這裡加入簡單的 HTML 轉換，因為讀下來的可能是 HTML
    # 如果讀取的是純文字，則用之前的邏輯
    text = text.replace('<br/>', '\n').replace('<p>', '').replace('</p>', '\n')
    
    date_pattern = re.compile(r'\[\d{4}/\d{2}/\d{2}\]') # 簡化版 regex
    matches = list(date_pattern.finditer(text))
    
    if len(matches) > n:
        cutoff_index = matches[n].start()
        kept_data = text[:cutoff_index].strip()
        moved_data = text[cutoff_index:].strip()
        return kept_data, moved_data
    return text, None

# --- 3. 建立 History Expand 巨集的 XML 結構 (如果不存在時使用) ---
def create_history_macro(soup, content_html):
    # 建立外層 expand macro
    macro = soup.new_tag('ac:structured-macro', attrs={"ac:name": "expand"})
    
    # 設定標題 parameter
    param_title = soup.new_tag('ac:parameter', attrs={"ac:name": "title"})
    param_title.string = "history"
    macro.append(param_title)
    
    # 建立 rich-text-body
    body = soup.new_tag('ac:rich-text-body')
    
    # 建立內部表格 (依照你的截圖風格)
    table = soup.new_tag('table')
    tbody = soup.new_tag('tbody')
    
    # 建立新的一列來放資料
    new_row = soup.new_tag('tr')
    # 假設第一欄是 Item (空白或填入 'Archive')
    td_item = soup.new_tag('td') 
    td_item.string = "Archived"
    
    # 第二欄是 Update (放入搬移過來的資料)
    td_update = soup.new_tag('td')
    td_update.append(BeautifulSoup(content_html, 'html.parser')) # 解析 HTML 字串放入
    
    new_row.append(td_item)
    new_row.append(td_update)
    tbody.append(new_row)
    table.append(tbody)
    
    body.append(table)
    macro.append(body)
    
    return macro

# --- 主程式 ---
# 1. 取得頁面內容 (Storage Format)
page = confluence.get_page_by_id(page_id, expand='body.storage')
soup = BeautifulSoup(page['body']['storage']['value'], 'html.parser')

# 2. 定位主要表格的 Update 欄位
# 注意：這裡需要根據你的實際表格結構定位，例如找含有 "Design : Schematic" 的列
target_row = None
rows = soup.find_all('tr')
for row in rows:
    # 這裡假設你的目標列第一格含有 "Design : Schematic" 字樣
    if "Design : Schematic" in row.text: 
        target_row = row
        break

if target_row:
    # 取得 Update 欄位 (通常是最後一欄或第二欄)
    update_cell = target_row.find_all('td')[-1] 
    original_html = str(update_cell) # 這是含 HTML 標籤的原始碼
    
    # 簡單轉成文字做切割 (實作上建議保留 HTML tag 比較安全，這裡簡化演示)
    # *實戰建議：不要轉純文字，而是用 soup 遍歷子節點計算日期數量*
    # 這裡假設已取得切割後的 "moved_html_content"
    
    # [模擬切割結果]
    kept_html = "" 
    moved_html = "<p>[2025/11/14] Old Data...</p>" 
    has_overflow = True # 假設有資料要搬

    if has_overflow:
        # A. 更新主要欄位
        update_cell.clear()
        update_cell.append(BeautifulSoup(kept_html, 'html.parser'))

        # B. 處理 History Expand 區塊
        # 尋找現有的 history expand macro
        history_macro = None
        all_macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "expand"})
        
        for m in all_macros:
            title_param = m.find('ac:parameter', attrs={"ac:name": "title"})
            if title_param and "history" in title_param.text.lower():
                history_macro = m
                break
        
        if history_macro:
            print("找到現有的 History 區塊，正在插入...")
            # 找到裡面的表格
            hist_table = history_macro.find('table')
            if not hist_table:
                # 如果有 expand 但沒表格，建一個
                hist_table = soup.new_tag('table')
                history_macro.find('ac:rich-text-body').append(hist_table)
            
            # 插入新列 (prepend 到最上面還是 append 到最下面看需求，通常 history 是堆疊)
            new_tr = soup.new_tag('tr')
            new_td1 = soup.new_tag('td')
            new_td1.string = "Archived"
            new_td2 = soup.new_tag('td')
            new_td2.append(BeautifulSoup(moved_html, 'html.parser'))
            
            new_tr.append(new_td1)
            new_tr.append(new_td2)
            
            # 插入表格最上方 (保留標題列之後)
            if hist_table.find('tbody'):
                hist_table.find('tbody').insert(0, new_tr)
            else:
                hist_table.append(new_tr)
                
        else:
            print("找不到 History 區塊，正在新建...")
            # 建立新的 macro 並插入到主要表格的下方
            new_macro = create_history_macro(soup, moved_html)
            
            # 插入位置：在主要表格 (target_row 的 parent table) 之後
            main_table = target_row.find_parent('table')
            main_table.insert_after(new_macro)

        # 4. 更新頁面
        confluence.update_page(
            page_id=page_id,
            title=page['title'],
            body=str(soup),
            type='page'
        )
        print("更新完成！")
