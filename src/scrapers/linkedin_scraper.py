# src/scrapers/linkedin_scraper.py
from playwright.sync_api import sync_playwright
import time
import logging
import random # Added for random delays
import json               # <-- Add import
from datetime import datetime # <-- Add import

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class LinkedInScraper:
    """
    Scraper for extracting job postings from LinkedIn.
    """
    BASE_URL = "https://www.linkedin.com/jobs/search/"

    def __init__(self, headless=True):
        self.headless = headless
        # We'll initialize the browser context later

    def _build_search_url(self, keywords, location, start=0, days_posted_ago=None):
        """
        Builds the LinkedIn job search URL with optional filters.

        Args:
            keywords (str): Job title, skill, or company.
            location (str): Geographical area.
            start (int): Starting index for results pagination.
            days_posted_ago (int, optional): Filter jobs posted within the last X days. Defaults to None (no filter).

        Returns:
            str: The constructed LinkedIn job search URL.
        """
        # Basic params
        params = {
            "keywords": keywords,
            "location": location,
            "start": start,
        }

        # Add time filter if specified
        if days_posted_ago is not None and isinstance(days_posted_ago, int) and days_posted_ago > 0:
            seconds_ago = days_posted_ago * 24 * 60 * 60
            params["f_TPR"] = f"r{seconds_ago}" # Time Posted Range filter

        # TODO: Add other filters like experience level (f_E), job type (f_JT), remote (f_WT)

        # URL encode parameters (Playwright usually handles this, but good practice)
        # For simplicity here, we'll let Playwright handle encoding for now.
        # A more robust solution would use urllib.parse.urlencode

        # Construct URL query string
        query_string_parts = []
        for key, value in params.items():
            # Simple encoding for keywords and location might be needed if not handled by Playwright
            if key in ["keywords", "location"] and isinstance(value, str):
                # Basic space encoding - a proper library is better
                encoded_value = value.replace(" ", "%20")
                query_string_parts.append(f"{key}={encoded_value}")
            else:
                query_string_parts.append(f"{key}={value}")

        query_string = "&".join(query_string_parts)
        url = f"{self.BASE_URL}?{query_string}"

        logging.info(f"Constructed search URL: {url}")
        return url

    def search_jobs(self, keywords, location, max_results=25, days_posted_ago=None):
        """
        Searches for jobs on LinkedIn based on keywords and location.

        Args:
            keywords (str): Job title, skill, or company.
            location (str): Geographical area.
            max_results (int): Maximum number of job results to fetch.
            days_posted_ago (int, optional): Filter jobs posted within the last X days.

        Returns:
            list: A list of job dictionaries, each containing basic info.
        """
        job_list = []
        start_index = 0 # Only used for initial URL, scrolling handles subsequent loading
        processed_urls = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            page = browser.new_page()
            logging.info(f"Launched browser (headless={self.headless})")

            # Initial navigation - pass days_posted_ago to _build_search_url
            search_url = self._build_search_url(keywords, location, start=start_index, days_posted_ago=days_posted_ago)
            logging.info(f"Navigating to initial search URL: {search_url}")
            try:
                page.goto(search_url, wait_until="load", timeout=60000) # Use 'load' or 'networkidle'
                time.sleep(3) # Initial wait
            except Exception as e:
                 logging.error(f"Failed to load initial search page {search_url}: {e}")
                 browser.close()
                 return job_list

            # Scroll and scrape loop
            last_height = page.evaluate("document.body.scrollHeight")
            scroll_attempts = 0
            MAX_SCROLL_ATTEMPTS = 10 # Limit scrolls to avoid infinite loops

            while len(job_list) < max_results and scroll_attempts < MAX_SCROLL_ATTEMPTS:
                logging.info(f"Scraping jobs. Found {len(job_list)}/{max_results} so far.")

                # Scrape currently visible jobs
                # Selector might need adjustment - target the container of job cards
                job_elements = page.query_selector_all('ul.jobs-search__results-list > li div.base-card') # Try a more specific selector path

                logging.info(f"Found {len(job_elements)} potential job card elements after scroll/wait.")

                new_jobs_found_this_pass = 0
                for job_element in job_elements:
                    if len(job_list) >= max_results:
                        break

                    # Use relative selectors from the card element
                    job_url_element = job_element.query_selector('a.base-card__full-link')
                    job_url = job_url_element.get_attribute('href') if job_url_element else None

                    # Skip if no URL or already processed
                    if not job_url or job_url in processed_urls:
                        continue

                    title_element = job_element.query_selector('h3.base-search-card__title')
                    company_element = job_element.query_selector('h4.base-search-card__subtitle a') # Often the company is linked
                    location_element = job_element.query_selector('span.job-search-card__location')

                    title = title_element.inner_text().strip() if title_element else "N/A"
                    # Handle cases where company name might not be in a link
                    company_text = ""
                    if company_element:
                       company_text = company_element.inner_text().strip()
                    else:
                       # Fallback if company is not a link (less common)
                       company_plain_element = job_element.query_selector('h4.base-search-card__subtitle')
                       if company_plain_element:
                           company_text = company_plain_element.inner_text().strip()
                       else:
                           company_text = "N/A"

                    location = location_element.inner_text().strip() if location_element else "N/A"

                    if title != "N/A" and company_text != "N/A" and location != "N/A":
                        job_data = {
                            "title": title,
                            "company": company_text,
                            "location": location,
                            "url": job_url,
                            "source": "LinkedIn"
                        }
                        job_list.append(job_data)
                        processed_urls.add(job_url)
                        new_jobs_found_this_pass += 1
                        logging.debug(f"Extracted job: {job_data['title']} at {job_data['company']}")
                    else:
                        logging.warning(f"Could not extract all details for a job card. URL: {job_url}")


                logging.info(f"Added {new_jobs_found_this_pass} new jobs in this pass. Total: {len(job_list)}")

                if len(job_list) >= max_results:
                    logging.info("Reached max_results limit.")
                    break

                # Scroll down to load more jobs
                logging.info("Scrolling down...")
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(4) # Wait for new content to load after scroll

                # Check if scroll height changed
                new_height = page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    scroll_attempts += 1
                    logging.warning(f"Scroll height didn't change. Attempt {scroll_attempts}/{MAX_SCROLL_ATTEMPTS}. Waiting longer...")
                    time.sleep(5) # Wait longer if height didn't change
                    new_height = page.evaluate("document.body.scrollHeight") # Check again
                    if new_height == last_height:
                         logging.warning("Scroll height still unchanged. Assuming end of results.")
                         break # Exit loop if height doesn't change after longer wait
                    else:
                         scroll_attempts = 0 # Reset attempts if scroll worked
                else:
                    scroll_attempts = 0 # Reset counter if scroll was successful

                last_height = new_height


            # TODO: Implement proper pagination using 'start' parameter if scrolling isn't enough for max_results

            logging.info(f"Finished scraping loop. Total jobs found: {len(job_list)}")
            browser.close()

        return job_list[:max_results]

    def get_job_details(self, job_url):
        """
        Fetches detailed information (primarily description) for a single job posting.

        Args:
            job_url (str): The URL of the LinkedIn job posting.

        Returns:
            dict: A dictionary containing the job URL and description (or None if failed).
                  Example: {"url": "...", "description": "..."}
        """
        details = {"url": job_url, "description": None}
        # Use a single Playwright instance/context if scraping many details later,
        # but for simplicity now, each call launches its own.
        with sync_playwright() as p:
            # Consider using firefox or webkit if chromium has issues
            browser = p.chromium.launch(headless=self.headless)
            # Create a context with a realistic user agent
            context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            page = context.new_page()
            logging.info(f"Fetching details for job: {job_url}")

            try:
                page.goto(job_url, wait_until="load", timeout=90000) # Increased timeout, wait for 'load'
                time.sleep(3) # Wait for dynamic elements

                # --- Attempt to click "Show more" button ---
                # Common selectors for the button (these might change)
                show_more_selectors = [
                    "button[aria-label='Show more, visually expands previously read content below']",
                    "button.show-more-less-html__button--more",
                    "button[data-tracking-control-name='public_jobs_show-more-html-btn']"
                ]
                clicked_show_more = False
                for selector in show_more_selectors:
                    try:
                        show_more_button = page.query_selector(selector)
                        if show_more_button and show_more_button.is_visible():
                            logging.info(f"Found 'Show more' button with selector: {selector}. Clicking...")
                            show_more_button.click(timeout=5000)
                            time.sleep(2) # Wait for content to expand
                            clicked_show_more = True
                            break # Stop trying selectors if one works
                    except Exception as click_err:
                        logging.warning(f"Could not click 'Show more' button ({selector}): {click_err}")
                if not clicked_show_more:
                     logging.info("No 'Show more' button found or clicked.")

                # --- Extract Description ---
                # Common selectors for the description container (these might change)
                description_selectors = [
                    "div.show-more-less-html__markup", # Original guess
                    "div#job-details",                # Often contains the description
                    "section.core-section-container", # Broader container sometimes
                    "div.description__text"           # Another common pattern
                ]
                description_html = None
                for selector in description_selectors:
                    description_element = page.query_selector(selector)
                    if description_element:
                        logging.info(f"Found description element with selector: {selector}")
                        description_html = description_element.inner_html().strip()
                        break # Stop trying selectors if one works

                if description_html:
                    details["description"] = description_html
                    logging.info(f"Successfully extracted description for {job_url}")
                else:
                    logging.warning(f"Could not find description element using known selectors for {job_url}")
                    # As a fallback, capture the whole body? Might be too noisy.
                    # details["description"] = page.content() # Or None

            except Exception as e:
                logging.error(f"Error fetching or processing details for {job_url}: {e}")
                # Log page source for debugging if needed (can be large)
                # try:
                #     logging.debug(f"Page source at time of error:\n{page.content()}")
                # except Exception:
                #     logging.error("Failed to get page content after error.")

            finally:
                # Ensure browser is closed even if errors occur
                try:
                    page.close()
                    context.close()
                    browser.close()
                    logging.debug("Playwright page, context, and browser closed.")
                except Exception as close_err:
                    logging.error(f"Error closing playwright resources: {close_err}")

        return details

    def save_jobs_to_json(self, jobs_data, filename):
        """Saves a list of job dictionaries to a JSON file."""
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(jobs_data, f, ensure_ascii=False, indent=4)
            logging.info(f"Successfully saved {len(jobs_data)} jobs to {filename}")
        except IOError as e:
            logging.error(f"Error saving jobs to JSON file {filename}: {e}")
        except TypeError as e:
            logging.error(f"Error serializing job data to JSON: {e}")

# Example Usage (for testing)
if __name__ == '__main__':
    # Consider running headless=True for faster detail scraping once selectors are stable
    scraper = LinkedInScraper(headless=False) # Keep False for initial testing
    TARGET_DAYS = 7 # Optionally adjust time filter (e.g., last 7 days)
    MAX_JOBS_TO_FETCH_DETAILS = 5 # Limit detail fetching for testing
    KEYWORDS = "Werkstudent" # <<< CHANGED
    # Use a more specific location string, combining options. LinkedIn search might handle this.
    LOCATION = "Köln OR Düsseldorf OR Bonn" # <<< CHANGED

    logging.info(f"Searching for '{KEYWORDS}' jobs in '{LOCATION}' posted within the last {TARGET_DAYS} days.")

    jobs = scraper.search_jobs(
        keywords=KEYWORDS,
        location=LOCATION,
        max_results=20, # Fetch more potential matches
        days_posted_ago=TARGET_DAYS
    )
    print(f"\n--- Found {len(jobs)} jobs posted in the last {TARGET_DAYS} days: ---")

    detailed_jobs = []
    if jobs:
        # Print basic info first
        for i, job in enumerate(jobs):
            print(f"{i+1}. {job['title']} at {job['company']} ({job['location']})")

        # Fetch details for a limited number
        print(f"\n--- Fetching details for the first {min(len(jobs), MAX_JOBS_TO_FETCH_DETAILS)} jobs... ---")
        fetched_details_count = 0
        for i, job in enumerate(jobs[:MAX_JOBS_TO_FETCH_DETAILS]):
            print(f"Fetching details for job {i+1}...")
            job_copy = job.copy()
            details = scraper.get_job_details(job_copy['url'])
            if details and details.get("description"):
                print(f"  ✅ Description found for job {i+1}.")
                job_copy.update(details) # Add description to the job dict
                detailed_jobs.append(job_copy)
                fetched_details_count += 1
            else:
                print(f"  ❌ Failed to get description for job {i+1}. Skipping detail save for this job.")
                detailed_jobs.append(job_copy)

            # Be respectful: add a delay between requests
            if i < MAX_JOBS_TO_FETCH_DETAILS - 1:
                 sleep_time = random.uniform(4, 8) # Random delay between 4-8 seconds
                 logging.info(f"Waiting {sleep_time:.2f} seconds before next detail request...")
                 time.sleep(sleep_time)

        # --- Save the results (including descriptions where available) ---
        if detailed_jobs:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_keywords = "".join(c if c.isalnum() else "_" for c in KEYWORDS)
            safe_location = "".join(c if c.isalnum() else "_" for c in LOCATION)
            filename = f"linkedin_jobs_{safe_keywords}_{safe_location}_{timestamp}.json"

            scraper.save_jobs_to_json(detailed_jobs, filename)
        else:
             logging.warning("No jobs with details were collected, skipping save.")

    else:
        print("No jobs found matching the criteria.")
    # ... (get_job_details example remains commented out)
    # scraper = LinkedInScraper(headless=False) # Run non-headless for easier debugging initially
    # # NOTE: LinkedIn might block frequent non-logged-in searches. Login might be required.
    # jobs = scraper.search_jobs(keywords="Software Engineer", location="Berlin, Germany", max_results=10)
    # print(f"Found {len(jobs)} jobs:")
    # for job in jobs:
    #     print(f"- {job['title']} at {job['company']} ({job['location']}) - {job['url']}")
    #     # Optionally fetch full details (be mindful of rate limits)
    #     # details = scraper.get_job_details(job['url'])
    #     # print(f"  Description found: {details['description'] is not None}")
    #     # time.sleep(2) # Delay between detail fetches 