import logging
import time
import random
import re
import os
import datetime
from io import BytesIO

from dateutil.relativedelta import relativedelta
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph

# Import the Gemini placeholder generator
from ..personalizer.gemini_client import generate_placeholder_doc_text

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
# Add more selectors as needed based on different ATS/career page structures
JOB_LINK_SELECTORS = [
    "a",                # General links
    "li a",             # Links within list items
    "div[role='listitem'] a", # Links in ARIA list items
    "h3 > a",           # Links directly inside h3 tags (like on msr.de)
    "div[class*='job'] a",   # Links within divs likely containing job info
    # Could add selectors specific to platforms like Greenhouse, Lever, etc.
    # e.g., "a[data-qa='posting-name']" (Lever specific example)
]

# Selectors for links/buttons that lead from a general career page to the actual job listings
JOB_PORTAL_LINK_SELECTORS = [
    'a:has-text("Alle Stellen")', 
    'a:has-text("Offene Stellen")', 
    'a:has-text("Stellenangebote")',
    'a:has-text("Jobportal")',
    'a:has-text("Jobs")',
    'a:has-text("Open Positions")',
    'a:has-text("View Jobs")',
    'a:has-text("Search Jobs")',
    'a:has-text("Current Openings")',
    'button:has-text("Alle Stellen")',
    'button:has-text("Offene Stellen")',
    'button:has-text("Zu den Stellenangeboten")', 
    'button:has-text("View Jobs")',
    '[role="button"]:has-text("Jobs")', # Sometimes divs act as buttons
    'a[href*="jobs"], a[href*="career"], a[href*="stellen"]', # Links likely pointing to job sections
    "a:has-text('View All Jobs')",
    "a:has-text('Karriere')",
]

# Selectors for primary "Apply Now" type buttons on job description pages
APPLY_NOW_SELECTORS = [
    'button:has-text("Apply Now")',
    'a:has-text("Apply Now")',
    'button:has-text("Jetzt bewerben")',
    'a:has-text("Jetzt bewerben")',
    'a.btn:has-text("Jetzt bewerben")', # Specific for MSR/onlyfy link
    'a[itemprop="applyUrl"]',          # Specific for MSR/onlyfy link
    'button:has-text("Bewerben")',
    'a:has-text("Bewerben")',
    '[data-testid*="apply-button" i]', # Common test IDs
    '[id*="apply-button" i]',
    'button[class*="apply" i]',
    'a[role="button"]:has-text("Apply")', 
    'a[role="button"]:has-text("Jetzt bewerben")', 
    'button:has-text("Apply")', # General Apply might be ambiguous, try last
    'a:has-text("Apply")',     # General Apply might be ambiguous, try last
]

# --- Placeholder Applicant Data --- 
# !! IMPORTANT: Replace with your actual data or load from a secure config !!
APPLICANT_DATA = {
    "first_name": "Asim",
    "last_name": "Gyawali",
    "email": "iasimsan@gmail.com",
    "phone": "+491748091629", # Example German format
    "salutation_preference": "Herr", # Add preferred salutation (e.g., Herr, Frau, Mx., Mr., Ms.)
    # Path to the resume file (ideally the *tailored* one for the specific job)
    "resume_path": "data/base_resume.pdf",
    # Add path for cover letter (placeholder - will be generated if needed)
    "cover_letter_path": "", # No base cover letter needed now
    # Add Salary Expectation
    "salary_expectation": "62345 EUR", # Added currency
    # Add default Source - will try to select first option anyway
    "source": "LinkedIn" # Or some other default if needed
}

# --- Placeholder Form Field Selectors ---
# !! IMPORTANT: These MUST be verified and adjusted for each target site (e.g., JOIN.com) !!
FORM_SELECTORS = {
    # Keep existing general selectors for now, may need refinement for JOIN.com
    "first_name": 'input[name*="first"][name*="name"], input#firstname, input[data-testid*="first-name"], input[name="firstName"], input[name="vorname"]',
    "last_name": 'input[name*="last"][name*="name"], input#lastname, input[data-testid*="last-name"], input[name="lastName"], input[name="nachname"]',
    "email": 'input[type="email"], input[name*="email"], input#email',
    "phone": 'input[type="tel"], input[name*="phone"], input#phone, input[name="telefon"]',
    # Keep general resume selector, refine if needed - ADDING MORE POSSIBILITIES
    # Prioritize the specific selector found for onlyfy.jobs
    "resume_upload": 'input[name="cv[cv]"], input[type="file"][name*="resume"], input[type="file"][aria-label*="resume"], input[type="file"][data-testid*="resume"], input#resume-upload-input, input[data-testid="resume-upload-input"], input[name="cv"], input[name="lebenslauf"], button:has-text("Upload Resume"), button:has-text("Upload CV"), div:has-text("Upload Resume") input[type="file"], div:has-text("Upload CV") input[type="file"]',
    # Add placeholder for cover letter - NEEDS VERIFICATION
    "cover_letter_upload": 'input[type="file"][name*="cover"], input[type="file"][aria-label*="cover"], input[data-testid*="cover-letter"], input[name="coverLetter"], input[name="anschreiben"], button:has-text("Upload Cover Letter")',
    "cover_letter_text": 'textarea[name*="cover"], textarea[aria-label*="cover"], textarea[name="coverLetter"], textarea[name="anschreiben"]',
    # Use the specific submit button found for JOIN.com - REVERTING TO GENERAL (NEEDS INSPECTION ON LOGGED-IN PAGE)
    "submit_button": 'button[type="submit"], button:has-text("Submit"), button:has-text("Apply"), button:has-text("Bewerbung abschicken"), button:has-text("Jetzt bewerben"), button.sc-brKeYL.dDIeSv', 

    # --- New Selectors (Guesses based on common patterns/German forms) ---
    "salutation": 'select[name*="salutation"], select[name="title"], select[id*="salutation"], select[name="anrede"]',
    # Prioritize specific onlyfy.jobs selectors
    "start_date": 'input[name="questions[cf_224307]"], input[name*="start_date"], input[name*="earliest"], input[name*="available"], input[id*="start_date"], input[placeholder*="Start date"], input[placeholder*="Verfügbar ab"]',
    "salary_expectation": 'input[name="questions[cf_224302]"], input[name*="salary"], input[name*="compensation"], input[id*="salary"], input[placeholder*="Salary"], input[placeholder*="Gehaltsvorstellung"]',
    "source": 'select[name*="source"], select[name*="found"], select[name*="referral"], select[id*="source"], select:near(:text("How did you hear"))', # Text proximity might work
    # Prioritize specific onlyfy.jobs button selector for consent
    "consent_checkbox": 'button[role="checkbox"][id="finish[extended_data_retention]"], input[type="checkbox"][name*="privacy"], input[type="checkbox"][name*="datenschutz"], input[type="checkbox"][id*="privacy"], input[type="checkbox"][id*="consent"], input[type="checkbox"]:near(:text("I hereby agree"))'
}

# --- Constants ---
COOKIE_SELECTORS = [
    "button:has-text('Accept')",
    "button:has-text('Accept')",
    "button:has-text('Alle akzeptieren')", # Common German
    "button:has-text('Akzeptieren')",    # Common German
    "#onetrust-accept-btn-handler",     # OneTrust common ID
]

# --- << NEW: Selectors for Primary Apply Buttons >> ---
# Use simple text first, regex as fallback
PRIMARY_APPLY_SELECTORS_TEXT = [
    "button:has-text('Ich will den Job')", # Specific for 50Hertz (exact match)
    "a:has-text('Ich will den Job')",     # Specific for 50Hertz (exact match)
    "button:has-text('Apply Now')",       # Case-sensitive common
    "a:has-text('Apply Now')",           # Case-sensitive common
    "button:has-text('Jetzt bewerben')", # Case-sensitive common German
    "a:has-text('Jetzt bewerben')",     # Case-sensitive common German
    "button:has-text('Apply')",           # Case-sensitive common
    "a:has-text('Apply')",               # Case-sensitive common
    "button:has-text('Bewerben')",       # Case-sensitive common German
    "a:has-text('Bewerben')",           # Case-sensitive common German
    # Add more exact text matches here
]
PRIMARY_APPLY_SELECTORS_REGEX = [
    # Use python re.compile for case-insensitive matching
    ('button', re.compile(r'^Apply Now$', re.IGNORECASE)),
    ('a', re.compile(r'^Apply Now$', re.IGNORECASE)),
    ('button', re.compile(r'^Jetzt bewerben$', re.IGNORECASE)),
    ('a', re.compile(r'^Jetzt bewerben$', re.IGNORECASE)),
    ('button', re.compile(r'^Apply$', re.IGNORECASE)),
    ('a', re.compile(r'^Apply$', re.IGNORECASE)),
    ('button', re.compile(r'^Bewerben$', re.IGNORECASE)),
    ('a', re.compile(r'^Bewerben$', re.IGNORECASE)),
]
OTHER_PRIMARY_APPLY_SELECTORS = [
    # Non-text based selectors
    "input[type='submit'][value*='Apply']",
    "input[type='button'][value*='Apply']",
    "button[data-testid*='apply']",
    "a[data-testid*='apply']",
]
# ------------------------------------------------------

# --- Core Logic ---

def normalize_text(text: str) -> str:
    """Normalize text for comparison (lowercase, remove extra whitespace/punctuation)."""
    if not text: return ""
    text = text.lower()
    # Simpler regex to remove common trailing/leading chars and extra space
    text = re.sub(r'[\s()/-]+$', '', text).strip() # Remove trailing space/(),-/
    text = re.sub(r'^[^a-z0-9]+', '', text, flags=re.IGNORECASE).strip() # Remove leading non-alphanumeric
    text = re.sub(r'\s+', ' ', text).strip() # Consolidate whitespace
    return text

def find_iframe_if_necessary(page):
    """Checks for known iframes that might contain job lists or forms."""
    # Add known iframe selectors here
    iframe_selectors = [
        '#psJobWidget iframe', # Example from MSR/onlyfy
        # Add other common ATS iframe selectors if known
        'iframe[id*="grnhse_iframe"]', # Greenhouse example
    ]
    
    search_context = page # Default to page
    iframe_locator = None # Default to no iframe

    for selector in iframe_selectors:
        try:
            iframe = page.locator(selector).first
            if iframe.is_visible(timeout=3000): # Quick check
                logging.info(f"Found potential job iframe: {selector}. Switching search context.")
                iframe_locator = page.frame_locator(selector)
                # Quick check if frame content is accessible
                iframe_locator.locator('body').wait_for(state='visible', timeout=5000)
                search_context = iframe_locator
                break # Use the first iframe found
        except PlaywrightTimeoutError:
            logging.debug(f"Iframe selector {selector} not visible or timed out.")
        except Exception as e_iframe:
            logging.warning(f"Error checking iframe selector {selector}: {e_iframe}")
            
    return search_context, iframe_locator

# --- Helper function to find the job link ---
def find_job_link(search_context, target_title):
    """Finds the best matching job link within a given context (Page or FrameLocator)."""
    normalized_target_title = normalize_text(target_title)
    logging.info(f"Searching for job link matching: '{normalized_target_title}' within the provided context.")
    
    best_match = None
    highest_sim = 0.0
    link_text_found = None

    # Prepare keywords
    cleaned_for_keywords = re.sub(r'[()/\\]', ' ', normalized_target_title)
    cleaned_for_keywords = re.sub(r'\s+', ' ', cleaned_for_keywords).strip()
    target_keywords = [word for word in cleaned_for_keywords.split() if len(word) > 1]
    logging.info(f"Target keywords for matching: {target_keywords}")

    # Strategy 1: Direct text match
    try:
        safe_target_title = normalized_target_title.replace('"', '\"').replace("'", "\'") 
        direct_text_selector = f'a:has-text("{safe_target_title}")'
        logging.info(f"Trying direct text selector: {direct_text_selector}")
        text_matches = search_context.locator(direct_text_selector).all()
        logging.info(f"Found {len(text_matches)} potential links via direct text match.")
        for link in text_matches:
            if link.is_visible(timeout=1500):
                 text = link.text_content(timeout=1000)
                 if not text: continue
                 normalized_text = normalize_text(text.strip())
                 keywords_present = all(keyword in normalized_text for keyword in target_keywords)
                 if keywords_present:
                    # Consider this a strong match if keywords are present
                    logging.info(f"Found visible link via direct text/keyword match: '{text.strip()}'")
                    return link # Return immediately if a good direct match is found
        logging.info("Direct text search did not yield a usable visible link.")
    except Exception as e_text_direct:
        logging.warning(f"Error during direct text search: {e_text_direct}")

    # Strategy 2: Structural selectors (Fallback)
    logging.info("Falling back to structural selectors...")
    potential_links = []
    for selector in JOB_LINK_SELECTORS: # Use the global constant
        try:
            elements = search_context.locator(selector).all()
            if elements: potential_links.extend(elements)
        except Exception as e:
            logging.warning(f"Error locating elements with selector '{selector}': {e}")
    
    logging.info(f"Checking {len(potential_links)} potential job links from structural selectors...")
    for link in potential_links:
        try:
            if not link.is_visible(timeout=1000): continue
            text = link.text_content(timeout=1000)
            if not text: continue
            normalized_text = normalize_text(text.strip())
            logging.debug(f"Checking link text: '{normalized_text}'")
            keywords_present = all(keyword in normalized_text for keyword in target_keywords)
            if keywords_present:
                sim = len(normalized_target_title) / len(normalized_text) if normalized_text else 0
                if sim > highest_sim:
                    highest_sim = sim
                    best_match = link
                    link_text_found = text.strip()
        except Exception as e_check:
            logging.debug(f"Error checking link: {e_check}")
            pass
    
    if best_match:
        logging.info(f"Found best matching job link via structural search: '{link_text_found}' (Similarity: {highest_sim:.2f})")
        return best_match
    else:
        logging.info(f"No job link found matching '{normalized_target_title}' via any method.")
        return None

# --- PDF Generation Helper (using reportlab) ---
def generate_placeholder_pdf(filepath: str, text_content: str):
    """Creates a simple PDF file with the given text content using reportlab."""
    try:
        output_dir = os.path.dirname(filepath)
        os.makedirs(output_dir, exist_ok=True)
        
        c = canvas.Canvas(filepath, pagesize=letter)
        styles = getSampleStyleSheet()
        style = styles['Normal']
        style.fontSize = 10
        
        # Simple wrapping - adjust margins as needed
        text_width = letter[0] - 100 # Width (page width - margins)
        text_height = letter[1] - 100 # Height (page height - margins)
        
        p = Paragraph(text_content.replace('\n', '<br/>'), style)
        p.wrapOn(c, text_width, text_height) 
        # Draw higher up on the page
        p.drawOn(c, 50, text_height - p.height + 50) 
        
        c.save()
        logging.info(f"Generated placeholder PDF with text: {filepath}")
        return True
    except Exception as e:
        logging.error(f"Failed to generate placeholder PDF {filepath} using reportlab: {e}")
        return False

# --- Form Filling Logic ---
def is_field_mandatory(element) -> bool:
    """Checks if a form element is likely mandatory (basic heuristic)."""
    try:
        # Check for required attribute
        if element.get_attribute('required'):
            return True
        # Check for aria-required attribute
        if element.get_attribute('aria-required') == 'true':
            return True
        # Check for common mandatory classes (site-specific)
        # class_attr = element.get_attribute('class')
        # if class_attr and ('required' in class_attr or 'mandatory' in class_attr):
        #     return True
        # Check for visible asterisk (*) in a nearby label
        input_id = element.get_attribute('id')
        if input_id:
            # Find the label associated with this input id
            label_selector = f'label[for="{input_id}"]'
            label = element.page.locator(label_selector).first
            # Check if label exists, is visible, and contains an asterisk
            try:
                if label.is_visible(timeout=2000): # Increased timeout
                    label_text = label.text_content(timeout=2000) # Increased timeout
                    if label_text and '*' in label_text:
                        logging.info(f"Detected mandatory field '{input_id}' via label asterisk.")
                        return True
                    else:
                        logging.debug(f"Label found for '{input_id}', but no asterisk in text: '{label_text}'")
                else:
                    logging.debug(f"Label found for '{input_id}' but not visible.")
                
                # --- NEW: Check for sibling span with asterisk --- 
                sibling_span_selector = f'label[for="{input_id}"] + span'
                logging.debug(f"Checking for sibling span: {sibling_span_selector}")
                sibling_span = element.page.locator(sibling_span_selector).first
                if sibling_span.is_visible(timeout=2000):
                    span_text = sibling_span.text_content(timeout=2000)
                    if span_text and '*' in span_text:
                        logging.info(f"Detected mandatory field '{input_id}' via sibling span asterisk.")
                        return True
                    else:
                        logging.debug(f"Sibling span found for '{input_id}', but no asterisk in text: '{span_text}'")
                else:
                     logging.debug(f"Sibling span found for '{input_id}' but not visible.")
                # --- END NEW SIBLING CHECK --- 
                 
            except PlaywrightTimeoutError:
                 logging.debug(f"Label or sibling span lookup timed out for id: {input_id}")
            except Exception as e_label:
                logging.debug(f"Error checking label[for='{input_id}']: {e_label}")
        else:
            logging.debug(f"Input element does not have an ID attribute.")
            
        # --- NEW: Check label[for=NAME] and its sibling span --- 
        input_name = element.get_attribute('name')
        if input_name:
            label_for_name_selector = f'label[for="{input_name}"]'
            logging.debug(f"Checking label using name: {label_for_name_selector}")
            label_for_name = element.page.locator(label_for_name_selector).first
            try:
                if label_for_name.is_visible(timeout=1000): # Shorter timeout ok here
                    # Check sibling span of this label
                    sibling_span_selector = f'label[for="{input_name}"] + span'
                    logging.debug(f"Checking sibling span of label[for=name]: {sibling_span_selector}")
                    sibling_span = element.page.locator(sibling_span_selector).first
                    if sibling_span.is_visible(timeout=1000):
                        span_text = sibling_span.text_content(timeout=1000)
                        if span_text and '*' in span_text:
                             logging.info(f"Detected mandatory field '{input_name}' via label[for=name] + sibling span asterisk.")
                             return True
                        else:
                            logging.debug(f"Sibling span of label[for={input_name}] found, but no asterisk: '{span_text}'")
                    else:
                        logging.debug(f"Sibling span of label[for={input_name}] not visible.")
                else:
                     logging.debug(f"Label[for={input_name}] found but not visible.")
            except PlaywrightTimeoutError:
                 logging.debug(f"Label[for=name] or its sibling span lookup timed out for name: {input_name}")
            except Exception as e_label_name:
                logging.debug(f"Error checking label[for='{input_name}'] or sibling: {e_label_name}")
        else:
            logging.debug(f"Input element also does not have a NAME attribute.")
        # --- END NEW NAME CHECK ---
            
        # Check parent label if no 'for' attribute match (common pattern)
        # Note: This is less precise, might need refinement
        logging.debug(f"Checking ancestor label for input '{input_id or element.get_attribute('name')}'")
        try:
            parent_label = element.locator('xpath=./ancestor::label').first
            if parent_label.is_visible(timeout=2000): # Increased timeout
                label_text = parent_label.text_content(timeout=2000) # Increased timeout
                if label_text and '*' in label_text:
                     logging.info(f"Detected mandatory field '{input_id or element.get_attribute('name')}' via parent label asterisk.")
                     return True
                else:
                     logging.debug(f"Ancestor label found, but no asterisk in text: '{label_text}'")
            else:
                logging.debug(f"Ancestor label found but not visible.")
        except PlaywrightTimeoutError:
            logging.debug(f"Ancestor label check timed out.")
        except Exception as e_parent_label:
            logging.debug(f"Error checking ancestor label: {e_parent_label}")

    except Exception as e:
        logging.error(f"General error checking mandatory status: {e}") # Changed to error for general exceptions
    
    # If none of the above returned True, it's not considered mandatory
    logging.debug(f"Field '{element.get_attribute('name') or input_id}' determined as NOT mandatory.")
    return False

def fill_application_form(page, applicant_data):
    """Attempts to fill common application form fields, including new logic."""
    logging.info("Attempting to fill the application form...")
    filled_count = 0
    
    # --- Handle Salutation (Dropdown) ---
    try:
        salutation_selector = FORM_SELECTORS["salutation"]
        salutation_dropdown = page.locator(salutation_selector).first
        if salutation_dropdown.is_visible(timeout=5000):
            preferred_salutation = applicant_data.get("salutation_preference")
            if not preferred_salutation:
                logging.warning("Salutation preference not found in applicant_data. Skipping salutation.")
            else:
                logging.info(f"Attempting to select salutation: '{preferred_salutation}'")
                options = salutation_dropdown.locator('option').all()
                found_match = False
                normalized_preference = preferred_salutation.lower().strip()

                for option in options:
                    try:
                        option_text = option.text_content()
                        option_value = option.get_attribute('value')
                        if not option_text:
                            continue
                        
                        normalized_option_text = option_text.lower().strip()
                        
                        # Check for exact match or common variations
                        # (Can be expanded with more variations if needed)
                        match_found = False
                        if normalized_option_text == normalized_preference:
                            match_found = True
                        elif normalized_preference == "herr" and normalized_option_text in ["mr.", "mr"]:
                             match_found = True
                        elif normalized_preference == "frau" and normalized_option_text in ["ms.", "ms", "mrs.", "mrs"]:
                            match_found = True
                        # Add other potential matches here (e.g., Mx., Divers)

                        if match_found:
                            logging.info(f"Found matching option: '{option_text}'")
                            if option_value:
                                salutation_dropdown.select_option(value=option_value)
                                logging.info(f"Selected by value: {option_value}")
                            else:
                                # Fallback to selecting by label/text if value is missing/empty
                                salutation_dropdown.select_option(label=option_text)
                                logging.info(f"Selected by label: {option_text}")
                            filled_count += 1
                            found_match = True
                            time.sleep(0.5)
                            break # Exit loop once match is found and selected
                    except Exception as e_option:
                        logging.debug(f"Error processing salutation option: {e_option}")
                
                if not found_match:
                    logging.warning(f"Could not find an option matching '{preferred_salutation}'. Trying index 1 as fallback.")
                    # Fallback to original logic if no match found
                    if len(options) > 1:
                        try:
                            value_to_select = options[1].get_attribute('value')
                            if value_to_select:
                                salutation_dropdown.select_option(value=value_to_select)
                            else:
                                salutation_dropdown.select_option(index=1)
                            filled_count += 1 # Count fallback attempt
                            time.sleep(0.5)
                        except Exception as e_fallback:
                             logging.error(f"Failed to select salutation option by index 1 fallback: {e_fallback}")
        else:
            logging.debug(f"Salutation dropdown selector not visible: {salutation_selector}")
    except PlaywrightTimeoutError:
        logging.warning(f"Salutation dropdown timed out waiting for visibility.")
    except Exception as e:
        logging.warning(f"Could not find or select Salutation: {e}")

    # --- Fill Text Fields (First/Last Name, Email, Phone) ---
    for field, selector in [
        ('first_name', FORM_SELECTORS["first_name"]),
        ('last_name', FORM_SELECTORS["last_name"]),
        ('email', FORM_SELECTORS["email"]),
        ('phone', FORM_SELECTORS["phone"]),
    ]:
        try:
            field_locator = page.locator(selector).first
            if field_locator.is_visible(timeout=5000):
                value = applicant_data.get(field)
                if value:
                    logging.info(f"Filling field '{field}'...")
                    field_locator.fill(value)
                    filled_count += 1
                    time.sleep(0.5) 
                else:
                    logging.warning(f"Applicant data for field '{field}' is missing.")
            else:
                logging.debug(f"Field '{field}' selector not visible: {selector}")
        except PlaywrightTimeoutError:
            logging.warning(f"Field '{field}' timed out waiting for visibility.")
        except Exception as e:
            logging.warning(f"Could not find or fill field '{field}' using selector {selector}: {e}")

    # --- Handle Resume Upload --- 
    resume_path = applicant_data.get("resume_path")
    resume_selector = FORM_SELECTORS["resume_upload"]
    if resume_path and os.path.exists(resume_path):
        try:
            upload_locator = page.locator(resume_selector).first
            if upload_locator.is_enabled(timeout=10000): 
                 logging.info(f"Uploading resume from: {resume_path}")
                 upload_locator.set_input_files(resume_path)
                 filled_count += 1
                 time.sleep(1) 
            else:
                logging.warning("Resume upload field found but not enabled.")
        except PlaywrightTimeoutError:
             logging.warning("Resume upload field timed out waiting for visibility/enabled state.")
        except Exception as e:
            logging.warning(f"Could not find or interact with resume upload field using selector {resume_selector}: {e}")
    elif resume_path:
         logging.error(f"Resume file not found at specified path: {resume_path}")
    else:
        logging.warning("Resume path not specified in applicant data.")
    
    # --- Fill Start Date ---
    try:
        start_date_selector = FORM_SELECTORS["start_date"]
        start_date_input = page.locator(start_date_selector).first
        if start_date_input.is_visible(timeout=5000):
            # Calculate date: 1 month after the start of next month
            today = datetime.date.today()
            first_day_current_month = today.replace(day=1)
            first_day_next_month = first_day_current_month + relativedelta(months=1)
            target_start_date = first_day_next_month + relativedelta(months=1)
            # Format as DD.MM.YYYY (common German format)
            formatted_date = target_start_date.strftime("%d.%m.%Y")
            logging.info(f"Filling Start Date with: {formatted_date}")
            start_date_input.fill(formatted_date)
            filled_count += 1
            time.sleep(0.5)
        else:
            logging.debug(f"Start Date input selector not visible: {start_date_selector}")
    except PlaywrightTimeoutError:
        logging.warning(f"Start Date input timed out waiting for visibility.")
    except Exception as e:
        logging.warning(f"Could not find or fill Start Date: {e}")

    # --- Fill Salary Expectation ---
    try:
        salary_selector = FORM_SELECTORS["salary_expectation"]
        salary_input = page.locator(salary_selector).first
        if salary_input.is_visible(timeout=5000):
            salary_value = applicant_data.get("salary_expectation")
            if salary_value:
                logging.info(f"Filling Salary Expectation with: {salary_value}")
                salary_input.fill(salary_value)
                filled_count += 1
                time.sleep(0.5)
            else:
                 logging.warning("Salary expectation missing in applicant data.")
        else:
            logging.debug(f"Salary Expectation input selector not visible: {salary_selector}")
    except PlaywrightTimeoutError:
        logging.warning(f"Salary Expectation input timed out waiting for visibility.")
    except Exception as e:
        logging.warning(f"Could not find or fill Salary Expectation: {e}")

    # --- Handle Cover Letter (Generate if Mandatory) --- 
    # --- << COMMENTING OUT THIS BLOCK - Handling via general loop below >> ---
    # cover_letter_selector = FORM_SELECTORS["cover_letter_upload"]
    # cover_letter_mandatory = False
    # cover_letter_field_found = False
    # try:
    #     upload_locator = page.locator(cover_letter_selector).first
    #     if upload_locator.is_visible(timeout=3000): # Quicker check
    #         cover_letter_field_found = True
    #         cover_letter_mandatory = is_field_mandatory(upload_locator)
    #         logging.info(f"Cover letter upload field found. Mandatory: {cover_letter_mandatory}")
    #         if upload_locator.is_enabled(timeout=1000):
    #              if cover_letter_mandatory:
    #                  logging.info("Cover letter is mandatory. Generating placeholder text with Gemini...")
    #                  # Call Gemini to generate text
    #                  placeholder_text = generate_placeholder_doc_text('cover_letter', language='German')
    #                  
    #                  if placeholder_text:
    #                      # Define a more permanent output directory and filename
    #                      output_dir = "output/generated_documents"
    #                      # Try to get a company name for the filename (optional)
    #                      company_name = page.url.split('//')[-1].split('/')[0].split('.')[0] # Basic extraction
    #                      filename = f"placeholder_cover_letter_{company_name}_{random.randint(1000,9999)}.pdf"
    #                      placeholder_path = os.path.join(output_dir, filename)
    #                      if generate_placeholder_pdf(placeholder_path, placeholder_text):
    #                          logging.info(f"Uploading generated cover letter: {placeholder_path}")
    #                          upload_locator.set_input_files(placeholder_path)
    #                          filled_count += 1
    #                          time.sleep(1)
    #                      else:
    #                           logging.error("Failed to generate placeholder cover letter PDF.")
    #                  else:
    #                       logging.error("Failed to generate cover letter text from Gemini.")
    #              else:
    #                  logging.info("Cover letter field found but not mandatory. Skipping.")
    #         else:
    #              logging.warning("Cover letter upload field found but not enabled.")
    #     else:
    #         logging.debug(f"Cover letter upload selector not visible: {cover_letter_selector}")
    #         # If upload not found, check for text area
    #         # TODO: Add logic to check/fill cover letter text area if needed/mandatory

    # except PlaywrightTimeoutError:
    #      logging.debug(f"Cover letter upload field timed out/not found.")
    #      # TODO: Add check for mandatory text area here too?
    # except Exception as e:
    #     logging.warning(f"Could not find or interact with cover letter upload field: {e}")
    # --- << END OF COMMENTED OUT BLOCK >> ---

    # --- Handle Other Mandatory File Uploads --- 
    # This is complex - requires identifying all file inputs and checking if mandatory
    # --- <<< REVISED LOGIC: Upload first 2 non-CV/CoverLetter files found >>> ---
    try:
        resume_selector = FORM_SELECTORS["resume_upload"]
        # Cover letter selector (even if commented out above, use it here to avoid double processing)
        cover_letter_selector = FORM_SELECTORS.get("cover_letter_upload", "") 
        
        all_file_inputs = page.locator('input[type="file"]').all()
        logging.info(f"Found {len(all_file_inputs)} file input elements total.")
        
        other_uploads_count = 0 # Counter for additional uploads
        max_other_uploads = 2   # Upload exactly 2 other documents
        
        for i, file_input in enumerate(all_file_inputs):
            # --- Skip the main CV input directly by checking its known name --- 
            input_name_current = file_input.get_attribute('name')
            if input_name_current == "cv[cv]":
                logging.debug(f"Skipping input {i} as it matches the resume selector.")
                continue 

            # Check if this input matches the cover letter selector (if defined)
            # This prevents processing it here if there was specific cover letter logic (even if commented out)
            is_cover_letter = False
            if cover_letter_selector:
                try:
                    cl_selectors_list = cover_letter_selector.split(', ')
                    for sel in cl_selectors_list:
                         if file_input.evaluate(f'node => node.matches("{sel.strip()}")'):
                            is_cover_letter = True
                            break
                except Exception as e_eval_cl:
                    logging.debug(f"Error evaluating cover letter selector match: {e_eval_cl}")
                    pass
            
            if is_cover_letter:
                logging.debug(f"Skipping input {i} as it matches the cover letter selector.")
                continue
            
            # Process if it's one of the first 2 other uploads needed
            if file_input.is_visible(timeout=1000):
                input_name = file_input.get_attribute('name') or file_input.get_attribute('id') or f'input_{i}'
                
                if other_uploads_count < max_other_uploads:
                    logging.warning(f"Uploading placeholder for presumed mandatory file input: '{input_name}' (Other upload #{other_uploads_count + 1})")
                    upload_type = 'other_document'
                    placeholder_text_de = generate_placeholder_doc_text(upload_type, language='German')
                    
                    if placeholder_text_de:
                        output_dir = "output/generated_documents"
                        company_name = page.url.split('//')[-1].split('/')[0].split('.')[0]
                        safe_input_id = re.sub(r'[^a-zA-Z0-9_\-]', '_', input_name)
                        filename = f"placeholder_{upload_type}_{company_name}_{safe_input_id}_{random.randint(1000,9999)}.pdf"
                        placeholder_path = os.path.join(output_dir, filename)
                        if generate_placeholder_pdf(placeholder_path, placeholder_text_de):
                            if file_input.is_enabled(timeout=1000):
                                 logging.info(f"Uploading generated placeholder for '{input_name}' ('{upload_type}'): {placeholder_path}")
                                 file_input.set_input_files(placeholder_path)
                                 filled_count += 1
                                 other_uploads_count += 1 # Increment the counter
                                 time.sleep(1)
                            else:
                                logging.warning(f"Targeted file input '{input_name}' found but not enabled.")
                        else:
                            logging.error(f"Failed to generate placeholder PDF for '{input_name}'.")
                else:
                    # Log inputs skipped after reaching the desired count
                    logging.info(f"Skipping file input '{input_name}' as {max_other_uploads} other documents have been uploaded.")
            else:
                 logging.debug(f"Skipping non-visible file input {i}")

    except Exception as e:
        logging.error(f"Error while checking/handling other file inputs: {e}")
    # --- <<< END REVISED LOGIC >>> ---

    # --- Handle Source (Dropdown) ---
    try:
        source_selector = FORM_SELECTORS["source"]
        source_dropdown = page.locator(source_selector).first
        if source_dropdown.is_visible(timeout=5000):
            logging.info(f"Selecting first option for Source...")
            options = source_dropdown.locator('option').all()
            if len(options) > 1:
                value_to_select = options[1].get_attribute('value')
                if value_to_select:
                     source_dropdown.select_option(value=value_to_select)
                     filled_count += 1
                     time.sleep(0.5)
                else:
                     source_dropdown.select_option(index=1)
                     filled_count += 1
                     time.sleep(0.5)
            else:
                logging.warning("Source dropdown has <= 1 option.")
        else:
            logging.debug(f"Source dropdown selector not visible: {source_selector}")
    except PlaywrightTimeoutError:
        logging.warning(f"Source dropdown timed out waiting for visibility.")
    except Exception as e:
        logging.warning(f"Could not find or select Source: {e}")

    # --- Handle Consent Checkbox --- 
    try:
        consent_selector = FORM_SELECTORS["consent_checkbox"]
        consent_checkbox = page.locator(consent_selector).first
        if consent_checkbox.is_visible(timeout=5000):
            if not consent_checkbox.is_checked():
                logging.info("Checking data consent checkbox...")
                consent_checkbox.check()
                filled_count += 1
                time.sleep(0.5)
            else:
                logging.info("Consent checkbox already checked.")
        else:
            logging.debug(f"Consent checkbox selector not visible: {consent_selector}")
    except PlaywrightTimeoutError:
        logging.warning(f"Consent checkbox timed out waiting for visibility.")
    except Exception as e:
        logging.warning(f"Could not find or check Consent checkbox: {e}")

    # --- Final Count --- 
    if filled_count > 0:
         logging.info(f"Attempted to fill/upload {filled_count} fields/files.")
    else:
        logging.warning("Could not fill any form fields based on current selectors and logic.")
    
    # --- Find and potentially click submit button (optional - dangerous to automate fully) ---
    try:
        submit_locator = page.locator(FORM_SELECTORS["submit_button"]).first
        if submit_locator.is_visible(timeout=10000):
             logging.info(f"Found potential submit button: {submit_locator.text_content()}")
             # submit_locator.click() # <-- Be careful automating submission!
             # print("Submit button clicked (SIMULATED)")
        else:
             logging.warning("Submit button not found or not visible.")
    except Exception as e:
        logging.warning(f"Could not find submit button: {e}")

# Rename function to reflect its full purpose
def apply_to_job(
    career_page_url: str,
    target_job_title: str,
    applicant_data: dict,
    headless: bool = False,
    user_data_dir: str | None = None  # Add user_data_dir parameter
) -> str | None:
    """
    Navigates to a career page, finds the job, clicks it, attempts to fill the form,
    and returns the final URL. Uses a persistent user data directory if provided.
    """
    logging.info(f"Starting application process for: '{target_job_title}' from {career_page_url}")
    if user_data_dir:
        logging.info(f"Using persistent browser profile: {user_data_dir}")
    final_url = None
    normalized_target_title = normalize_text(target_job_title)

    browser = None
    context = None
    page = None
    job_link_element = None # Initialize job_link_element here
    try:
        with sync_playwright() as p:
            browser_type = p.chromium
            launch_options = {"headless": headless}
            if user_data_dir:
                # Launch persistent context instead of a new browser instance
                # This reuses the profile directory directly
                context = browser_type.launch_persistent_context(user_data_dir, **launch_options)
            else:
                # Launch a temporary browser if no user_data_dir is given
                browser = browser_type.launch(**launch_options)
                context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')

            page = context.new_page()
            # Apply stealth if needed (might be necessary for some career portals)
            # from playwright_stealth import stealth_sync
            # stealth_sync(page)

            page.goto(career_page_url, wait_until="load", timeout=90000)
            logging.info("Career page loaded.")

            # --- Attempt to handle Cookie Banners --- 
            logging.info("Checking for and trying to accept cookie banners...")
            accepted_cookie = False
            for selector in COOKIE_SELECTORS:
                try:
                    cookie_button = page.locator(selector).first # Use first to avoid ambiguity if multiple match
                    if cookie_button.is_visible(timeout=3000): # Short timeout, banner should appear quickly
                        logging.info(f"Found cookie button with selector: {selector}. Clicking...")
                        cookie_button.click()
                        accepted_cookie = True
                        time.sleep(1.5) # Wait a moment for banner to disappear
                        logging.info("Cookie banner likely accepted.")
                        break # Stop checking once one is clicked
                except PlaywrightTimeoutError:
                    logging.debug(f"Cookie selector not visible or timed out: {selector}")
                except Exception as e_cookie:
                    logging.warning(f"Error interacting with cookie selector {selector}: {e_cookie}")
            
            if not accepted_cookie:
                logging.info("Did not find or interact with any known cookie banners. Proceeding...")
            # -------------------------------------- 

            time.sleep(random.uniform(3, 5)) # Wait a bit longer for dynamic content
            logging.info("Waiting for dynamic content after potential cookie handling...")
            page.wait_for_load_state('networkidle', timeout=15000) # Wait for network to be idle

            # --- <<< REVISED STEP: Look for and click primary Apply Now button >>> ---
            primary_apply_button_found = False
            clicked_primary_apply = False
            logging.info("Searching for primary apply button (e.g., 'Apply Now', 'Ich will den Job')...")
            
            # Combine all strategies
            potential_buttons = []

            # 1. Try simple text selectors
            for selector in PRIMARY_APPLY_SELECTORS_TEXT:
                try:
                    potential_buttons.append(page.locator(selector).first)
                except Exception as e_sel:
                    logging.debug(f"Error finding locator for text selector {selector}: {e_sel}")

            # 2. Try regex selectors
            for tag, pattern in PRIMARY_APPLY_SELECTORS_REGEX:
                 try:
                    potential_buttons.append(page.locator(tag, text=pattern).first)
                 except Exception as e_sel_re:
                    logging.debug(f"Error finding locator for regex selector {tag} / {pattern}: {e_sel_re}")

            # 3. Try other attribute-based selectors
            for selector in OTHER_PRIMARY_APPLY_SELECTORS:
                 try:
                    potential_buttons.append(page.locator(selector).first)
                 except Exception as e_sel_attr:
                    logging.debug(f"Error finding locator for attr selector {selector}: {e_sel_attr}")

            # Now check the found potential buttons for visibility and click the first valid one
            for apply_button in potential_buttons:
                try:
                    if apply_button.is_visible(timeout=2000): # Quick check
                        button_text = apply_button.text_content(timeout=1000) or apply_button.get_attribute('value') or apply_button.get_attribute('aria-label')
                        button_text = button_text.strip() if button_text else "[button]"
                        # Attempt to get the selector string for logging (best effort)
                        selector_str = "unknown_selector"
                        try:
                            # This is internal API, might break, but useful for logging
                            selector_str = apply_button._selector
                        except: pass
                        
                        logging.info(f"Found primary Apply button: '{button_text}' with selector hint: {selector_str}. Clicking...")
                        apply_button.scroll_into_view_if_needed(timeout=5000)
                        time.sleep(0.5)

                        # Handle potential new tab (logic remains the same)
                        new_page_info = None
                        try:
                            with context.expect_page(timeout=15000) as new_page_info_ctx:
                                apply_button.click(timeout=10000)
                            new_page_info = new_page_info_ctx
                        except PlaywrightTimeoutError:
                            pass # No new page opened

                        if new_page_info:
                            new_page = new_page_info.value
                            logging.info(f"New tab opened: {new_page.url}")
                            if page: page.close() # Close the old page if it exists
                            page = new_page # Switch focus to the new page
                            page.wait_for_load_state('domcontentloaded', timeout=30000)
                            logging.info("Switched to new tab and waited for load.")
                        else:
                            logging.info("No new tab opened by clicking primary apply button, continuing on current page.")
                            if page: # Ensure page exists before waiting
                                 page.wait_for_load_state('domcontentloaded', timeout=15000)

                        primary_apply_button_found = True
                        clicked_primary_apply = True
                        final_url = page.url if page else None # Update final_url
                        break # Stop searching once a button is clicked

                except PlaywrightTimeoutError:
                    logging.debug(f"Potential apply button not visible or timed out.")
                except Exception as e_click:
                    logging.warning(f"Error clicking primary apply button: {e_click}")
                    # Continue trying other potential buttons
            
            if clicked_primary_apply:
                 logging.info("Successfully clicked a primary apply button.")
            else:
                 # ... (rest of the code including fallback to find_job_link, etc.)
                 # ... IMPORTANT: Ensure indentation errors are manually fixed here if they persist
                 logging.info("No primary apply button found or clicked. Proceeding to look for job title link.")
                 # ... (rest of the fallback logic)

            # --- Proceed to Fill Form --- 
            if clicked_primary_apply or job_link_element: # Only proceed if navigation was successful
                logging.info(f"Proceeding to fill form at URL: {page.url if page else 'UNKNOWN'}")
                if page: # Check if page object exists before filling
                    form_filled = fill_application_form(page, applicant_data)
                else:
                    logging.error("Page object became None before form filling could start.")
            else:
                logging.error("Could not navigate to application form page. Skipping form filling.")

    except Exception as e:
        logging.error(f"An unexpected error occurred in apply_to_job: {e}", exc_info=True)
        final_url = None # Indicate failure
    finally:
        # Ensure context and browser are properly closed
        logging.debug("Closing Playwright context and browser (if applicable)...")
        if context:
            try:
                # Check if context is still connected before closing
                if context.pages: # A simple check if there are pages
                    context.close()
                    logging.debug("Context closed.")
                else:
                    logging.debug("Context seems already closed or disconnected.")
            except Exception as e_ctx:
                # Log specific error if closing fails
                logging.warning(f"Error closing context: {e_ctx}") 
        if browser: # Check if browser object exists
            try:
                browser.close()
                logging.debug("Browser closed.")
            except Exception as e_brw:
                logging.warning(f"Error closing browser: {e_brw}")

    return final_url

# --- Example Usage ---
if __name__ == '__main__':
    logging.info("Starting Apply On Site example (Navigation to Job)... ")

    # --- Get URL and Title (Replace with actual values from previous step) ---
    # Example URL found by find_career_page.py for TieTalent
    example_career_url = "https://tietalent.com/en/jobs"
    # Example Title from the job data
    example_job_title = "(Junior) Data Analyst (w/m/d)"

    if not example_career_url or not example_job_title:
        logging.error("Please provide example_career_url and example_job_title in the script.")
    else:
        print(f"\nAttempting to navigate to application page for:")
        print(f"  Job Title: {example_job_title}")
        print(f"  From Page: {example_career_url}")
        # Run with headless=False to watch
        # Define the path to the persistent profile directory (Use a separate one for TieTalent)
        profile_path = "playwright_profiles/tietalent"
        # Create the directory if it doesn't exist
        os.makedirs(profile_path, exist_ok=True)

        application_page_url = apply_to_job(
            example_career_url,
            example_job_title,
            APPLICANT_DATA, # Pass the applicant data dictionary
            headless=False,
            user_data_dir=profile_path # Pass the profile path
        )

        if application_page_url:
            print(f"\n--> Reached potential application page URL:")
            print(f"    {application_page_url}")
            print("\nNext step: Implement form filling for this page structure.")
        else:
            print("\n--> Failed to navigate to the specific job application page.")

    logging.info("Apply On Site example finished.") 