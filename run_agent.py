import os
import logging
import argparse
import json
import re
import pypdf # Import pypdf
from urllib.parse import urlparse
from datetime import datetime

# Import agent components
from src.personalizer.gemini_client import configure_gemini, suggest_job_titles_from_resume
from src.scrapers.linkedin_scraper import LinkedInScraper
from src.scrapers.stepstone_scraper import StepstoneScraper
from src.application_engine.find_career_page import find_career_page_url, load_json_data
from src.application_engine.apply_on_site import apply_to_job, APPLICANT_DATA

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Constants ---
# Define target locations as a list
DEFAULT_SEARCH_LOCATIONS = ["Köln", "Düsseldorf", "Bonn"]
BASE_RESUME_PATH = "data/ann.pdf"
OUTPUT_DIR = "output/scrape_results"

# --- Argument Parser ---
def setup_main_parser():
    parser = argparse.ArgumentParser(description='Job Application Agent End-to-End Runner.')
    parser.add_argument('--job-index', type=int, default=0, 
                        help='Index of the job to process from the generated combined JSON file (default: 0).')
    # Add argument to override locations if needed
    parser.add_argument('--locations', type=str, nargs='+', default=DEFAULT_SEARCH_LOCATIONS,
                        help=f'Locations for job search (space-separated, default: {" ".join(DEFAULT_SEARCH_LOCATIONS)}).')
    parser.add_argument('--headless', action='store_true', 
                        help='Run the browser in headless mode (no GUI).')
    parser.add_argument('--profile-dir-base', type=str, default='playwright_profiles',
                        help='Base directory to store persistent browser profiles.')
    parser.add_argument('--max-results-per-combination', type=int, default=5, # Lower default per combo
                         help='Max jobs to scrape per suggested title AND location combination.')
    parser.add_argument('--num-titles-to-suggest', type=int, default=3, # Match gemini client default
                        help='Number of job titles for Gemini to suggest based on resume.')
    return parser

# --- Resume Reader ---
def read_resume_text(pdf_path):
    """Reads text content from a PDF file."""
    try:
        with open(pdf_path, 'rb') as f:
            reader = pypdf.PdfReader(f)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
        logging.info(f"Successfully read text from resume: {pdf_path}")
        return text
    except FileNotFoundError:
        logging.error(f"Resume file not found: {pdf_path}")
        return None
    except Exception as e:
        logging.error(f"Error reading resume PDF {pdf_path}: {e}")
        return None

# --- Helper: Save Combined Jobs ---
def save_combined_jobs(jobs, filename):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, ensure_ascii=False, indent=4)
        logging.info(f"Saved {len(jobs)} combined jobs to {filepath}")
        return filepath
    except Exception as e:
        logging.error(f"Error saving combined jobs to {filepath}: {e}")
        return None

# --- Helper to get a profile directory name ---
def get_profile_dir_name(url_or_company: str, base_dir: str) -> str:
    """Creates a reasonably safe directory name from a URL or company name."""
    name = url_or_company
    try:
        # Try parsing as URL first
        parsed_url = urlparse(url_or_company)
        # Use domain name (e.g., tietelent.com)
        name = parsed_url.netloc.replace('www.', '').split(':')[0] 
    except Exception:
        pass # If not a valid URL, use the string as is (likely company name)

    # Sanitize the name for directory usage
    safe_name = "".join(c if c.isalnum() or c in ('-', '_') else '_' for c in name)
    if not safe_name: # Handle empty cases
        safe_name = "default_profile"
    
    profile_path = os.path.join(base_dir, safe_name)
    # Ensure the base directory and profile directory exist
    os.makedirs(profile_path, exist_ok=True) 
    return profile_path

# --- Helper to extract potential hiring company from description ---
def extract_hiring_company(description: str, original_company: str) -> str | None:
    """Attempts to extract the actual hiring company name from the description text."""
    if not description: return None

    # Simple cleaning of HTML for better matching
    text_content = re.sub('<[^>]+>', ' ', description)
    text_content = re.sub('\\s+', ' ', text_content).strip()

    # Regex patterns to find potential company names (German/English)
    # Looks for capitalized words after certain prepositions/phrases.
    # This is a basic heuristic and might need refinement.
    patterns = [
        r'(?:bei|für|at|von|for)\s+([A-Z][A-Za-z]+(?:\s+(?:GmbH|AG|Inc|Ltd|SE|KG))?(?:\s+[A-Z][A-Za-z]+)*)', # Preposition + Name (optional legal form)
        r'([A-Z][A-Za-z]+(?:\s+(?:GmbH|AG|Inc|Ltd|SE|KG))?(?:\s+[A-Z][A-Za-z]+)*)\s+(?:sucht|is hiring|seeks|employs)', # Name + Verb
        r'Welcome to\s+([A-Z][A-Za-z]+(?:\s+(?:GmbH|AG|Inc|Ltd|SE|KG))?(?:\s+[A-Z][A-Za-z]+)*)', # Welcome to + Name
        r'working at\s+([A-Z][A-Za-z]+(?:\s+(?:GmbH|AG|Inc|Ltd|SE|KG))?(?:\s+[A-Z][A-Za-z]+)*)' # working at + Name
    ]
    
    potential_matches = []
    for pattern in patterns:
        matches = re.findall(pattern, text_content, re.IGNORECASE) # Find all potential names
        for match in matches:
            # Sometimes the regex group captures multiple things, take the first non-empty
            name = match if isinstance(match, str) else next((g for g in match if g), None)
            if name and len(name) > 2: # Basic check for reasonable length
                # Simple normalization (e.g., remove trailing legal forms for comparison)
                normalized_name = name.replace(' GmbH', '').replace(' AG', '').replace(' Inc', '').replace(' Ltd', '').strip()
                # Avoid matching the original company/recruiter name itself
                if original_company and normalized_name.lower() not in original_company.lower() and original_company.lower() not in normalized_name.lower():
                    potential_matches.append(name.strip()) 

    if potential_matches:
        # Basic logic: return the first plausible match found that is different from original
        # More sophisticated logic could score matches or look for frequency
        found_name = potential_matches[0]
        logging.info(f"Found potential hiring company '{found_name}' in description, different from original '{original_company}'.")
        return found_name
       
    return None

# --- Filtering Keywords ---
EXCLUSION_KEYWORDS = [
    'senior', 'lead', 'principal', 'sr.', 'director', # Seniority
    'intern', 'internship', 'praktikant', 'praktikum', # Internships
    'ausbildung', 'auszubildende', # Apprenticeships
]

# --- NEW: Stricter Title Matching Helper ---
def title_matches_suggestion(found_title: str, suggested_title: str) -> bool:
    """Checks if the found title is a plausible match for the suggested Werkstudent role type."""
    if not found_title or not suggested_title:
        return False

    found_lower = found_title.lower()
    suggested_lower = suggested_title.lower()

    # Basic check: Must contain 'werkstudent' or 'working student'
    if 'werkstudent' not in found_lower and 'working student' not in found_lower:
        return False

    # Extract key terms from suggestion (ignoring common/generic words)
    ignore_terms = {'werkstudent', 'working', 'student', '/', 'm', 'w', 'd', 'gn', 'in'}
    suggestion_terms = set(re.findall(r'\b\w+\b', suggested_lower)) - ignore_terms

    if not suggestion_terms: # If suggestion was just "Werkstudent"
        return True # Allow generic match if suggestion was generic

    # Check if *all* key terms from the suggestion are present in the found title
    match = all(term in found_lower for term in suggestion_terms)
    if match:
        logging.debug(f"Title MATCH: '{found_title}' matches suggestion terms {suggestion_terms} from '{suggested_title}'")
    else:
        logging.debug(f"Title NO MATCH: '{found_title}' missing terms {suggestion_terms - set(re.findall(r'\b\w+\b', found_lower))} from '{suggested_title}'")
       
    return match

# --- Main Execution Logic (Modified for Location Loop and Stricter Filtering) ---
if __name__ == '__main__':
    main_parser = setup_main_parser()
    main_args = main_parser.parse_args()

    logging.info("--- Starting Job Application Agent ---")

    if not configure_gemini():
        logging.critical("Gemini API could not be configured. Exiting.")
        exit(1)

    # --- Phase 1: Resume Analysis and Werkstudent Role Suggestion ---
    logging.info("--- Phase 1: Resume Analysis and Werkstudent Role Suggestion ---")
    resume_text = read_resume_text(BASE_RESUME_PATH)
    if not resume_text:
        logging.critical(f"Could not read resume text from {BASE_RESUME_PATH}. Exiting.")
        exit(1)
    
    suggested_werkstudent_roles = suggest_job_titles_from_resume(resume_text, main_args.num_titles_to_suggest) 
    if not suggested_werkstudent_roles:
        logging.critical("Gemini did not suggest any suitable Werkstudent roles. Exiting.")
        exit(1)
    
    logging.info(f"Gemini suggested Werkstudent roles: {suggested_werkstudent_roles}")

    # --- Phase 2: Scraping Jobs (Looping through Locations and Roles) ---
    logging.info("--- Phase 2: Scraping Jobs Based on Suggested Roles and Locations ---")
    all_found_jobs = []
    processed_job_urls = set() # Track URLs globally to avoid duplicates across searches
    linkedin_scraper = LinkedInScraper(headless=main_args.headless)
    stepstone_scraper = StepstoneScraper(headless=main_args.headless)
    target_locations = main_args.locations

    # Outer loop for locations
    for location in target_locations:
        logging.info(f"=== Processing Location: {location} === ")
        # Inner loop for suggested roles
        for i, suggested_role in enumerate(suggested_werkstudent_roles):
            logging.info(f"Searching for role ({i+1}/{len(suggested_werkstudent_roles)}): '{suggested_role}' in '{location}'")
            jobs_found_this_combo = 0
            max_results_per_combo = main_args.max_results_per_combination
            
            # --- LinkedIn Search --- 
            try:
                logging.info(f"Running LinkedIn search for '{suggested_role}' in '{location}'...")
                linkedin_jobs = linkedin_scraper.search_jobs(
                    keywords=suggested_role, 
                    location=location, # Use single location
                    max_results=max_results_per_combo
                )
                if linkedin_jobs:
                    logging.info(f"Found {len(linkedin_jobs)} LinkedIn cards for '{suggested_role}' in '{location}'. Fetching details & filtering...")
                    for job_card in linkedin_jobs:
                        job_url = job_card.get('url')
                        if not job_url or job_url in processed_job_urls:
                            continue # Skip already processed
                        
                        job_title = job_card.get('title', '')
                        # --- STRICTER FILTERING --- 
                        if title_matches_suggestion(job_title, suggested_role) and \
                           not any(excl_kw in job_title.lower() for excl_kw in EXCLUSION_KEYWORDS):
                           
                            details = linkedin_scraper.get_job_details(job_url)
                            if details:
                                job_card.update(details) 
                                if not details.get("description"):
                                     job_card['description'] = "Description not fetched/found."
                            else:
                                 job_card['description'] = "Description fetching failed."
                                
                            all_found_jobs.append(job_card)
                            processed_job_urls.add(job_url)
                            jobs_found_this_combo += 1
                        else:
                            logging.debug(f"Excluding LinkedIn job (failed strict filter or exclusion keyword): {job_title}")
                        # --------------------------
                else:
                    logging.info(f"No LinkedIn jobs found for this combo.")
            except Exception as e_li:
                logging.error(f"Error during LinkedIn search for '{suggested_role}' in '{location}': {e_li}")

            # --- StepStone Search --- 
            try:
                logging.info(f"Running StepStone search for '{suggested_role}' in '{location}'...")
                stepstone_jobs = stepstone_scraper.search_jobs(
                    keywords=suggested_role, 
                    location=location, # Use single location
                    max_results=max_results_per_combo
                )
                if stepstone_jobs:
                    logging.info(f"Found {len(stepstone_jobs)} StepStone cards for '{suggested_role}' in '{location}'. Fetching details & filtering...")
                    for job_card in stepstone_jobs:
                        job_url = job_card.get('url')
                        if not job_url or job_url in processed_job_urls:
                             continue # Skip already processed
                            
                        job_title = job_card.get('title', '')
                         # --- STRICTER FILTERING --- 
                        if title_matches_suggestion(job_title, suggested_role) and \
                           not any(excl_kw in job_title.lower() for excl_kw in EXCLUSION_KEYWORDS):
                              
                            details = stepstone_scraper.get_job_details(job_url)
                            if details and details.get('description') != "Description not found.":
                                job_card.update(details)
                            else:
                                # Still add job even if details fail, but mark description
                                job_card['description'] = "Description not found or fetching failed."
                                logging.debug(f"Missing StepStone details for: {job_title}")
                               
                            all_found_jobs.append(job_card)
                            processed_job_urls.add(job_url)
                            jobs_found_this_combo += 1
                        else:
                             logging.debug(f"Excluding StepStone job (failed strict filter or exclusion keyword): {job_title}")
                        # --------------------------
                else:
                    logging.info(f"No StepStone jobs found for this combo.")
            except Exception as e_ss:
                logging.error(f"Error during StepStone search for '{suggested_role}' in '{location}': {e_ss}")

            logging.info(f"Added {jobs_found_this_combo} suitable jobs for combo: '{suggested_role}' in '{location}'.")

    logging.info(f"=== Finished all location/role combinations === ")

    if not all_found_jobs:
        logging.critical("No suitable jobs found for any suggested Werkstudent role/location combination after filtering. Exiting.")
        exit(1)
    
    # Save combined results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    combined_filename = f"combined_jobs_{timestamp}.json"
    combined_job_file_path = save_combined_jobs(all_found_jobs, combined_filename)
    if not combined_job_file_path:
        logging.critical("Failed to save combined job results. Exiting.")
        exit(1)
    # else: # If user specified a JSON file directly
    #     combined_job_file_path = main_args.json_file
    #     logging.info(f"Using specified job file: {combined_job_file_path}")
    # --- << END NEW WORKFLOW >> ---

    logging.info(f"--- Scraping and Job Collection Complete --- ")
    logging.info(f"Combined job data saved to: {combined_job_file_path}")
    logging.info(f"Total jobs collected: {len(all_found_jobs)}")
    logging.info("Exiting as requested before application phase.")
    exit(0) # Exit successfully after collecting jobs

    # --- APPLICATION PHASES (COMMENTED OUT / REMOVED) ---
    # # 1. Determine Job to Process (Now uses the combined file)
    # company_name = None
    # job_title = None
    # job_description = None
    # source_file = combined_job_file_path # Use the path of the generated file
    # 
    # # --- Modified Job Loading --- 
    # if not source_file or not os.path.exists(source_file):
    #     logging.error(f"Combined job file not found or not specified: {source_file}. Exiting")
    #     exit(1)
    # 
    # logging.info(f"Loading jobs from combined file: {source_file}")
    # jobs = load_json_data(source_file) 
    # if not jobs:
    #     logging.error(f"Failed to load jobs from {source_file} or file is empty. Exiting.")
    #     exit(1)
    # 
    # job_index = main_args.job_index
    # if 0 <= job_index < len(jobs):
    #     job_to_process = jobs[job_index]
    #     company_name = job_to_process.get("company")
    #     job_title = job_to_process.get("title")
    #     job_description = job_to_process.get("description")
    #     logging.info(f"Selected job index {job_index} from {source_file}:")
    #     logging.info(f"  Company (from JSON): {company_name}")
    #     logging.info(f"  Title:   {job_title}")
    #     # ... (rest of the effective_company_name logic remains the same)
    #     effective_company_name = company_name
    #     if job_description:
    #         hiring_co_from_desc = extract_hiring_company(job_description, company_name)
    #         if hiring_co_from_desc:
    #             logging.info(f"Using company name found in description: {hiring_co_from_desc}")
    #             effective_company_name = hiring_co_from_desc
    #         else:
    #             logging.info("No different hiring company found... Using original.")
    #     else:
    #         logging.info("No description available...")
    # else:
    #     logging.error(f"Job index {job_index} is out of bounds for file {source_file} (contains {len(jobs)} jobs). Exiting.")
    #     exit(1)
    # # --- End Modified Job Loading ---
    # 
    # if not company_name or not job_title:
    #     logging.error("Could not determine company and job title from selected job. Exiting.")
    #     exit(1)
    # 
    # # 2. Find Career Page URL (remains the same)
    # logging.info("--- Phase 3: Finding Career Page URL ---") # Renumbered phase
    # career_page_url = find_career_page_url(effective_company_name, job_title)
    # 
    # if not career_page_url:
    #     logging.error(f"Failed to find a suitable career page URL for '{job_title}' at '{effective_company_name}'. Stopping application attempt for this job.")
    #     # Decide whether to exit or continue with next job (if implemented later)
    #     exit(1) 
    # 
    # # 3. Apply on Site (remains the same)
    # logging.info(f"--- Phase 4: Attempting Application via URL: {career_page_url} ---") # Renumbered phase
    # profile_dir = get_profile_dir_name(effective_company_name, main_args.profile_dir_base)
    # logging.info(f"Using browser profile directory: {profile_dir}")
    # 
    # final_page_url = apply_to_job(
    #     career_page_url=career_page_url,
    #     target_job_title=job_title,
    #     applicant_data=APPLICANT_DATA, 
    #     headless=main_args.headless,
    #     user_data_dir=profile_dir 
    # )
    # 
    # if final_page_url:
    #     logging.info(f"--- Application process finished for '{job_title}'. Final URL: {final_page_url} ---")
    # else:
    #     logging.error(f"--- Application process failed or did not complete for '{job_title}' at '{effective_company_name}' ---")
    # 
    # logging.info("--- Job Application Agent Finished ---") 