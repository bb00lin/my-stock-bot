import os
import requests
import json
import re
import sys
import html
from datetime import datetime
from dateutil.relativedelta import relativedelta
from requests.auth import HTTPBasicAuth
from urllib.parse import urlparse

# ==========================================
# 1. Configuration
# ==========================================
RAW_URL = os.environ.get("CONF_URL")
USERNAME = os.environ.get("CONF_USER")
API_TOKEN = os.environ.get("CONF_PASS")
PARENT_PAGE_TITLE = "Personal Tasks"

if not RAW_URL or not USERNAME or not API_TOKEN:
    print("❌ Error: Missing environment variables (CONF_URL, CONF_USER, CONF_PASS)")
    sys.exit(1)

parsed_url = urlparse(RAW_URL)
BASE_URL = f"{parsed_url.scheme}://{parsed_url.netloc}"
API_ENDPOINT = f"{BASE_URL}/wiki/rest/api/content"

def get_headers():
    return {"Content-Type": "application/json"}

# ==========================================
# 2. Core Functions
# ==========================================

def get_page_by_id(page_id):
    """Fetch full page content by ID"""
    url = f"{API_ENDPOINT}/{page_id}"
    params = {'expand': 'body.storage,version,ancestors,space'}
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"❌ Failed to fetch page (ID: {page_id}): {e}")
    return None

def get_page_id_by_title(title):
    """Fetch page ID by title"""
    url = f"{API_ENDPOINT}"
    params = {'title': title, 'expand': 'body.storage,version,ancestors'}
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        results = r.json().get('results', [])
        if results: return results[0]
    except Exception as e:
        print(f"❌ Search failed for '{title}': {e}")
    return None

def get_child_pages(parent_id):
    """Get all child pages"""
    url = f"{API_ENDPOINT}/{parent_id}/child/page"
    params = {'limit': 100, 'expand': 'version'} 
    try:
        r = requests.get(url, auth=HTTPBasicAuth(USERNAME, API_TOKEN), params=params)
        r.raise_for_status()
        return r.json().get('results', [])
    except Exception as e:
        print(f"❌ Failed to get child pages: {e}")
        return []

def find_latest_monthly_page():
    print(f"🔍 Searching for parent page: {PARENT_PAGE_TITLE}...")
    parent_page = get_page_id_by_title(PARENT_PAGE_TITLE)
    if not parent_page:
        print(f"❌ Parent page not found: {PARENT_PAGE_TITLE}")
        sys.exit(1)

    parent_id = parent_page['id']
    print(f"✅ Parent ID found: {parent_id}")

    children = get_child_pages(parent_id)
    monthly_pages = []
    
    for child in children:
        title = child['title']
        if re.match(r'^\d{6}$', title):
            monthly_pages.append(child)
    
    if not monthly_pages:
        print("⚠️ No YYYYMM pages found under Personal Tasks.")
        sys.exit(1)

    # Sort to find the latest
    monthly_pages.sort(key=lambda x: x['title'], reverse=True)
    latest_basic_info = monthly_pages[0]
    
    print(f"📅 Latest month found: {latest_basic_info['title']} (ID: {latest_basic_info['id']})")
    
    # Fetch full content using ID
    full_page = get_page_by_id(latest_basic_info['id'])
    
    return full_page

def increment_date_match(match):
    """Callback: Increment date by 1 month"""
    full_date = match.group(0)
    sep = match.group(2)
    try:
        # Format: YYYY-MM-DD or YYYY/MM/DD
        fmt = f"%Y{sep}%m{sep}%d"
        dt = datetime.strptime(full_date, fmt)
        new_dt = dt + relativedelta(months=1)
        new_str = new_dt.strftime(fmt)
        return new_str
    except ValueError:
        return full_date

def process_jql_content_smart(html_content):
    """
    Locates JQL inside XML structure (Confluence Storage Format) 
    and updates dates safely.
    """
    print("🔧 Processing content (Smart XML Mode)...")
    
    # Debug: Print a snippet to verify content retrieval
    # print(f"   👀 Raw Content Snippet: {html_content[:500]}...")

    try:
        from bs4 import BeautifulSoup
        # Use 'xml' parser to handle Confluence Storage Format correctly
        soup = BeautifulSoup(html_content, 'xml')
    except Exception as e:
        print(f"⚠️ XML parser failed, falling back to html.parser: {e}")
        soup = BeautifulSoup(html_content, 'html.parser')

    # Find all Jira macros
    # The tag is usually <ac:structured-macro ac:name="jira">
    jira_macros = soup.find_all('ac:structured-macro', attrs={"ac:name": "jira"})
    
    print(f"   🔎 Found {len(jira_macros)} Jira macros.")
    
    total_modified = 0
    # Regex for YYYY-MM-DD
    date_pattern = re.compile(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})')

    for i, macro in enumerate(jira_macros):
        # Find the JQL parameter
        # <ac:parameter ac:name="jql"> ... </ac:parameter>
        jql_param = macro.find('ac:parameter', attrs={"ac:name": "jql"})
        
        if jql_param:
            raw_jql = jql_param.string
            if not raw_jql: continue

            # Decode HTML entities (e.g. &quot; -> ") just in case, though BS usually handles it
            decoded_jql = html.unescape(raw_jql)
            
            # Apply regex substitution
            new_jql, count = date_pattern.subn(increment_date_match, decoded_jql)
            
            if count > 0:
                print(f"      🔄 Table #{i+1}: Modified {count} dates.")
                # print(f"         OLD: {decoded_jql}")
                # print(f"         NEW: {new_jql}")
                
                # Update the soup content
                jql_param.string = new_jql
                total_modified += count
            else:
                print(f"      ⚠️ Table #{i+1}: No dates found in JQL.")
                # print(f"         JQL: {raw_jql}")

    if total_modified > 0:
        return str(soup)
    
    # --- FALLBACK: Plain Text Regex (if XML parsing missed it) ---
    print("⚠️ Smart XML mode made no changes. Trying Brute Force Regex on raw string...")
    
    # NOTE: Confluence often stores quotes as &quot; or &#34;
    # We will run regex on the raw string. The regex (\d{4}-\d{2}-\d{2}) is robust against surrounding quotes.
    new_raw_content, raw_count = date_pattern.subn(increment_date_match, html_content)
    
    if raw_count > 0:
        print(f"   💪 Brute Force updated {raw_count} dates!")
        return new_raw_content
    
    print("❌ No dates modified in either mode.")
    return html_content

def create_new_month_page(latest_page):
    current_title = latest_page['title']
    try:
        current_date_obj = datetime.strptime(current_title, "%Y%m")
        next_date_obj = current_date_obj + relativedelta(months=1)
        next_title = next_date_obj.strftime("%Y%m")
    except ValueError:
        print("❌ Title date format error (expected YYYYMM)")
        sys.exit(1)

    print(f"🚀 Preparing to create page: {next_title}")

    if get_page_id_by_title(next_title):
        print(f"⚠️ Page '{next_title}' already exists. Skipping.")
        return

    original_body = latest_page['body']['storage']['value']
    
    # Process content
    new_body = process_jql_content_smart(original_body)

    # Determine Parent ID
    if latest_page.get('ancestors'):
        parent_id = latest_page['ancestors'][-1]['id']
    else:
        p_page = get_page_id_by_title(PARENT_PAGE_TITLE)
        parent_id = p_page['id']

    payload = {
        "type": "page",
        "title": next_title,
        "ancestors": [{"id": parent_id}],
        "space": {"key": latest_page['space']['key']},
        "body": {
            "storage": {
                "value": new_body,
                "representation": "storage"
            }
        },
        "version": {
            "number": 1,
            "minorEdit": True
        }
    }

    try:
        response = requests.post(
            API_ENDPOINT, 
            auth=HTTPBasicAuth(USERNAME, API_TOKEN),
            headers=get_headers(),
            data=json.dumps(payload)
        )
        response.raise_for_status()
        
        data = response.json()
        base_url = BASE_URL.rstrip('/')
        link_suffix = data['_links']['webui']
        full_link = f"{base_url}/wiki{link_suffix}" if not link_suffix.startswith('/wiki') else f"{base_url}{link_suffix}"
        
        print(f"🎉 Success! New Page: {full_link}")

    except requests.exceptions.HTTPError as e:
        print(f"❌ Creation Failed: {e}")
        print(f"Response: {response.text}")
        sys.exit(1)

def main():
    print(f"=== Confluence Monthly Task Automator (v5.0) ===")
    try:
        latest_page = find_latest_monthly_page()
        if latest_page:
            create_new_month_page(latest_page)
        else:
            print("❌ Could not retrieve source page data.")
    except Exception as e:
        print(f"Execution Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
