import csv
import requests
from bs4 import BeautifulSoup
from pathlib import Path
import re

def main():
    url = "https://en.wikipedia.org/wiki/Metropolitan_statistical_area"
    print(f"Fetching tables from {url} ...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'lxml')
    
    msa_table = None
    for table in soup.find_all('table', class_='wikitable'):
        headers = [th.text.strip().lower() for th in table.find_all('th')]
        if any('metropolitan statistical area' in h for h in headers):
            msa_table = table
            break

    if not msa_table:
        print("Error: Could not find the MSA table on the Wikipedia page.")
        return

    regions = []
    
    # Process rows
    for row in msa_table.find_all('tr'):
        cells = row.find_all(['td', 'th'])
        if not cells or len(cells) < 2:
            continue
            
        # Usually the area name is in the second column (index 1), let's just find the cell that contains ' MSA'
        area_name = ""
        for cell in cells:
            text = cell.text.strip()
            if ' MSA' in text or ' Metropolitan Statistical Area' in text:
                area_name = text
                break
                
        # Fallback if the pattern changed: take the second column if it looks like a city, state
        if not area_name and len(cells) > 1:
            potential_name = cells[1].text.strip()
            if ',' in potential_name:
                 area_name = potential_name

        if not area_name:
            continue

        # Cleanup citations like "[1]"
        area_name = re.sub(r'\[\d+\]', '', area_name).strip()
        
        # Skip Puerto Rico
        if ' PR' in area_name or area_name.endswith(' PR'):
            continue
            
        if ',' in area_name:
            cities_part, states_part = area_name.split(',', 1)
            
            primary_city = cities_part.split('-')[0].strip()
            
            states_clean = states_part.replace(' MSA', '').replace(' Metropolitan Statistical Area', '').strip()
            primary_state = states_clean.split('-')[0].strip()
            primary_state = primary_state[:2]
            
            if primary_city and primary_state and len(primary_state) == 2:
                regions.append({'City': primary_city, 'State': primary_state})

    print(f"Found {len(regions)} metropolitan statistical areas.")
    
    data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    out_path = data_dir / "regions.csv"
    
    # Deduplicate while preserving order
    seen = set()
    unique_regions = []
    for r in regions:
        key = (r['City'], r['State'])
        if key not in seen:
            seen.add(key)
            unique_regions.append(r)
    
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['City', 'State'])
        writer.writeheader()
        writer.writerows(unique_regions)
        
    print(f"Successfully wrote {len(unique_regions)} regions to {out_path}")

if __name__ == "__main__":
    main()
