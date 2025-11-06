import requests
from bs4 import BeautifulSoup
import csv
import os.path
from typing import List, Tuple, Dict, Optional
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import logging
from time import sleep


CONFIG = {
    'BASE_URL': 'http://imot.bg',
    'HEADERS': ["Title", "Price", "oldValue", "Link", "RecordId"],
    'REQUEST_DELAY': 1,
    'DATA_DIR': 'data'  # Add this line
}

if not os.path.exists(CONFIG['DATA_DIR']):
    os.makedirs(CONFIG['DATA_DIR'])

def setup_logging(use_file=True):
    """Setup logging with optional file output"""
    log_file = 'data/scraper.log'
    if use_file:
        if os.path.exists(log_file):
            try:
                os.remove(log_file)
            except OSError as e:
                print(f"Error clearing log file: {e}")
        
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            filename=log_file
        )
    else:
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )

# Add this line after the function definition (around line 25):
setup_logging()

def create_session() -> requests.Session:
    """Create a session with retry logic"""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504]
    )
    session.mount('http://', HTTPAdapter(max_retries=retries))
    session.mount('https://', HTTPAdapter(max_retries=retries))
    return session

def read_input_urls(file_path: str = "data/inputURLS.csv") -> List[Tuple[str, str]]:
    """Read URLs and filenames from input CSV"""
    urls = []
    try:
        with open(file_path, "r", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)
            urls = [(row["URL"], row["FileName"]) for row in reader]
        logging.info(f"Successfully read {len(urls)} URLs from {file_path}")
        return urls
    except Exception as e:
        logging.error(f"Error reading input URLs: {str(e)}")
        return []

def read_reference_data(filename: str) -> Tuple[Dict[str, str], set]:
    """Read existing data for comparison"""
    reference_data = {}
    processed_keys = set()

    filepath = os.path.join(CONFIG['DATA_DIR'], filename)  # Add this line

    if not os.path.isfile(filepath):
        return reference_data, processed_keys

    try:
        with open(filepath, "r", encoding="utf-8") as csvfile:
            reader = csv.reader(csvfile)
            header = next(reader, None)
            if header is not None and len(header) == 5 and header[4] == "RecordId":
                for row in reader:
                    reference_data[row[4]] = row[1]
                    processed_keys.add(row[4])
    except Exception as e:
        logging.error(f"Error reading reference data: {str(e)}")

    return reference_data, processed_keys

def extract_listing_data(listing: BeautifulSoup) -> Optional[Tuple[str, str, str, str]]:
    """Extract data from a listing"""
    try:
        if len(listing['class']) > 1 and listing['class'][1] == 'fakti':
            return None

        record_id_key = listing['id'][3:]
        link_element = f"{CONFIG['BASE_URL']}/{record_id_key}"

        link_tag = listing.find("a", class_="title saveSlink")
        if not link_tag:
            return None

        full_description = link_tag.get_text(separator=' ', strip=True)
        parts = full_description.split(' ')
        title = parts[1] if len(parts) > 1 else ""

        price_div = listing.find("div", class_=lambda x: x and x.startswith('price'))
        if not price_div:
            return None

        price_text = extract_price(price_div)
        if not price_text:
            return None

        return title, price_text, link_element, record_id_key
    except Exception as e:
        logging.error(f"Error processing listing: {e}")
        return None

def extract_price(price_div: BeautifulSoup) -> Optional[str]:
    """Extract price information from price div"""
    try:
        vat_span = price_div.find('span')
        vat_status = vat_span.text.strip() if vat_span else ""

        price_data = price_div.find('div')
        if not price_data:
            return None

        main_price = price_data.get_text('\n').split('\n')[0].strip()
        return f"{main_price} | {vat_status}" if vat_status else main_price
    except Exception as e:
        logging.error(f"Error extracting price: {e}")
        return None

def process_page(session: requests.Session, url: str, page: int) -> Optional[BeautifulSoup]:
    """Process a single page"""
    try:
        sleep(CONFIG['REQUEST_DELAY'])
                
        # Handle both old and new URL formats
        if page == 1:
            page_url = url
        else:
            # Check if URL has parameters (contains '?')
            if '?' in url:
                # Split URL at '?' to insert pagination before parameters
                base_part, params = url.split('?', 1)
                page_url = f"{base_part.rstrip('/')}/p-{page}?{params}"
            else:
                # No parameters, just add pagination at the end
                page_url = f"{url.rstrip('/')}/p-{page}"
        
        response = session.get(page_url)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser")
    except Exception as e:
        logging.error(f"Error processing page {page_url}: {e}")
        return None

def write_output(filename: str, records: List[List[str]], headers: List[str]):
    """Write records to output file"""
    try:
        filepath = os.path.join(CONFIG['DATA_DIR'], filename)
        with open(filepath, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            writer.writerows(records)
    except Exception as e:
        logging.error(f"Error writing to {filename}: {e}")

def main():
    try:
        session = create_session()
        urls = read_input_urls()

        for base_url, output_filename in urls:
            logging.info(f"Processing: {output_filename}")
            reference_data, processed_keys = read_reference_data(output_filename)
            new_records = []
            all_records = []

            page = 1
            while True:
                soup = process_page(session, base_url, page)
                if not soup:
                    break

                listings = soup.find_all("div", class_=lambda x: x and x.startswith('item'))
                if not listings:
                    if page == 1:
                        logging.warning("No listings found on page 1")
                    break

                for listing in listings:
                    result = extract_listing_data(listing)
                    if not result:
                        continue

                    title, price_text, link_element, record_id_key = result
                    
                    if record_id_key in reference_data:
                        reference_value = reference_data[record_id_key]
                        if price_text != reference_value:
                            new_records.append([title, price_text, reference_value, link_element, record_id_key])
                            logging.info(f"Changed price: {record_id_key} from {reference_value} to {price_text} - {link_element}")
                        new_value = reference_value if price_text != reference_value else ""
                        del reference_data[record_id_key]
                    else:
                        new_value = "new"
                        new_records.append([title, price_text, new_value, link_element, record_id_key])
                        logging.info(f"New record: {title} with price: {price_text} - {link_element}")

                    all_records.append([title, price_text, new_value, link_element, record_id_key])
                
                if not soup.find('a', class_='saveSlink next'):
                    break
                page += 1

            # Handle remaining/deleted records
            for record_id_key, reference_value in reference_data.items():
                if record_id_key not in processed_keys:
                    all_records.append([record_id_key, reference_value, "missing", "", record_id_key])
                    logging.info(f"Missing record: {record_id_key}")

            # Write output files
            write_output(output_filename, all_records, CONFIG['HEADERS'])
            
            new_records_filename = f"NewRecords_{output_filename}"
            if new_records:
                write_output(new_records_filename, new_records, CONFIG['HEADERS'])
            else:
                try:
                    filepath = os.path.join(CONFIG['DATA_DIR'], new_records_filename)  # Add this line
                    if os.path.isfile(filepath):  # Change new_records_filename to filepath
                        os.remove(filepath)
                    # Modified message to include record count
                    total_records = len(all_records)
                    logging.info(f"No new records for: {output_filename} among {total_records} records")
                except OSError:
                    pass

        return True
    except Exception as e:
        logging.error(f"Scraper failed with exception: {e}")
        return False

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"Fatal error: {e}")
    finally:
        input("Press Enter to exit...")