import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import re

class PokemonCenterScraper:
    def __init__(self):
        self.base_url = "https://www.pokemoncenter-online.com"
        # 更加完整的瀏覽器偽裝
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
            "Referer": "https://www.pokemoncenter-online.com/",
            "Upgrade-Insecure-Requests": "1"
        }
        self.all_data = []

    def get_product_ids(self, max_pages=1):
        product_ids = []
        for page in range(1, max_pages + 1):
            # 使用新著商品清單頁面
            list_url = f"{self.base_url}/?main_page=product_list&page={page}&sort=new"
            print(f"正在嘗試連線至列表頁: {list_url}")
            
            try:
                response = requests.get(list_url, headers=self.headers, timeout=15)
                print(f"網頁回應狀態碼: {response.status_code}")
                
                if response.status_code == 403:
                    print("🚫 錯誤：GitHub 的 IP 已被寶可夢官網屏蔽 (403 Forbidden)。")
                    print("建議改在『個人電腦』上執行此腳本。")
                    break
                
                soup = BeautifulSoup(response.text, 'html.parser')
                # 同時搜尋兩種連結格式：
                # 1. ?p_cd=4521329...
                # 2. /4521329....html
                links = soup.find_all('a', href=True)
                for link in links:
                    href = link.get('href')
                    # 檢查是否為商品頁連結 (通常是 13 位數字)
                    match = re.search(r'(\d{13})', href)
                    if match:
                        p_cd = match.group(1)
                        if p_cd not in product_ids:
                            product_ids.append(p_cd)
                
                print(f"第 {page} 頁解析完成，目前找到 {len(product_ids)} 個商品 ID")
                time.sleep(random.uniform(2, 4))
                
            except Exception as e:
                print(f"連線發生錯誤: {e}")
                
        return product_ids

    def get_product_details(self, p_cd):
        """抓取詳細資訊表格"""
        url = f"{self.base_url}/?p_cd={p_cd}"
        try:
            res = requests.get(url, headers=self.headers, timeout=15)
            if res.status_code != 200: return None
            
            soup = BeautifulSoup(res.text, 'html.parser')
            item = {
                "商品名稱": soup.select_one('h1').get_text(strip=True) if soup.select_one('h1') else "N/A",
                "商品編號": p_cd,
                "網址": url
            }

            # 解析規格表格
            table = soup.find('table', class_='common_table')
            if table:
                for row in table.find_all('tr'):
                    th = row.find('th')
                    td = row.find('td')
                    if th and td:
                        item[th.get_text(strip=True)] = td.get_text(strip=True)
            
            return item
        except:
            return None

    def start(self):
        ids = self.get_product_ids(max_pages=1) # 先試抓一頁
        if not ids: return

        print(f"開始抓取詳細資料，預計抓取 {len(ids)} 筆...")
        for i, p_cd in enumerate(ids):
            data = self.get_product_details(p_cd)
            if data:
                self.all_data.append(data)
                print(f"[{i+1}/{len(ids)}] 成功獲取: {data['商品名稱'][:15]}...")
            time.sleep(random.uniform(3, 5)) # 避免過快被擋

        df = pd.DataFrame(self.all_data)
        df.to_excel("pokemon_data_fixed.xlsx", index=False)
        print("✅ 抓取成功！請下載 pokemon_data_fixed.xlsx")

if __name__ == "__main__":
    scraper = PokemonCenterScraper()
    scraper.start()
