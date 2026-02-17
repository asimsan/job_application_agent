# src/personalizer/personalize.py
import json
import os
import logging
from .gemini_client import generate_text, is_configured as is_gemini_configured
# Import pypdf
try:
    from pypdf import PdfReader
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False
    logging.warning("pypdf library not found. PDF resume loading will not be available. Install with: pip install pypdf")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Configuration ---
# Update default path to suggest PDF possibility
DEFAULT_BASE_RESUME_PATH = "data/ann.pdf" # <<< CHANGED
DEFAULT_OUTPUT_DIR = "output/tailored_resumes"

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

def load_text_file(file_path: str) -> str | None:
    """Loads text content from a file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        logging.info(f"Successfully loaded text from {file_path}")
        return content
    except FileNotFoundError:
        logging.error(f"Text file not found: {file_path}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred loading {file_path}: {e}")
        return None

def load_pdf_text(file_path: str) -> str | None:
    """Loads text content from a PDF file using pypdf."""
    if not PYPDF_AVAILABLE:
        logging.error("pypdf library is required to load PDF files but it's not installed.")
        return None
    try:
        reader = PdfReader(file_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n" # Add newline between pages
        logging.info(f"Successfully extracted text from PDF: {file_path}")
        return text.strip()
    except FileNotFoundError:
        logging.error(f"PDF file not found: {file_path}")
        return None
    except Exception as e:
        # pypdf can raise various errors on malformed PDFs
        logging.error(f"Failed to extract text from PDF {file_path}: {e}")
        return None

def save_text_file(content: str, file_path: str):
    """Saves text content to a file, creating directories if needed."""
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logging.info(f"Successfully saved text to {file_path}")
    except Exception as e:
        logging.error(f"Failed to save text to {file_path}: {e}")

def create_tailoring_prompt(job_description: str, base_resume: str) -> str:
    """Creates a prompt for Gemini to tailor a resume to a job description."""
    # This prompt can be refined significantly for better results
    prompt = f"""
Here is a base resume:
--- BASE RESUME START ---
{base_resume}
--- BASE RESUME END ---

Here is a job description for a **Werkstudent (Working Student)** position:
--- JOB DESCRIPTION START ---
{job_description}
--- JOB DESCRIPTION END ---

Please rewrite the base resume to highlight the skills and experiences most relevant to this specific **Werkstudent** job description. Focus on matching keywords and requirements mentioned in the job description. Emphasize relevant coursework, projects, technical skills, availability for part-time work, and eagerness to learn practical skills alongside university studies. Maintain a professional tone and the overall structure of the base resume, but adjust wording and emphasis where appropriate to create a targeted version for a working student role. Output only the tailored resume text.
"""
    return prompt

# --- Main Function ---

def personalize_resume_for_job(
    job_data_path: str,
    job_index: int = 0, # Which job in the list to process
    base_resume_path: str = DEFAULT_BASE_RESUME_PATH,
    output_dir: str = DEFAULT_OUTPUT_DIR
) -> bool:
    """
    Loads job data, base resume, generates a tailored resume using Gemini, and saves it.

    Args:
        job_data_path (str): Path to the JSON file containing scraped job data.
        job_index (int): Index of the job within the JSON list to personalize for.
        base_resume_path (str): Path to the base resume text file.
        output_dir (str): Directory to save the tailored resume.

    Returns:
        bool: True if successful, False otherwise.
    """
    if not is_gemini_configured:
        logging.error("Gemini client is not configured. Exiting personalization.")
        return False

    # 1. Load data
    jobs = load_json_data(job_data_path)
    if not jobs:
        return False

    # Load base resume (handling PDF or TXT)
    logging.info(f"Loading base resume from: {base_resume_path}")
    if base_resume_path.lower().endswith('.pdf'):
        base_resume = load_pdf_text(base_resume_path)
    else:
        base_resume = load_text_file(base_resume_path)

    if not base_resume:
        logging.error(f"Failed to load base resume content from {base_resume_path}. Aborting.")
        return False

    # 2. Select job and get description
    if job_index < 0 or job_index >= len(jobs):
        logging.error(f"Invalid job index {job_index} for job list of length {len(jobs)}.")
        return False

    job = jobs[job_index]
    job_description = job.get("description")
    job_title = job.get("title", "Unknown Title")
    job_company = job.get("company", "Unknown Company")
    job_key = job.get("job_key") or job.get("url", "").split('/')[-1] or f"job_{job_index}" # Create a fallback ID

    if not job_description:
        logging.error(f"Job at index {job_index} ('{job_title}' at '{job_company}') has no description. Skipping.")
        # We could potentially try to *fetch* the description here if it's missing,
        # but for now, we require it to be present from the scraping stage.
        return False

    logging.info(f"Personalizing resume for: '{job_title}' at '{job_company}' (Index: {job_index})")

    # 3. Create prompt
    prompt = create_tailoring_prompt(job_description, base_resume)

    # 4. Generate tailored resume
    logging.info("Requesting tailored resume from Gemini...")
    tailored_resume = generate_text(prompt) # Uses the function from gemini_client

    if not tailored_resume:
        logging.error("Failed to generate tailored resume from Gemini.")
        return False

    # 5. Save result
    output_filename = f"tailored_resume_{job_company.replace(' ', '_')}_{job_title.replace(' ', '_')}_{job_key}.txt"
    output_path = os.path.join(output_dir, output_filename)
    save_text_file(tailored_resume, output_path)

    return True

# --- Example Usage ---
if __name__ == '__main__':
    logging.info("Starting resume personalization example...")

    # --- Prerequisites ---
    # Ensure you have run a scraper (e.g., linkedin_scraper.py) to generate a JSON file.
    # Find the latest generated JSON file (example assumes LinkedIn)
    try:
        output_files = sorted([f for f in os.listdir('.') if f.startswith('linkedin_jobs') and f.endswith('.json')])
        if not output_files:
             raise FileNotFoundError("No LinkedIn JSON output files found in the root directory.")
        latest_job_file = output_files[-1]
        logging.info(f"Using latest job file: {latest_job_file}")
    except FileNotFoundError as e:
        logging.error(f"{e} Please run a scraper first.")
        latest_job_file = None
    except Exception as e:
        logging.error(f"Error finding latest job file: {e}")
        latest_job_file = None

    # Define the specific resume path we want to use
    target_resume_path = "data/ann.pdf" # <<< ADDED specific path variable

    # Check for base resume (PDF or TXT)
    if not os.path.exists(target_resume_path):
        logging.warning(f"Base resume file not found at {target_resume_path}. Ensure your resume PDF is placed there.")
        # We won't create a placeholder PDF, user must provide their resume.

    # --- Run Personalization ---
    # Check if the specified target_resume_path exists before running
    if latest_job_file and os.path.exists(target_resume_path):
        # Personalize for the first job (index 0) in the latest file
        success = personalize_resume_for_job(
            job_data_path=latest_job_file,
            job_index=0,
            base_resume_path=target_resume_path # <<< UPDATED to use specific path
        )

        if success:
            logging.info("Resume personalization process completed successfully.")
        else:
            logging.error("Resume personalization process failed.")
    else:
        logging.error(f"Cannot run personalization. Check if job file exists ('{latest_job_file}') and base resume exists ('{target_resume_path}').") # <<< UPDATED logging message

    logging.info("Resume personalization example finished.") 