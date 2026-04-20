import csv
import time
import re
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from playwright.sync_api import sync_playwright

# 1. Dominant Color Extractor (Ignores White/Black)
def get_dominant_hex(image_url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(image_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        img = Image.open(BytesIO(response.content)).convert('RGB')
        q_img = img.quantize(colors=5).convert('RGB')
        
        colors = q_img.getcolors(maxcolors=img.width * img.height)
        colors.sort(key=lambda x: x[0], reverse=True)
        
        for count, (r, g, b) in colors:
            # Skip white/near-white (packaging/dividing lines)
            if r > 240 and g > 240 and b > 240:
                continue
            # Skip pure black
            if r < 15 and g < 15 and b < 15:
                continue
                
            return '#{:02x}{:02x}{:02x}'.format(r, g, b)
            
        r, g, b = colors[0][1]
        return '#{:02x}{:02x}{:02x}'.format(r, g, b)
        
    except Exception as e:
        print(f"      [!] Error processing image {image_url}: {e}")
        return None

# 2. HTML Parser
def parse_sephora_product(html_content, product_url):
    soup = BeautifulSoup(html_content, 'html.parser')
    blush_data = []

    brand_elem = soup.find('a', {'data-at': 'brand_name'})
    product_elem = soup.find('span', {'data-at': 'product_name'})
    
    brand = brand_elem.text.strip() if brand_elem else "Unknown Brand"
    product_name = product_elem.text.strip() if product_elem else "Unknown Product"

    swatch_buttons = soup.find_all('button', {'data-comp': 'SwatchItem '})

    for button in swatch_buttons:
        raw_label = button.get('aria-label', '')
        clean_label = re.sub(r'^Out of stock:\s*', '', raw_label)
        clean_label = re.sub(r'\s*-\s*Selected$', '', clean_label)
        
        parts = clean_label.split(' - ')
        shade_name = parts[0].strip() if len(parts) > 0 else clean_label
        
        img_tag = button.find('img')
        if not img_tag or not img_tag.get('src'):
            continue
            
        img_url = img_tag['src']
        if img_url.startswith('/'):
            img_url = 'https://www.sephora.com' + img_url
            
        print(f"    -> Extracting dominant shade: {shade_name}")
        hex_code = get_dominant_hex(img_url)

        if hex_code:
            blush_data.append({
                'product url': product_url,
                'brand': brand,
                'name': product_name,
                'shade': shade_name,
                'dominant color 1': hex_code
            })

    return blush_data

# 3. The Updater Engine
def update_existing_dataset():
    input_csv = 'data/sephora_blushes.csv'
    output_csv = 'data/sephora_blushes_updated.csv'
    headers = ['product url', 'brand', 'name', 'shade', 'dominant color 1']

    # Read the existing CSV to get all unique product URLs
    unique_urls = set()
    try:
        with open(input_csv, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                unique_urls.add(row['product url'])
    except FileNotFoundError:
        print(f"Could not find {input_csv}. Make sure it is in the data/ folder!")
        return

    urls_to_visit = list(unique_urls)
    print(f"Found {len(urls_to_visit)} unique products in your existing dataset.")

    # Prepare the new output file
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()

    # Launch Playwright to visit each product page
    with sync_playwright() as p:
        print("Connecting to Chrome on port 9222...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0] 
        page = context.new_page()

        for i, url in enumerate(urls_to_visit, 1):
            print(f"\n[{i}/{len(urls_to_visit)}] Loading: {url}")
            try:
                page.goto(url, timeout=45000)
                # Wait for the swatch elements to appear
                page.wait_for_selector('div[data-comp="SwatchGroup "]', timeout=8000)
                time.sleep(1.5) 
                
                html_content = page.content()
                extracted_data = parse_sephora_product(html_content, url)
                
                if extracted_data:
                    with open(output_csv, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=headers)
                        writer.writerows(extracted_data)
                else:
                    print("    No swatches found.")

            except Exception as e:
                print(f"    [!] Error loading product page: {e}")
                time.sleep(3)
                continue

        page.close()
        print(f"\nDone! All updated hex codes safely stored in {output_csv}")

if __name__ == "__main__":
    update_existing_dataset()