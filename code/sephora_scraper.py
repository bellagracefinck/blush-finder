import os
import csv
import time
import re
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------
# 1. Image Processing: Dominant Color Extractor
# ---------------------------------------------------------
def get_dominant_hex(image_url):
    """Downloads a swatch image and returns the hex code of the most frequent color."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(image_url, headers=headers, timeout=10)
        response.raise_for_status()
        
        img = Image.open(BytesIO(response.content)).convert('RGB')
        
        # Quantize reduces the image to 5 distinct colors, grouping similar pixels together
        q_img = img.quantize(colors=5).convert('RGB')
        
        # Get all colors and sort them by the number of pixels (highest count first)
        colors = q_img.getcolors(maxcolors=img.width * img.height)
        colors.sort(key=lambda x: x[0], reverse=True)
        
        for count, (r, g, b) in colors:
            # Skip white/near-white pixels (like dividing lines and backgrounds)
            if r > 240 and g > 240 and b > 240:
                continue
            # Skip pure black pixels (like dark borders)
            if r < 15 and g < 15 and b < 15:
                continue
                
            return '#{:02x}{:02x}{:02x}'.format(r, g, b)
            
        # Fallback if the image is entirely white/black
        r, g, b = colors[0][1]
        return '#{:02x}{:02x}{:02x}'.format(r, g, b)
        
    except Exception as e:
        print(f"      [!] Error processing image {image_url}: {e}")
        return None

# ---------------------------------------------------------
# 2. HTML Parser Function
# ---------------------------------------------------------
def parse_sephora_product(html_content, product_url):
    """Parses a single Sephora product page's HTML."""
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

# ---------------------------------------------------------
# 3. Playwright Automation Engine
# ---------------------------------------------------------
def scrape_sephora_blushes():
    # We are saving this as a NEW file so it doesn't skip the old ones
    csv_filename = 'data/sephora_blushes_v2.csv' 
    headers = ['product url', 'brand', 'name', 'shade', 'dominant color 1']
    
    scraped_urls = set()
    os.makedirs('data', exist_ok=True)
    
    if os.path.exists(csv_filename):
        with open(csv_filename, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                scraped_urls.add(row['product url'])
        print(f"Resume Mode: Found {len(scraped_urls)} shades already saved.")
    else:
        with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()

    with sync_playwright() as p:
        print("Connecting to Chrome on port 9222...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0] 
        page = context.new_page()

        print("\nNavigating to Sephora Blush index...")
        page.goto("https://www.sephora.com/shop/blush", timeout=60000)
        
        print("Waiting 10 seconds for you to close any initial popups...")
        time.sleep(10) 

        print("Scrolling and looking for 'Show More' buttons...")
        while True:
            page.mouse.wheel(0, 2000)
            time.sleep(2)
            try:
                load_more_btn = page.locator("button:has-text('more')")
                if load_more_btn.count() > 0 and load_more_btn.first.is_visible():
                    print("Clicking 'Load More' button...")
                    load_more_btn.first.click(force=True)
                    time.sleep(4) 
                else:
                    print("No more buttons found! All products loaded.")
                    break
            except Exception as e:
                break

        print("Finding product links...")
        hrefs = page.evaluate("""() => {
            const anchors = Array.from(document.querySelectorAll('a[href*="/product/"]'));
            return anchors.map(a => a.href);
        }""")
        
        product_urls = list(set([url.split('?')[0].split('#')[0] for url in hrefs if '/product/' in url]))
        print(f"Found {len(product_urls)} unique products!")

        for i, url in enumerate(product_urls, 1):
            if url in scraped_urls:
                print(f"[{i}/{len(product_urls)}] Skipping (Already Scraped): {url}")
                continue

            print(f"\n[{i}/{len(product_urls)}] Loading: {url}")
            try:
                page.goto(url, timeout=45000)
                page.wait_for_selector('div[data-comp="SwatchGroup "]', timeout=8000)
                time.sleep(1.5) 
                
                html_content = page.content()
                extracted_data = parse_sephora_product(html_content, url)
                
                if extracted_data:
                    with open(csv_filename, 'a', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=headers)
                        writer.writerows(extracted_data)
                    scraped_urls.add(url) 
                else:
                    print("    No swatches found.")

            except Exception as e:
                print(f"    [!] Error loading product page: {e}")
                time.sleep(3)
                continue

        print(f"\nDone! All data safely stored in {csv_filename}")
        page.close() 

if __name__ == "__main__":
    scrape_sephora_blushes()