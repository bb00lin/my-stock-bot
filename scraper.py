import requests
from bs4 import BeautifulSoup
import time
import pandas as pd
import random

class PokemonCenterScraper:
    def __init__(self):
        self.base_url = "https://www.pokemoncenter-online.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        self.results = []

    def get_product_info(self, product_id):
        """抓取單一商品頁面的詳細資訊"""
        url = f"{self.base_url}/?p_cd={product_id}"
        print(f"正在抓取商品: {product_id}...")
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                print(f"無法存取: {url}, 狀態碼: {response.status_code}")
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 初始化數據字典
            data = {
                "商品名稱": soup.select_one('h1').text.strip() if soup.select_one('h1') else "N/A",
                "商品網址": url,
                "價格": soup.select_one('.price').text.strip() if soup.select_one('.price') else "N/A"
            }

            # 抓取詳細規格表 (通常在 class="product_detail" 或 table 內)
            # 根據你提供的結構，資訊通常在 .detail_txt 或者是特定的 table 中
            table = soup.find('table', class_='common_table') # 這是該網站常見的規格表 class
            if table:
                rows = table.find_all('tr')
                for row in rows:
                    th = row.find('th')
                    td = row.find('td')
                    if th and td:
                        key = th.text.strip()
                        value = td.text.strip().replace('\n', ' ')
                        data[key] = value
            
            return data

        except Exception as e:
            print(f"抓取 {product_id} 時發生錯誤: {e}")
            return None

    def get_all_product_ids(self, max_pages=5):
        """
        從新著商品或分類頁獲取商品 ID 列表
        這只是一個示例，實際需要遍歷所有分頁
        """
        product_ids = []
        for page in range(1, max_pages + 1):
            # 範例：從新到貨頁面抓取
            list_url = f"{self.base_url}/?main_page=product_list&sort=new&page={page}"
            res = requests.get(list_url, headers=self.headers)
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 尋找商品連結，提取 p_cd
            links = soup.select('a[href*="p_cd="]')
            for link in links:
                href = link.get('href')
                # 提取 p_cd= 後面的數字
                p_cd = href.split('p_cd=')[-1].split('&')[0]
                if p_cd not in product_ids:
                    product_ids.append(p_cd)
            
            print(f"已獲取第 {page} 頁，目前共有 {len(product_ids)} 個商品 ID")
            time.sleep(random.uniform(1, 3)) # 隨機延遲預防封鎖
            
        return product_ids

    def run(self):
        # 1. 獲取商品 ID
        ids = self.get_all_product_ids(max_pages=2) # 測試抓取前 2 頁
        
        # 2. 循環抓取詳細資訊
        for p_id in ids:
            info = self.get_product_info(p_id)
            if info:
                self.results.append(info)
            time.sleep(random.uniform(2, 4)) # 每個商品間隔 2-4 秒
        
        # 3. 儲存為 Excel 或 CSV
        df = pd.DataFrame(self.results)
        df.to_excel("pokemon_center_products.xlsx", index=False)
        print("抓取完成，資料已儲存至 pokemon_center_products.xlsx")

if __name__ == "__main__":
    scraper = PokemonCenterScraper()
    scraper.run()
