import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import re

class PokemonCenterScraper:
    def __init__(self):
        self.base_url = "https://www.pokemoncenter-online.com"
        # 模擬真實瀏覽器的 Header，避免被視為機器人
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,zh-TW;q=0.7,zh;q=0.6",
            "Referer": "https://www.pokemoncenter-online.com/",
            "Connection": "keep-alive"
        }
        self.all_data = []

    def get_product_ids(self, max_pages=1):
        """第一步：從列表頁抓取所有商品編號 (p_cd)"""
        product_ids = []
        for page in range(1, max_pages + 1):
            list_url = f"{self.base_url}/?main_page=product_list&page={page}&sort=new"
            print(f"正在讀取第 {page} 頁列表...")
            
            try:
                response = requests.get(list_url, headers=self.headers, timeout=15)
                if response.status_code != 200:
                    print(f"無法讀取列表，狀態碼: {response.status_code}")
                    break
                
                soup = BeautifulSoup(response.text, 'html.parser')
                # 尋找所有包含 p_cd= 的商品連結
                links = soup.find_all('a', href=re.compile(r'p_cd='))
                
                for link in links:
                    href = link.get('href')
                    match = re.search(r'p_cd=([0-9]+)', href)
                    if match:
                        p_cd = match.group(1)
                        if p_cd not in product_ids:
                            product_ids.append(p_cd)
                
                print(f"目前累計找到 {len(product_ids)} 個商品編號")
                time.sleep(random.uniform(2, 4)) # 隨機停頓
            except Exception as e:
                print(f"抓取列表時出錯: {e}")
                
        return product_ids

    def get_product_details(self, p_cd):
        """第二步：進入商品詳細頁，抓取你要求的欄位"""
        url = f"{self.base_url}/?p_cd={p_cd}"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code != 200:
                return None
            
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 初始化該商品資料
            item = {
                "商品名稱": soup.select_one('h1').text.strip() if soup.select_one('h1') else "N/A",
                "商品網址": url,
                "價格": soup.select_one('.price').text.strip() if soup.select_one('.price') else "N/A"
            }

            # 抓取規格表 (common_table)
            table = soup.find('table', class_='common_table')
            if table:
                rows = table.find_all('tr')
                for row in rows:
                    th = row.find('th')
                    td = row.find('td')
                    if th and td:
                        key = th.text.strip()
                        value = td.get_text(separator="\n").strip() # 保留換行
                        item[key] = value
            
            return item
        except Exception as e:
            print(f"抓取商品 {p_cd} 詳細資訊時失敗: {e}")
            return None

    def start(self, pages=1):
        # 1. 拿 ID
        ids = self.get_product_ids(max_pages=pages)
        if not ids:
            print("未找到任何商品，請檢查網路或是否被網站屏蔽。")
            return

        # 2. 拿細節
        print(f"開始爬取 {len(ids)} 個商品的詳細資料...")
        for i, p_cd in enumerate(ids):
            detail = self.get_product_details(p_cd)
            if detail:
                self.all_data.append(detail)
                print(f"[{i+1}/{len(ids)}] 成功: {detail['商品名稱'][:15]}...")
            
            # 這是為了不被封鎖，一定要留間隔
            time.sleep(random.uniform(3, 5))
            
            # 每 10 筆存一次檔備份
            if (i + 1) % 10 == 0:
                self.save_to_excel("backup_pokemon.xlsx")

        # 3. 存檔
        self.save_to_excel("pokemon_products_full.xlsx")
        print("任務全部完成！")

    def save_to_excel(self, filename):
        df = pd.DataFrame(self.all_data)
        df.to_excel(filename, index=False)
        print(f"資料已儲存至 {filename}")

if __name__ == "__main__":
    scraper = PokemonCenterScraper()
    # 這裡設定要抓幾頁列表，建議先設為 1 測試，沒問題再改成 10 或更多
    scraper.start(pages=1)
