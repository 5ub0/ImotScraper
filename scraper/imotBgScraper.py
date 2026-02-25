"""
Scraper module for ImotScraper - handles property listing scraping logic.
This module is self-contained and does not depend on GUI or email components.
"""

import requests
from bs4 import BeautifulSoup
import csv
import os.path
from typing import List, Tuple, Dict, Optional
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import logging
from time import sleep


class ImotScraper:
    """Handles scraping of property listings from imot.bg"""
    
    CONFIG = {
        'BASE_URL': 'http://imot.bg',
        'HEADERS': ["Title", "Price", "oldValue", "Link", "RecordId"],
        'REQUEST_DELAY': 1,
        'DATA_DIR': 'data'
    }

    def __init__(self, data_dir='data'):
        """Initialize the scraper with configuration"""
        self.config = self.CONFIG.copy()
        self.config['DATA_DIR'] = data_dir
        self.logger = logging.getLogger(__name__)
        
        if not os.path.exists(self.config['DATA_DIR']):
            os.makedirs(self.config['DATA_DIR'])

    def execute(self, input_csv: str = "data/inputURLS.csv") -> bool:
        """Execute the scraping job"""
        try:
            self.logger.info(f"Starting scraper execution with input CSV: {input_csv}")
            
            # Check if input CSV exists
            if not os.path.exists(input_csv):
                self.logger.error(f"Input CSV file not found: {input_csv}")
                return False
            
            session = self._create_session()
            urls = self._read_input_urls(input_csv)
            
            if not urls:
                self.logger.warning("No URLs found in input CSV file")
                return True  # Not an error, just no URLs to process

            for base_url, output_filename in urls:
                self.logger.info(f"Processing: {output_filename}")
                reference_data, processed_keys = self._read_reference_data(output_filename)
                new_records = []
                all_records = []

                page = 1
                while True:
                    soup = self._process_page(session, base_url, page)
                    if not soup:
                        break

                    listings = soup.find_all("div", class_=lambda x: x and x.startswith('item'))
                    if not listings:
                        if page == 1:
                            self.logger.warning("No listings found on page 1")
                        break

                    for listing in listings:
                        result = self._extract_listing_data(listing)
                        if not result:
                            continue

                        title, price_text, link_element, record_id_key = result
                        
                        if record_id_key in reference_data:
                            reference_value = reference_data[record_id_key]
                            if price_text != reference_value:
                                new_records.append([title, price_text, reference_value, link_element, record_id_key])
                                self.logger.info(f"Changed price: {record_id_key} from {reference_value} to {price_text} - {link_element}")
                            new_value = reference_value if price_text != reference_value else ""
                            del reference_data[record_id_key]
                        else:
                            new_value = "new"
                            new_records.append([title, price_text, new_value, link_element, record_id_key])
                            self.logger.info(f"New record: {title} with price: {price_text} - {link_element}")

                        all_records.append([title, price_text, new_value, link_element, record_id_key])
                    
                    if not soup.find('a', class_='saveSlink next'):
                        break
                    page += 1

                # Handle remaining/deleted records
                for record_id_key, reference_value in reference_data.items():
                    if record_id_key not in processed_keys:
                        all_records.append([record_id_key, reference_value, "missing", "", record_id_key])
                        self.logger.info(f"Missing record: {record_id_key}")

                # Write output files
                self._write_output(output_filename, all_records, self.config['HEADERS'])
                
                new_records_filename = f"NewRecords_{output_filename}"
                if new_records:
                    self._write_output(new_records_filename, new_records, self.config['HEADERS'])
                else:
                    try:
                        filepath = os.path.join(self.config['DATA_DIR'], new_records_filename)
                        if os.path.isfile(filepath):
                            os.remove(filepath)
                        total_records = len(all_records)
                        self.logger.info(f"No new records for: {output_filename} among {total_records} records")
                    except OSError:
                        pass

            return True
        except Exception as e:
            self.logger.error(f"Scraper failed with exception: {e}")
            return False

    def _create_session(self) -> requests.Session:
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

    def _read_input_urls(self, file_path: str) -> List[Tuple[str, str]]:
        """Read URLs and filenames from input CSV"""
        urls = []
        try:
            self.logger.info(f"Reading input URLs from: {file_path}")
            with open(file_path, "r", encoding="utf-8") as csvfile:
                reader = csv.DictReader(csvfile)
                if reader.fieldnames is None:
                    self.logger.error(f"Input CSV file is empty: {file_path}")
                    return urls
                
                urls = [(row["URL"], row["FileName"]) for row in reader if row.get("URL") and row.get("FileName")]
            
            self.logger.info(f"Successfully read {len(urls)} URLs from {file_path}")
            return urls
        except FileNotFoundError:
            self.logger.error(f"Input CSV file not found: {file_path}")
            return urls
        except KeyError as e:
            self.logger.error(f"CSV file missing required column {e}: {file_path}")
            return urls
        except Exception as e:
            self.logger.error(f"Error reading input URLs: {str(e)}")
            return urls

    def _read_reference_data(self, filename: str) -> Tuple[Dict[str, str], set]:
        """Read existing data for comparison"""
        reference_data = {}
        processed_keys = set()

        filepath = os.path.join(self.config['DATA_DIR'], filename)

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
            self.logger.error(f"Error reading reference data: {str(e)}")

        return reference_data, processed_keys

    def _extract_listing_data(self, listing: BeautifulSoup) -> Optional[Tuple[str, str, str, str]]:
        """Extract data from a listing"""
        try:
            if len(listing['class']) > 1 and listing['class'][1] == 'fakti':
                return None

            record_id_key = listing['id'][3:]
            link_element = f"{self.config['BASE_URL']}/{record_id_key}"

            link_tag = listing.find("a", class_="title saveSlink")
            if not link_tag:
                return None

            full_description = link_tag.get_text(separator=' ', strip=True)
            parts = full_description.split(' ')
            title = parts[1] if len(parts) > 1 else ""

            price_div = listing.find("div", class_=lambda x: x and x.startswith('price'))
            if not price_div:
                return None

            price_text = self._extract_price(price_div)
            if not price_text:
                return None

            return title, price_text, link_element, record_id_key
        except Exception as e:
            self.logger.error(f"Error processing listing: {e}")
            return None

    def _extract_price(self, price_div: BeautifulSoup) -> Optional[str]:
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
            self.logger.error(f"Error extracting price: {e}")
            return None

    def _process_page(self, session: requests.Session, url: str, page: int) -> Optional[BeautifulSoup]:
        """Process a single page"""
        try:
            sleep(self.config['REQUEST_DELAY'])
                    
            if page == 1:
                page_url = url
            else:
                if '?' in url:
                    base_part, params = url.split('?', 1)
                    page_url = f"{base_part.rstrip('/')}/p-{page}?{params}"
                else:
                    page_url = f"{url.rstrip('/')}/p-{page}"
            
            response = session.get(page_url)
            response.raise_for_status()
            return BeautifulSoup(response.content, "html.parser")
        except Exception as e:
            self.logger.error(f"Error processing page {page}: {e}")
            return None

    def _write_output(self, filename: str, records: List[List[str]], headers: List[str]):
        """Write records to output file"""
        try:
            filepath = os.path.join(self.config['DATA_DIR'], filename)
            with open(filepath, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(headers)
                writer.writerows(records)
        except Exception as e:
            self.logger.error(f"Error writing to {filename}: {e}")


# Backward compatibility function
def main():
    """Legacy entry point for backward compatibility"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        filename='data/scraper.log'
    )
    scraper = ImotScraper()
    return scraper.execute()