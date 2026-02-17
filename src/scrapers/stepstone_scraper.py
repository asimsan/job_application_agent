# src/scrapers/stepstone_scraper.py
from playwright.sync_api import sync_playwright
import time
import logging
import json
import random
from datetime import datetime
from urllib.parse import urlencode
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class StepstoneScraper:
    """
    Scraper for extracting job postings from StepStone.de.
    Note: StepStone might use JavaScript rendering, so Playwright is used.
    """
    BASE_URL = "https://www.stepstone.de/jobs"

    def __init__(self, headless=True):
        self.headless = headless

    def _build_search_url(self, keywords, location, page=1):
        """
        Builds the StepStone.de job search URL.

        Args:
            keywords (str): Job title, skill, or company.
            location (str): Geographical area.
            page (int): Page number for pagination (starts at 1).

        Returns:
            str: The constructed StepStone job search URL.
        """
        # Basic structure based on observation/web search (may need refinement)
        # Example: https://www.stepstone.de/jobs/python?location=berlin&page=1
        params = {}
        query_path = ""

        if keywords:
            # Keywords often seem to be part of the path
            query_path += f"/{keywords.replace(' ', '-')}" # Basic slugify

        # Location and page seem to be query parameters
        if location:
            params['location'] = location
        if page > 1:
            params['page'] = page

        # Add other potential params like sort, radius etc. later if needed
        # params['sort'] = 'standard' # Example

        encoded_params = urlencode(params)
        url = f"{self.BASE_URL}{query_path}"
        if encoded_params:
            url += f"?{encoded_params}"

        logging.info(f"Constructed search URL: {url}")
        return url

    def search_jobs(self, keywords, location, max_results=25, days_posted_ago=None):
        """
        Searches for jobs on StepStone.de based on keywords and location.
        NOTE: days_posted_ago filter might require specific StepStone parameter - not implemented yet.

        Args:
            keywords (str): Job title, skill, or company.
            location (str): Geographical area.
            max_results (int): Maximum number of job results to fetch.
            days_posted_ago (int, optional): Filter jobs posted within the last X days (currently ignored).

        Returns:
            list: A list of job dictionaries, each containing basic info.
        """
        if days_posted_ago:
             logging.warning("days_posted_ago filter is not yet implemented for StepStone.")

        job_list = []
        processed_urls = set()
        current_page = 1

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            page = context.new_page()
            logging.info(f"Launched browser (headless={self.headless})")

            while len(job_list) < max_results:
                search_url = self._build_search_url(keywords, location, page=current_page)
                logging.info(f"Navigating to page {current_page}: {search_url}")

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                    time.sleep(random.uniform(2, 4))

                    # --- Handle Popups/Consent (as added previously) ---
                    # 1. Try to accept cookies (selectors need verification)
                    cookie_accept_selectors = [
                        "button#ccmgt_explicit_accept", # Common ID pattern
                        "button[data-testid='uc-accept-all-button']", # Another pattern
                        "//button[contains(text(), 'Akzeptieren')]", # XPath for German text
                        "//button[contains(text(), 'Accept')]" # XPath for English text
                    ]
                    accepted_cookies = False
                    for selector in cookie_accept_selectors:
                        try:
                            accept_button = page.locator(selector).first
                            if accept_button.is_visible(timeout=2000):
                                logging.info(f"Found cookie consent button: {selector}. Clicking...")
                                accept_button.click(timeout=3000)
                                accepted_cookies = True
                                time.sleep(1)
                                break
                        except Exception:
                            logging.debug(f"Cookie button selector {selector} not found/visible.")
                            pass
                    if accepted_cookies:
                        logging.info("Cookie consent likely accepted.")
                    else:
                        logging.info("No obvious cookie consent button found or clicked.")

                    # 2. Try to close other modals
                    close_button_selectors = [
                        "button[aria-label='close']",
                        "button[aria-label='Close']",
                        "button[aria-label='Schließen']",
                        "button.modal-close",
                        "div[role='dialog'] button.close",
                        "button[data-dismiss='modal']"
                    ]
                    closed_modal = False
                    for selector in close_button_selectors:
                         try:
                             close_button = page.locator(selector).first
                             if close_button.is_visible(timeout=1500):
                                 logging.info(f"Found potential modal close button: {selector}. Clicking...")
                                 close_button.click(timeout=3000)
                                 closed_modal = True
                                 time.sleep(1)
                         except Exception:
                             logging.debug(f"Modal close button selector {selector} not found/visible.")
                             pass
                    if closed_modal:
                        logging.info("Attempted to close modal popup(s).")
                    # --- END: Handle Popups/Consent ---

                    # --- Find Job Cards using the provided selector ---
                    job_elements = page.query_selector_all('article[data-at="job-item"]') # Updated selector
                    logging.info(f"Found {len(job_elements)} potential job elements on page {current_page}.")

                    if not job_elements:
                        logging.info(f"No job elements found on page {current_page}. Assuming end of results.")
                        break

                    new_jobs_found_this_page = 0
                    for job_element in job_elements:
                        if len(job_list) >= max_results:
                            break

                        # --- Extract data using specific StepStone selectors ---
                        title_element = job_element.query_selector('a[data-testid="job-item-title"]')
                        company_element = job_element.query_selector('span[data-at="job-item-company-name"]')
                        location_element = job_element.query_selector('span[data-at="job-item-location"]')

                        title = title_element.inner_text().strip() if title_element else "N/A"
                        job_url = title_element.get_attribute('href') if title_element else None
                        company = company_element.inner_text().strip() if company_element else "N/A"
                        job_location = location_element.inner_text().strip() if location_element else "N/A"


                        # Ensure URL is absolute
                        if job_url and not job_url.startswith('http'):
                             base = "https://www.stepstone.de" # Base URL for relative paths
                             job_url = f"{base}{job_url}"

                        if job_url and job_url not in processed_urls: # Check URL validity and duplication
                            job_data = {
                                "title": title,
                                "company": company,
                                "location": job_location,
                                "url": job_url,
                                "source": "StepStone"
                            }
                            job_list.append(job_data)
                            processed_urls.add(job_url)
                            new_jobs_found_this_page += 1
                            logging.debug(f"Extracted job: {title} at {company}")
                        elif not job_url:
                             logging.warning("Could not extract job URL from a card.")
                        # else: # Optional: log skipped duplicates

                    logging.info(f"Added {new_jobs_found_this_page} new jobs from page {current_page}. Total: {len(job_list)}")

                    if new_jobs_found_this_page == 0 or len(job_elements) < 10:
                        logging.info("Found fewer jobs than expected or no new jobs, assuming end of results.")
                        break

                    current_page += 1
                    time.sleep(random.uniform(2, 5))

                except Exception as e:
                    logging.error(f"Error scraping page {current_page} ({search_url}): {e}")
                    break

            logging.info(f"Finished scraping loop. Total jobs found: {len(job_list)}")
            try:
                 page.close()
                 context.close()
                 browser.close()
            except Exception as close_err:
                 logging.error(f"Error closing playwright resources: {close_err}")

        return job_list[:max_results]

    def get_job_details(self, job_url):
        """
        Fetches detailed information (primarily description) for a single StepStone job posting.
        Uses BeautifulSoup to clean the extracted description HTML.

        Args:
            job_url (str): The URL of the StepStone job posting.

        Returns:
            dict: A dictionary containing the job URL and cleaned description text (or None if failed).
        """
        details = {"url": job_url, "description": None}
        logging.info(f"Fetching details for job: {job_url}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            page = context.new_page()

            try:
                page.goto(job_url, wait_until="load", timeout=90000)
                # Increased initial wait slightly in case content loads slower
                time.sleep(random.uniform(4, 7))

                # --- Try to find the description element using multiple selectors with waits ---
                description_selectors = [
                    # Prioritize selectors likely containing the main text content area
                    "div[data-at='job-ad-content']", # Common content wrapper
                    "div#job-ad-content",           # Another common ID
                    "article[data-at='job-description']", # Sometimes used
                    ".job-description",             # Generic class
                    "main[data-genesis-element='BASE']", # Fallback to main element
                    "div[data-at='content-container']" # Broader container as last resort
                ]
                description_html = None
                description_element = None # Keep track of the element itself
                for i, selector in enumerate(description_selectors):
                    logging.debug(f"Attempting to find description with selector: {selector}")
                    try:
                        element = page.locator(selector).first
                        # Wait longer for the element to be attached to the DOM
                        element.wait_for(state="attached", timeout=10000) # Wait up to 10 seconds
                        html_content = element.inner_html(timeout=5000) # Get inner HTML
                        
                        # Basic check: Does it contain common text tags and avoid being JUST script?
                        if html_content and ('<p>' in html_content or '<li>' in html_content) and '<script>' not in html_content[:200]: 
                            logging.info(f"Found description content using selector: {selector}")
                            description_html = html_content
                            description_element = element # Store the element for later use
                            break # Found good content, stop trying selectors
                        else:
                             logging.debug(f"Selector {selector} found, but content seems script-heavy, empty, or lacks text tags.")
                             description_html = None # Reset if content wasn't good
                    except Exception as e:
                        logging.debug(f"Selector {selector} failed: {e}")
                        pass # Try next selector
                # --- END: Try to find the description element ---

                if description_html:
                    # --- Clean the HTML using BeautifulSoup ---
                    try:
                        soup = BeautifulSoup(description_html, 'html.parser')
                        
                        # Remove script and style elements
                        for script_or_style in soup(['script', 'style']):
                            script_or_style.decompose()

                        # Attempt to find the core text container more precisely if possible
                        # This selector targets the divs typically holding the rich text sections in the example JSON
                        text_sections = soup.select("div[data-genesis-element='CARD_CONTENT'] span[data-genesis-element='TEXT']")
                        
                        if text_sections:
                             cleaned_texts = []
                             for section in text_sections:
                                 # Extract text, preserving paragraph breaks reasonably
                                 section_text = section.get_text(separator='\\n', strip=True)
                                 cleaned_texts.append(section_text)
                             details["description"] = "\\n\\n".join(cleaned_texts).strip() # Join sections with double newline
                             logging.info(f"Successfully cleaned description using specific text sections.")
                        else:
                            # Fallback: Get text from the whole initial element if specific sections aren't found
                            logging.warning("Could not find specific text sections, using text from the broader element.")
                            # We need the outer_html of the *element* we found, not just inner_html
                            fallback_html = ""
                            if description_element: 
                                fallback_html = description_element.outer_html(timeout=3000)
                            
                            if fallback_html:
                                fallback_soup = BeautifulSoup(fallback_html, 'html.parser')
                                for script_or_style in fallback_soup(['script', 'style']):
                                    script_or_style.decompose()
                                details["description"] = fallback_soup.get_text(separator='\\n', strip=True)
                            else:
                                # If even the fallback element retrieval fails, use basic text from initial HTML
                                details["description"] = soup.get_text(separator='\\n', strip=True)

                        # Further cleanup: remove excessive blank lines
                        if details["description"]:
                            details["description"] = "\\n".join(line for line in details["description"].splitlines() if line.strip())

                    except Exception as e_bs4:
                        logging.error(f"Error cleaning description HTML with BeautifulSoup: {e_bs4}")
                        # Fallback to raw HTML if cleaning fails badly
                        details["description"] = description_html 
                else:
                    logging.warning(f"Could not extract description from {job_url} using known selectors.")
                    details["description"] = "Description not found."

            except Exception as e:
                logging.error(f"Error scraping details for {job_url}: {e}")
            finally:
                try:
                    page.close()
                    context.close()
                    browser.close()
                except Exception as close_err:
                    logging.error(f"Error closing playwright resources: {close_err}")

        return details

    def save_jobs_to_json(self, jobs_data, filename):
        """Saves a list of job dictionaries to a JSON file."""
        # This method can be reused from LinkedInScraper or placed in a base class/utility module later
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(jobs_data, f, ensure_ascii=False, indent=4)
            logging.info(f"Successfully saved {len(jobs_data)} jobs to {filename}")
        except IOError as e:
            logging.error(f"Error saving jobs to JSON file {filename}: {e}")
        except TypeError as e:
            logging.error(f"Error serializing job data to JSON: {e}")


# Example Usage (Now should work better with correct selectors)
if __name__ == '__main__':
    scraper = StepstoneScraper(headless=False)
    # Update search criteria
    KEYWORDS = "Werkstudent" # <<< CHANGED
    # StepStone URL might work better with specific locations listed
    # We might need separate runs if this format doesn't capture all.
    LOCATION = "Köln, Düsseldorf, Bonn" # <<< CHANGED
    MAX_RESULTS_TEST = 20 # Increase slightly for more results
    MAX_DETAILS_TO_FETCH = 5 # Keep detail fetch limited

    print(f"\n--- Searching StepStone for '{KEYWORDS}' in '{LOCATION}' ---")
    jobs = scraper.search_jobs(
        keywords=KEYWORDS,
        location=LOCATION,
        max_results=MAX_RESULTS_TEST
    )
    print(f"\n--- Found {len(jobs)} initial job results (before detail fetching) ---")

    detailed_jobs = []
    if jobs:
        for i, job in enumerate(jobs):
             print(f"{i+1}. {job.get('title','N/A')} at {job.get('company','N/A')} ({job.get('location','N/A')})")

        print(f"\n--- Fetching details for the first {min(len(jobs), MAX_DETAILS_TO_FETCH)} jobs... ---")
        for i, job in enumerate(jobs[:MAX_DETAILS_TO_FETCH]):
            print(f"Fetching details for job {i+1} ({job.get('url','N/A')})...")
            job_copy = job.copy()
            if job_copy.get('url') and not job_copy['url'].startswith('http'): # Double check URL format
                 base = "https://www.stepstone.de"
                 job_copy['url'] = f"{base}{job_copy['url']}"

            if job_copy.get('url') and job_copy['url'].startswith('http'):
                details = scraper.get_job_details(job_copy['url'])

                if details and details.get("description"):
                    print(f"  ✅ Description found for job {i+1}.")
                    job_copy.update(details)
                    detailed_jobs.append(job_copy)
                else:
                    print(f"  ❌ Failed to get description for job {i+1}.")
                    detailed_jobs.append(job_copy)
            else:
                print(f"  Skipping details for job {i+1} due to missing/invalid URL: {job_copy.get('url','N/A')}")
                detailed_jobs.append(job_copy)

            if i < MAX_DETAILS_TO_FETCH - 1:
                 sleep_time = random.uniform(4, 7)
                 logging.info(f"Waiting {sleep_time:.2f} seconds before next detail request...")
                 time.sleep(sleep_time)

        if detailed_jobs:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_keywords = "".join(c if c.isalnum() else "_" for c in KEYWORDS)
            safe_location = "".join(c if c.isalnum() else "_" for c in LOCATION)
            filename = f"stepstone_jobs_{safe_keywords}_{safe_location}_{timestamp}.json"
            scraper.save_jobs_to_json(detailed_jobs, filename)
        else:
            logging.warning("No jobs with details were collected, skipping save.")

    else:
        print("No jobs found matching the criteria.") 