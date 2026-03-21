"""
Exploratory spider for TPEx (SSL bypassed)
"""
import requests
from bs4 import BeautifulSoup
import re
import json
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

print("Fetching TPEx homepage...")
res = requests.get('https://ic.tpex.org.tw/', headers=HEADERS, timeout=10, verify=False)
soup = BeautifulSoup(res.text, 'html.parser')

industries = {}
for a in soup.find_all('a', href=re.compile(r'introduce\.php\?ic=[A-Z0-9]+')):
    match = re.search(r'ic=([A-Z0-9]+)', a['href'])
    if match:
        code = match.group(1)
        name = a.text.strip()
        if name and code:
            industries[code] = name

print(f"Found {len(industries)} industries:")
for code, name in industries.items():
    print(f" - {code}: {name}")

# Inspect D000 半導體
res = requests.get('https://ic.tpex.org.tw/introduce.php?ic=D000', headers=HEADERS, verify=False)
soup = BeautifulSoup(res.text, 'html.parser')
links = soup.find_all('a', href=re.compile(r'stk=\d{4}'))
tickers = set()
for link in links:
    match = re.search(r'stk=(\d{4})', link['href'])
    if match:
        tickers.add(match.group(1))

print(f"\nFound {len(tickers)} companies in D000 (Semiconductor).")

print("\nStructure hints in D000:")
for heading in soup.find_all(['h2', 'h3', 'h4', 'div'], class_=re.compile(r'title|chain|node', re.I)):
    text = heading.text.strip().replace('\n', ' ')
    if text and len(text) < 50:
        print(f"  Tag: {heading.name}, Class: {heading.get('class')}, Text: {text}")

# On TPEx, the actual data is often fetched via AJAX to company_list.php
# Let's see if we can hit company_list.php directly
# The URL for company list is usually: https://ic.tpex.org.tw/company_list.php?ic=D000
print("\nFetching company_list.php?ic=D000...")
try:
    c_res = requests.get('https://ic.tpex.org.tw/company_list.php?ic=D000', headers=HEADERS, verify=False)
    if c_res.status_code == 200:
        print(f"Length of response: {len(c_res.text)}")
        c_soup = BeautifulSoup(c_res.text, 'html.parser')
        # Look for div.chain-node or similar
        nodes = c_soup.find_all('div', class_='chain-node')
        for n in nodes:
            print("Node found inside company_list:", n.get('class'), n.text)
except Exception as e:
    print(f"company_list fetch failed: {e}")
