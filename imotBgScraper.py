import requests
from bs4 import BeautifulSoup
import csv
import os.path
import re

# Read the URLs and filenames from the inputURLS.csv file
urls = []
with open("inputURLS.csv", "r") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        url = row["URL"]
        filename = row["FileName"]
        urls.append((url, filename))

# Iterate over each URL and corresponding output filename
for base_url, output_filename in urls:
    print(f"\n--- Starting scrape for {output_filename} ---")
    
    reference_data = {}
    processed_keys = set()
    new_records = []
    file_exists = os.path.isfile(output_filename)

    # Read existing data for comparison
    if file_exists:
        try:
            with open(output_filename, "r", encoding="utf-8") as csvfile:
                reader = csv.reader(csvfile)
                header = next(reader, None)
                if header is not None and len(header) == 5 and header[4] == "RecordId":
                    for row in reader:
                        reference_key = row[4]
                        reference_data[reference_key] = row[1]
                        processed_keys.add(reference_key)
        except Exception as e:
            print(f"Warning: Could not read existing file {output_filename}. Error: {e}")
    
    # Clean the output file and write header
    with open(output_filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Title", "Price", "oldValue", "Link", "RecordId"])

    # Start scraping loop in append mode
    with open(output_filename, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)

        page = 1
        
        # --- PAGINATION LOGIC: Loop until no "next" button is found ---
        while True:
            # Construct the URL by appending the page suffix
            if page == 1:
                page_url = base_url
            else:
                # Append /p-X for subsequent pages
                clean_base_url = base_url.rstrip('/')
                page_url = f"{clean_base_url}/p-{page}"

            print(f"Scraping page {page}: {page_url}")

            try:
                response = requests.get(page_url)
                response.raise_for_status()
                html_content = response.content
            except requests.exceptions.RequestException as e:
                print(f"Error fetching {page_url}: {e}. Stopping pagination.")
                break

            soup = BeautifulSoup(html_content, "html.parser")

            # --- CRITICAL FIX: Find listings based on the "item" classes ---
            # Using a function to match classes starting with 'item' (e.g., 'item ', 'item TOP ', 'item VIP ')
            listings = soup.find_all("div", class_=lambda x: x and x.startswith('item'))

            if not listings and page == 1:
                print("Warning: No listings found on page 1. Check your base URL or selectors.")
            
            if not listings:
                print(f"No more listings found on page {page}. Assuming this is the last page.")
                break # Exit the while loop

            # --- PROCESS LISTINGS ---
            for listing in listings:
                if len(listing['class']) > 1 and listing['class'][1] == 'fakti':
                    continue

                record_id_key = listing['id'][3:]
                
                link_element = "http://imot.bg/"+record_id_key            

                link_tag = listing.find("a", class_="title saveSlink")

                # Now you can extract the text and link safely
                if link_tag:
                    # 1. Get the combined text (Title + Location)
                    full_description = link_tag.get_text(separator=' ', strip=True)
                    parts = full_description.split(' ')

                    # parts ще бъде списък: ['Продава', '2-СТАЕН', 'град', 'София,', 'Горна', 'баня']

                    # 2. Проверяваме дали списъкът е достатъчно дълъг и извличаме втория елемент (индекс 1).
                    # Вторият елемент е винаги на индекс 1 в списъка.
                    if len(parts) > 1:
                        full_description = parts[1]
                    
                # 2. Extract Price
                # Price is inside the first div within class="price "
                price_div = listing.find("div", class_=lambda x: x and x.startswith('price'))
                price_text = ""

                vat_span = price_div.find('span')
                vat_status = vat_span.text.strip() if vat_span else ""

                if price_div:
                    # The price text is inside the first <div> inside price_div
                    price_data = price_div.find('div')
                    # Get the primary price (e.g., "167 520 €") and clean it up
                    main_price = price_data.get_text('\n').split('\n')[0].strip()
                    
                    # Combine the main price and the VAT status
                    if vat_status:
                        # Join the price and status, e.g., "167 520 € | Цената е без ДДС"
                        price_text = f"{main_price} | {vat_status}"
                    else:
                        price_text = main_price
                
                if not price_text:
                    continue # Skip if no price found


                # --- Comparison Logic ---
                if record_id_key in reference_data:
                    reference_value = reference_data[record_id_key]
                    if price_text != reference_value:
                        new_value = reference_value
                        new_records.append([full_description, price_text, new_value, link_element, record_id_key])
                        print(f"Record with changed price: {record_id_key}; With price: {new_value}->{price_text}")
                    else:
                        new_value = ""
                    del reference_data[record_id_key]
                    processed_keys.add(record_id_key)
                else:
                    new_value = "new"
                    new_records.append([full_description, price_text, new_value, link_element, record_id_key])  # Store the new record
                    print(f"New record: {record_id_key}; With price: {price_text}")

                writer.writerow([full_description, price_text, new_value, link_element, record_id_key])

                

            # --- CHECK FOR NEXT PAGE BUTTON ---
            # The next page link is often in the pagination section. Let's rely on finding 
            # a link that has the next page number/indicator to be safer than a simple div.
            next_link = soup.find('a', class_='saveSlink next')
            # Check for the link in the pagination area pointing to the next page
            
            if next_link:
                page += 1
            else:
                break

    # --- Process the remaining records (Missing items/Deleted listings) ---
    with open(output_filename, "a", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        for record_id_key, reference_value in reference_data.items():
            if record_id_key not in processed_keys:
                writer.writerow([record_id_key, reference_value, "missing"])
                print(f"Missing record (might be deleted): {record_id_key}; Old price: {reference_value}")

    # --- Create a new file for new or changed records ---
    new_records_filename = "NewRecords_" + output_filename
    if new_records:
        with open(new_records_filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(["RecordId", "Price", "oldValue"])
            writer.writerows(new_records)
    else:
        try:
            if os.path.isfile(new_records_filename):
                os.remove(new_records_filename)
            print(f"There are no new/changed records for: {output_filename}")
        except OSError:
            print(f"There are no new/changed records for: {output_filename}")

print("\nScraping complete.")
input("Press Enter to exit...")