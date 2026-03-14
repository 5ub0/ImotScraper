"""
Scraper module for ImotScraper - handles property listing scraping logic.
This module is self-contained and does not depend on GUI or email components.
"""

import requests
from bs4 import BeautifulSoup
import re
import os.path
from typing import List, Tuple, Dict, Optional
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import logging
from time import sleep

from database.db_manager import DatabaseManager


class ImotScraper:
    """Handles scraping of property listings from imot.bg"""
    
    CONFIG = {
        'BASE_URL': 'http://imot.bg',
        'REQUEST_DELAY': 1,
        'DETAIL_DELAY': 0.3,
        'DATA_DIR': 'data'
    }

    def __init__(self, data_dir='data'):
        """Initialize the scraper with configuration"""
        self.config = self.CONFIG.copy()
        self.config['DATA_DIR'] = data_dir
        self.logger = logging.getLogger(__name__)
        self.db = DatabaseManager(db_path=os.path.join(data_dir, "imot_scraper.db"))

        if not os.path.exists(self.config['DATA_DIR']):
            os.makedirs(self.config['DATA_DIR'])

    def execute(self) -> bool:
        """Execute the scraping job. Reads searches from DB, persists results to DB."""
        try:
            self.logger.info("Starting scraper execution - reading searches from database.")

            searches = self.db.get_all_searches()
            if not searches:
                self.logger.warning("No searches found in the database. Add searches via the GUI.")
                return True

            session = self._create_session()

            all_success     = True
            all_errors: list[str] = []

            for search in searches:
                self.logger.info(f"Processing: {search['search_name']}")
                result = self._scrape_search(session, search["url"], search["search_name"], search["id"])
                if not result["success"]:
                    all_success = False
                    if result["error_message"]:
                        all_errors.append(f"{search['search_name']}: {result['error_message']}")

            return all_success

        except Exception as e:
            self.logger.error(f"Scraper failed with exception: {e}")
            return False

    def _scrape_search(self, session: requests.Session, base_url: str, search_name: str, search_id: int) -> dict:
        """Scrape all pages for one search entry and persist results.

        Returns a dict with counters for aggregation in execute():
          records_found, new_records, changed_prices, inactive_count,
          active_count, sqm_values (list[float]), success, error_message.
        """
        active_record_ids: List[str] = []
        records_found = 0
        new_count = 0
        changed_count = 0

        try:
            # Pre-load known prices for this search into a dict to avoid one DB
            # round-trip per listing inside the loop.
            known = self._load_known_prices(search_id)

            # Snapshot which record_ids are Active RIGHT NOW (before the scrape loop)
            # so we can later distinguish truly-new inactivations from ones that were
            # already Inactive in a previous run.
            known_active_before: set[str] = {
                p["record_id"]
                for p in self.db.get_properties(search_id, status="Active")
            }

            page = 1
            while True:
                soup = self._process_page(session, base_url, page)
                if not soup:
                    break

                listings = soup.find_all("div", class_=lambda x: x and x.startswith('item'))
                if not listings:
                    if page == 1:
                        self.logger.warning(f"No listings found on page 1 for {search_name}")
                    break

                for listing in listings:
                    result = self._extract_listing_data(listing)
                    if not result:
                        continue

                    list_title, price_text, link, record_id = result
                    records_found += 1
                    active_record_ids.append(record_id)

                    existing_price, existing_title, existing_location = known.get(record_id, (None, None, None))

                    if existing_price is None:
                        # Brand new listing — fetch detail page for clean title, location, description, images, price/m²
                        title, location, description, image_urls, price_per_sqm, area_sqm, floor, yard_sqm = self._extract_title_and_location(session, link)
                        if not title:
                            title = list_title
                        is_new = True
                        new_count += 1
                        self.logger.info(f"New listing: {title} | price: {price_text} | search: {search_name} | {link}")
                    elif existing_price != price_text:
                        # Price changed — reuse stored title/location/description, no detail fetch needed
                        title = existing_title or list_title
                        location = existing_location or ""
                        description = None   # COALESCE keeps existing value in DB
                        image_urls  = None   # no re-fetch; images already stored
                        area_sqm    = None   # COALESCE keeps existing value in DB
                        floor       = None   # COALESCE keeps existing value in DB
                        yard_sqm    = None   # COALESCE keeps existing value in DB
                        # Re-fetch detail page only for the updated price/m²
                        _, _, _, _, price_per_sqm, *_ = self._extract_title_and_location(session, link)
                        is_new = False
                        changed_count += 1
                        self.logger.info(f"Price change: {title} | old: {existing_price} | new: {price_text} | search: {search_name} | {link}")
                    else:
                        # Unchanged — skip detail fetch and DB write entirely.
                        continue

                    property_id = self.db.upsert_property(
                        record_id=record_id,
                        search_id=search_id,
                        title=title,
                        location=location,
                        description=description,
                        link=link,
                        price=price_text,
                        is_new=is_new,
                        price_per_sqm=price_per_sqm,
                        area_sqm=area_sqm,
                        floor=floor,
                        yard_sqm=yard_sqm,
                    )

                    # Store images only for new listings (detail page already fetched)
                    if is_new and image_urls:
                        n = self.db.upsert_images(property_id, image_urls, session=session)
                        self.logger.debug(f"  Stored {n}/{len(image_urls)} images")

                if not soup.find('a', class_='saveSlink next'):
                    break
                page += 1

            # Mark anything not seen this run as Inactive
            inactive_count = self.db.mark_inactive(search_id, active_record_ids)

            # Emit a feed log line only for listings that were Active BEFORE this run
            # and are now gone — skip ones that were already Inactive from a previous run.
            # known_active_before was captured before the scrape loop started.
            if inactive_count > 0:
                active_set = set(active_record_ids)
                for rid in known_active_before:
                    if rid not in active_set:
                        price, title, location = known[rid]
                        link = self.db.get_link_for_record(rid, search_id)
                        self.logger.info(
                            f"Removed listing: {title or rid} | search: {search_name} | {link or ''}"
                        )

            # Record area avg snapshot for this search after the run
            self.db.record_area_stats_snapshot(search_id)

            # Compute avg €/m² and active count for this search
            active_props = self.db.get_properties(search_id, status="Active")
            active_count = len(active_props)
            sqm_values: list[float] = []
            for p in active_props:
                raw = p.get("price_per_sqm")
                if raw:
                    try:
                        sqm_values.append(float(raw.split()[0].replace(",", ".")))
                    except (ValueError, IndexError):
                        pass

            self.logger.info(
                f"Done '{search_name}': {records_found} found, "
                f"{new_count} new, {changed_count} changed, {inactive_count} inactive."
            )

            avg_sqm = round(sum(sqm_values) / len(sqm_values), 2) if sqm_values else None
            self.db.log_scrape_run(
                searches=search_name,
                records_found=records_found,
                new_records=new_count,
                changed_prices=changed_count,
                inactive_count=inactive_count,
                success=True,
                avg_price_per_sqm=avg_sqm,
                active_count=active_count,
                search_id=search_id,
            )

            return {
                "records_found":  records_found,
                "new_records":    new_count,
                "changed_prices": changed_count,
                "inactive_count": inactive_count,
                "active_count":   active_count,
                "sqm_values":     sqm_values,
                "success":        True,
                "error_message":  None,
            }

        except Exception as e:
            self.logger.error(f"Error scraping '{search_name}': {e}")
            self.db.log_scrape_run(
                searches=search_name,
                records_found=records_found,
                new_records=new_count,
                changed_prices=changed_count,
                inactive_count=0,
                success=False,
                error_message=str(e),
                avg_price_per_sqm=None,
                active_count=0,
                search_id=search_id,
            )
            return {
                "records_found":  records_found,
                "new_records":    new_count,
                "changed_prices": changed_count,
                "inactive_count": 0,
                "active_count":   0,
                "sqm_values":     [],
                "success":        False,
                "error_message":  str(e),
            }

    def _load_known_prices(self, search_id: int) -> dict:
        """
        Load all known properties for this search into a dict keyed by record_id.
        Value is (current_price, title, location) — single DB read before the scrape loop.
        Returns {} if no properties exist yet.
        """
        props = self.db.get_properties(search_id, status=None)
        if not props:
            return {}

        conn = self.db._get_connection()
        result = {}
        for p in props:
            row = conn.execute(
                "SELECT price FROM price_history WHERE property_id = ? AND price_status = 'Current'",
                (p["id"],)
            ).fetchone()
            if row:
                result[p["record_id"]] = (row["price"], p.get("title", ""), p.get("location", ""))
        return result

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

    def _extract_listing_data(self, listing: BeautifulSoup) -> Optional[Tuple[str, str, str, str]]:
        """Extract data from a listing card. Returns (title, price_text, link, record_id)."""
        try:
            if len(listing['class']) > 1 and listing['class'][1] == 'fakti':
                return None

            record_id_key = listing['id'][3:]
            link_element = f"{self.config['BASE_URL']}/{record_id_key}"

            link_tag = listing.find("a", class_="title saveSlink")
            if not link_tag:
                return None

            # Keep the full description text as the title (stripped of extra whitespace)
            title = link_tag.get_text(separator=' ', strip=True)

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

    def _extract_price_per_sqm(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract the price-per-square-metre value from a parsed page (detail page
        or listing card).  The relevant HTML looks like:
          <span>(1 686 €, 3 297.53 лв./m<sup>2</sup>)</span>
        The span may be nested at any depth inside the price block, so we search
        the entire price div without restricting to direct children.
        Returns e.g. "1686 €/m²" or None if not present.
        """
        try:
            # Use exact class match — NOT a startswith lambda.
            # The page also contains div.price-stat (translations block) which
            # appears earlier in the DOM and would be returned by a startswith match.
            price_div = soup.find("div", class_="price")
            if not price_div:
                return None

            # Search all descendant spans — the €/m² span can be nested inside
            # .cena or other inner elements depending on page type.
            # Skip spans that belong to the priceHistory block to avoid false matches.
            price_history = price_div.find("div", class_=lambda x: x and "priceHistory" in x)
            for span in price_div.find_all("span"):
                # Skip spans inside the price-history sub-block
                if price_history and price_history in span.parents:
                    continue
                # get_text collapses <sup>2</sup> → "2", giving e.g. "(1 686 €, 3 297.53 лв./m 2 )"
                text = span.get_text(" ", strip=True)
                if "/m" not in text:
                    continue
                # Capture the EUR figure before the € sign
                m = re.search(r'\(\s*(\d[\d\s.]*(?:,\d+)?)\s*€', text)
                if m:
                    raw = m.group(1).strip()
                    # Normalise: remove thousands-space, keep decimal dot
                    # e.g. "1 686" → "1686", "10.43" → "10.43"
                    value = raw.replace(" ", "").replace(",", ".")
                    self.logger.debug(f"price/m² extracted: {value} from span text: {repr(text)}")
                    return f"{value} €/m²"

            self.logger.debug(f"price/m² not found in price div: {repr(price_div.get_text(' ', strip=True)[:80])}")
            return None
        except Exception as e:
            self.logger.debug(f"price/m² extraction error: {e}")
            return None

    def _fetch_detail(self, session: requests.Session, url: str) -> Optional[BeautifulSoup]:
        """Fetch and parse an individual property detail page."""
        try:
            sleep(self.config['DETAIL_DELAY'])
            response = session.get(url, timeout=15)
            response.raise_for_status()
            return BeautifulSoup(response.content, "html.parser")
        except Exception as e:
            self.logger.warning(f"Could not fetch detail page {url}: {e}")
            return None

    def _extract_title_and_location(self, session: requests.Session, url: str) -> Tuple[str, str, str, List[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Fetch the property detail page and extract the clean title, full location,
        description text, carousel image URLs, price per square metre, area (m²),
        floor info, and yard size (m²) for houses.

        The price-per-sqm is read from the detail page's price block (same page
        as images and description) — it is NOT reliably available on the listing
        card in the search results page.

        Only non-cloned owl-item divs are collected for images (the carousel
        duplicates cloned items for infinite-scroll; those carry the same URLs).

        Returns (title, location, description, image_urls, price_per_sqm, area_sqm, floor, yard_sqm)
        — empty/None values on failure.
        """
        soup = self._fetch_detail(session, url)
        if not soup:
            return "", "", "", [], None, None, None, None

        adv_header = soup.find("div", class_="advHeader")
        if not adv_header:
            return "", "", "", [], None, None, None, None

        # --- Title ---
        title_div = adv_header.find("div", class_="title")
        title = ""
        if title_div:
            btns = title_div.find("div", class_="btns")
            if btns:
                btns.decompose()
            title = title_div.get_text(separator=' ', strip=True)

        # --- Location ---
        location = ""
        location_div = adv_header.find("div", class_="location")
        if location_div:
            parts = []
            for node in location_div.descendants:
                if isinstance(node, str):
                    text = node.strip()
                    if text:
                        parts.append(text)
            location = ", ".join(dict.fromkeys(parts))

        # --- Description ---
        description = ""
        more_info = soup.find("div", class_="moreInfo")
        if more_info:
            text_div = more_info.find("div", class_="text")
            if text_div:
                description = text_div.get_text(separator='\n', strip=True)

        # --- Price per sqm ---
        price_per_sqm = self._extract_price_per_sqm(soup)

        # --- adParams: area, floor, yard ---
        area_sqm: Optional[str] = None
        floor:    Optional[str] = None
        yard_sqm: Optional[str] = None
        ad_params = soup.find("div", class_="adParams")
        if ad_params:
            for div in ad_params.find_all("div", recursive=False):
                label = div.get_text(separator="\n", strip=True)
                strong = div.find("strong")
                value = strong.get_text(strip=True) if strong else ""
                if not value:
                    continue
                if "Площ" in label:
                    # "54 m²" — strip the superscript number that may appear after "m"
                    area_sqm = value.replace("\u00b2", "²").strip()
                elif "Eтаж" in label or "Етаж" in label:
                    floor = value.strip()
                elif "Двор" in label:
                    yard_sqm = value.replace("\u00b2", "²").strip()

        # --- Images ---
        image_urls: List[str] = []
        seen: set = set()
        for img in soup.find_all("img", class_="carouselimg"):
            if len(image_urls) >= 5:
                break
            src = img.get("data-src", "")
            if src and src not in seen:
                seen.add(src)
                image_urls.append(src)

        return title, location, description, image_urls, price_per_sqm, area_sqm, floor, yard_sqm

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