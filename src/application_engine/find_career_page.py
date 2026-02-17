# src/application_engine/find_career_page.py
import json
import os
import logging
import shlex
import requests
from urllib.parse import urlparse
import re # Import regex for parsing LLM response
import argparse

# Import Gemini client functions
from ..personalizer.gemini_client import generate_text, configure_gemini, gemini_model

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Environment Variable Setup ---
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")

# Check for Google Search keys
search_keys_ok = True
if not GOOGLE_SEARCH_API_KEY:
    logging.warning("GOOGLE_SEARCH_API_KEY environment variable not found.")
    search_keys_ok = False
if not GOOGLE_CSE_ID:
    logging.warning("GOOGLE_CSE_ID environment variable not found.")
    search_keys_ok = False

# Check for Gemini key by attempting configuration if needed
if not gemini_model:
    configure_gemini() # Attempt configuration
gemini_ok = gemini_model is not None # Check if model was successfully configured
if not gemini_ok:
    logging.warning("Gemini client is not configured (check GEMINI_API_KEY).")

# --- Helper Functions ---

def load_json_data(file_path: str) -> list | None:
    """Loads job data from a JSON file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            logging.info(f"Successfully loaded {len(data)} jobs from {file_path}")
            return data
        else:
            logging.error(f"JSON data in {file_path} is not a list.")
            return None
    except FileNotFoundError:
        logging.error(f"JSON file not found: {file_path}")
        return None
    except json.JSONDecodeError:
        logging.error(f"Failed to decode JSON from {file_path}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred loading {file_path}: {e}")
        return None

# --- Core Logic ---

def construct_search_query(company: str, title: str) -> str:
    """
    Constructs a Google search query string to find a company's career page for a specific job.

    Args:
        company (str): The company name.
        title (str): The job title.

    Returns:
        str: A formatted Google search query string.
    """
    # Use shlex.quote to handle potential special characters or quotes
    quoted_company = shlex.quote(company)
    quoted_title = shlex.quote(title)

    # Combine terms, adding "careers" or "jobs" for better targeting
    query = f'{quoted_company} {quoted_title} careers OR jobs'
        
    logging.info(f"Constructed search query: {query}")
    return query

def google_search(query: str, api_key: str, cse_id: str, num_results: int = 10) -> list[dict] | None:
    """
    Performs a Google Custom Search and returns results.

    Args:
        query (str): The search query.
        api_key (str): Google Custom Search API Key.
        cse_id (str): Programmable Search Engine ID.
        num_results (int): Number of results to request (max 10 per query).

    Returns:
        list[dict] | None: A list of search result items, or None if an error occurred.
    """
    if not api_key or not cse_id:
        logging.error("Google Search API Key or CSE ID is missing. Cannot perform search.")
        return None

    search_url = "https://www.googleapis.com/customsearch/v1"
    # Ensure we don't request more than 10
    num_to_request = min(num_results, 15)
    params = {
        'key': api_key,
        'cx': cse_id,
        'q': query,
        'num': num_to_request,
        'cr': 'countryDE'
    }

    try:
        logging.info(f"Performing Google Search for: '{query}' (Requesting {params['num']} results, Region: DE)")
        response = requests.get(search_url, params=params, timeout=20)
        response.raise_for_status()
        results = response.json()
        found_items = results.get('items', [])
        logging.info(f"Google Search successful. Received {len(found_items)} items.")
        return found_items
    except requests.exceptions.Timeout:
        logging.error("Google Search request timed out.")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Google Search request failed: {e}")
        if e.response is not None:
            logging.error(f"Response status: {e.response.status_code}")
            try: logging.error(f"Response body: {e.response.json()}")
            except json.JSONDecodeError: logging.error(f"Response body: {e.response.text}")
        return None
    except json.JSONDecodeError:
        logging.error("Failed to decode JSON response from Google Search API.")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred during Google Search: {e}")
        return None

def format_search_results_for_llm(search_results: list[dict]) -> str:
    """Formats search results into a string suitable for an LLM prompt."""
    formatted = ""
    for i, result in enumerate(search_results, 1):
        title = result.get('title', 'N/A')
        link = result.get('link', 'N/A')
        snippet = result.get('snippet', 'N/A').replace('\n', ' ') # Remove newlines from snippet
        formatted += f"{i}. Title: {title}\n   Link: {link}\n   Snippet: {snippet}\n\n"
    return formatted.strip()

def select_link_with_gemini(search_results: list[dict], company: str, title: str) -> str | None:
    """
    Asks Gemini to select the best career page link from search results.
    Returns the selected URL string or None.
    """
    if not gemini_ok:
        logging.error("Gemini client is not configured. Cannot select link.")
        return None

    if not search_results:
        logging.warning("No search results provided to Gemini.")
        return None

    formatted_results = format_search_results_for_llm(search_results)

    # Use the new detailed prompt provided by the user
    prompt = f"""
You are an AI agent tasked with analyzing a list of Google search result URLs for the job title \"{{title}}\" at \"{{company}}\", based on a provided job description.

Your goal is to identify the single most relevant URL that leads directly to the official job posting — either on the company's own career page or on their Applicant Tracking System (ATS). This task is focused on positions in Germany, so German-language domains and platforms should be taken into account.

✅ Prioritize URLs that:
Are hosted on the company's official domain, including subdomains like:

karriere.{{company}}.de

{{company}}.de/jobs

{{company}}.com/careers

Redirect to known Applicant Tracking System (ATS) platforms, including:

workday.com

join.com

lever.co

greenhouse.io

icims.com

taleo.net

smartrecruiters.com

onlyfy.jobs (Prescreen)

softgarden.io

jobbase.io

Clearly point to a specific job posting, preferably containing the job title or a job ID in the URL.

🚫 Avoid or deprioritize URLs from:
General or third-party job boards and aggregators, such as:

linkedin.com

stepstone.de

xing.com

indeed.com

kimeta.de

arbeitsagentur.de

meinestadt.de

monster.de

jobrapido.de

jobware.de

Any URL with excessive tracking parameters (e.g. utm, trk, ref, click)

🔁 Redirect Handling:
For each URL, resolve all HTTP redirects (follow 301/302/307 chains).

Analyze the final destination URL.

Apply the above inclusion/exclusion rules only to the final landing page.

If a URL ultimately leads to a third-party job board, exclude it.

Search Results:
---
{formatted_results}
---

🎯 Output:
Return only one URL — the most credible and direct link to the official job posting on the company's own domain or ATS platform.
"""

    logging.info("Asking Gemini to select the best application link...")
    response_text = generate_text(prompt)

    # --- Log the text returned by generate_text (which might be None) --- 
    logging.info(f"Value returned by generate_text: {response_text}")

    # Log the raw response if it's not None/empty
    if response_text:
        logging.info(f"Raw response string from Gemini: '{response_text.strip()}'")
    else:
        logging.error("Did not receive a response from Gemini for link selection.")
        return None

    # --- Attempt to extract URL (check response_text again) --- 
    if not response_text:
        logging.warning("Cannot extract URL because response text is empty or None.")
        return None
        
    url_match = re.search(r'https?://[\S]+', response_text.strip())
    if url_match:
        selected_url = url_match.group(0)
        logging.info(f"Extracted URL from Gemini response: {selected_url}")
        if '.' in selected_url and '//' in selected_url:
            return selected_url
        else:
            logging.warning(f"Extracted URL seems invalid: {selected_url}")
            return None
    else:
        logging.warning(f"Could not extract a URL from Gemini response.")
        return None

# --- Main Function to Find URL ---
def find_career_page_url(company_name: str, title: str) -> str | None:
    """Finds the most likely career page URL for a given company and job title."""
    if not search_keys_ok or not gemini_ok:
        logging.error("Google Search or Gemini is not configured. Cannot find career page.")
        return None

    logging.info(f"Attempting to find career page for: {title} at {company_name}")
    search_query = construct_search_query(company_name, title)
    # Use num_results=10 as before
    search_results = google_search(search_query, GOOGLE_SEARCH_API_KEY, GOOGLE_CSE_ID, num_results=10) 

    if search_results:
        # --- Print the fetched Google Search Results (including snippets) --- 
        print("\n--- Full Google Search Results Sent to Gemini ---")
        for i, item in enumerate(search_results):
            print(f"{i+1}. Title: {item.get('title', 'N/A')}")
            print(f"   Link:  {item.get('link', 'N/A')}")
            print(f"   Snippet: {item.get('snippet', 'N/A').replace('\n', ' ')}") # Print snippet
            print("---")
        print("-------------------------------------------")
        # ----------------------------------------- 
        
        career_page_url = select_link_with_gemini(search_results, company_name, title)
        if career_page_url:
             logging.info(f"Gemini selected career page URL: {career_page_url}")
             return career_page_url
        else:
             logging.warning(f"Gemini failed to select a suitable career page URL for '{title}' at '{company_name}'.")
             return None
    else:
        logging.warning(f"Google Search failed or returned no results for '{title}' at '{company_name}'.")
        return None

# --- Argument Parser --- 
def setup_arg_parser():
    parser = argparse.ArgumentParser(description='Find company career page URL using Google Search and Gemini.')
    parser.add_argument('--company', type=str, help='Company name for the job search.')
    parser.add_argument('--title', type=str, help='Job title for the job search.')
    parser.add_argument('--json-file', type=str, help='Optional: Path to a specific JSON job file to use (overrides latest file search).')
    parser.add_argument('--job-index', type=int, default=0, help='Index of the job within the JSON file to use (default: 0).')
    return parser

# --- Example Usage ---
if __name__ == '__main__':
    parser = setup_arg_parser()
    args = parser.parse_args()

    logging.info("Starting Find Career Page example (Gemini Link Selection)...")

    if not search_keys_ok:
         logging.error("Missing Google Search API Key or CSE ID. Aborting.")
         exit()
    if not gemini_ok:
         logging.error("Missing or invalid Gemini API Key. Aborting.")
         exit()
    
    company_name = None
    job_title = None

    # --- Determine Company and Title --- 
    if args.company and args.title:
        # Prioritize command line arguments
        company_name = args.company
        job_title = args.title
        logging.info(f"Using company '{company_name}' and title '{job_title}' from command line arguments.")
    else:
        # Fallback: Find and use job file
        job_file_to_use = args.json_file # Use specified file if provided
        if not job_file_to_use:
            # Find latest job file if none specified
            try:
                output_files = sorted([f for f in os.listdir('.') if f.startswith('linkedin_jobs') and f.endswith('.json')])
                if not output_files: output_files = sorted([f for f in os.listdir('.') if f.startswith('stepstone_jobs') and f.endswith('.json')])
                if not output_files: raise FileNotFoundError("No LinkedIn or StepStone JSON output files found in the root directory and no arguments/file specified.")
                job_file_to_use = output_files[-1]
                logging.info(f"Using latest job file: {job_file_to_use}")
            except FileNotFoundError as e: 
                logging.error(f"{e} Please run a scraper first or provide --company and --title arguments.")
            except Exception as e: 
                logging.error(f"Error finding latest job file: {e}")
        
        if job_file_to_use:
            jobs = load_json_data(job_file_to_use)
            if jobs:
                job_index_to_test = args.job_index
                if 0 <= job_index_to_test < len(jobs):
                    job_to_test = jobs[job_index_to_test]
                    company_name = job_to_test.get("company")
                    job_title = job_to_test.get("title")
                else:
                    logging.error(f"Job index {job_index_to_test} is out of bounds for file {job_file_to_use}.")
            else:
                 logging.error(f"Failed to load jobs from {job_file_to_use}.")
        else:
             logging.error("Could not determine job file to use.")


    # --- Construct Query, Search, and Select Link with Gemini ---
    # Ensure we have a company name and job title before proceeding
    if company_name and job_title:
        # Load description as well if available
        description = None
        if 'job_to_test' in locals(): # Check if we loaded from JSON
            description = job_to_test.get("description")
        elif args.company and args.title: # If using args, we don't have description
            # logging.warning("Running with command-line company/title. No description available for search query enhancement.")
            pass # Description not needed for query anymore

        # --- Call the main function --- 
        selected_url = find_career_page_url(company_name, job_title) # Remove description

        if selected_url:
            print(f"\n--> Career page URL found:")
            print(f"    {selected_url}")
        else:
            print(f"\n--> Failed to find a suitable career page URL.")
            
        # --- Original logging moved inside the function or removed/simplified ---
        # print(f"\nJob Details Used:")
        # print(f"  Company: {company_name}")
        # print(f"  Title:   {job_title}")
        # search_query = construct_search_query(company_name, job_title)
        # search_results = google_search(search_query, GOOGLE_SEARCH_API_KEY, GOOGLE_CSE_ID, num_results=10)
        # if search_results:
        #     print("\n--- Fetched Google Search Results ---")
        #     for i, item in enumerate(search_results):
        #         print(f"{i+1}. Title: {item.get('title')}")
        #         print(f"   Link:  {item.get('link')}")
        #     print("-----------------------------------")
        #     career_page_url = select_link_with_gemini(search_results, company_name, job_title)
        #     if career_page_url:
        #          print(f"\n--> Gemini selected career page URL:")
        #          print(f"    {career_page_url}")
        #     else:
        #          print(f"\n--> Gemini failed to select a suitable career page URL from search results.")
        # else:
        #     print(f"\n--> Google Search failed or returned no results.")
    else:
         logging.error("Could not determine company and job title to search for. Aborting.")

    logging.info("Find Career Page example finished.") 