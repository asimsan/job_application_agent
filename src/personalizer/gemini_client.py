# src/personalizer/gemini_client.py
import os
# Revert import if needed, ensure it's the intended one
import google.generativeai as genai
import logging
from typing import Literal # Import Literal

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global Gemini client instance (initialized later)
gemini_model = None

def configure_gemini():
    """Configures the Gemini client using the API key from environment variables."""
    global gemini_model
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        logging.error("GEMINI_API_KEY environment variable not found.")
        return False
    try:
        genai.configure(api_key=api_key)
        # Initialize the specific model variant we want to use
        # Using 1.5 Flash Preview for speed and cost-effectiveness
        # Switch back to the requested preview model
        gemini_model = genai.GenerativeModel('gemini-2.5-flash-preview-04-17')
        logging.info("Gemini API configured successfully using environment variable.")
        return True
    except Exception as e:
        logging.error(f"Failed to configure Gemini API: {e}")
        gemini_model = None
        return False

def generate_text(prompt: str, model_name='gemini-2.5-flash-preview-04-17') -> str | None:
    """Generates text using the configured Gemini model."""
    global gemini_model
    if not gemini_model:
        logging.warning("Gemini client is not configured. Attempting to configure now...")
        if not configure_gemini():
             logging.error("Cannot generate text, Gemini configuration failed.")
             return None
        # Re-check if model got assigned after configuration attempt
        if not gemini_model:
             logging.error("Gemini model still not available after reconfiguration attempt.")
             return None

    # If the user specified a different model for this specific call
    current_model = gemini_model
    if model_name != gemini_model.model_name:
        try:
            logging.info(f"Using specified model for this call: {model_name}")
            current_model = genai.GenerativeModel(model_name)
        except Exception as e:
            logging.error(f"Failed to instantiate model {model_name}: {e}. Falling back to default.")
            # Fallback handled by using the globally configured gemini_model

    try:
        logging.info(f"Sending prompt to Gemini model ({current_model.model_name})...")
        response = current_model.generate_content(prompt)
        logging.info(f"Received response object from Gemini: {response!r}") # Log the raw response object for debugging
        
        # Check for empty or blocked responses
        if not response.candidates:
            logging.warning(f"Gemini response has no candidates. Finish reason: {response.prompt_feedback}")
            return None
        if not response.text:
             logging.warning(f"Gemini response candidate has no text. Finish reason: {response.candidates[0].finish_reason}")
             # Attempt to access parts if text is missing (might indicate multimodal or structured output not handled here)
             try:
                 parts_text = " ".join([part.text for part in response.candidates[0].content.parts if hasattr(part, 'text')])
                 if parts_text:
                      logging.info(f"Extracted text from response parts: '{parts_text[:100]}...'")
                      return parts_text
             except Exception as e_parts:
                 logging.error(f"Could not extract text from response parts: {e_parts}")
             return None # Return None if no text found

        logging.info(f"Value returned by generate_text: {response.text[:100]}...") # Log start of text
        return response.text
    except Exception as e:
        logging.error(f"Error generating text with Gemini: {e}", exc_info=True)
        return None

# --- Example Usage (Optional) ---
if __name__ == '__main__':
    # Test script should be run from root using python -m src.personalizer.gemini_client
    logging.info("Testing Gemini client configuration...")
    if gemini_model:
        logging.info("Gemini API appears configured.")
        # Test with the default model
        test_model = gemini_model
        if test_model:
            logging.info(f"Gemini model instance ({test_model.model_name}) obtained successfully.")
            example_prompt = "Write a short paragraph about the benefits of automating job applications."
            generated_content = generate_text(example_prompt) # Uses DEFAULT_MODEL
            if generated_content:
                print("\n--- Generated Content ---")
                print(generated_content)
                print("-------------------------")
            else:
                print("\nFailed to generate content.")
        else:
             logging.error(f"Could not obtain Gemini model instance ({test_model.model_name}).")
    else:
        logging.error("Gemini client could not be configured. Please check API key and environment variable.")

# --- New Function for Placeholder Document Generation ---
def generate_placeholder_doc_text(doc_type: str, language: str = 'German') -> str | None:
    """Generates placeholder text for a specific document type using Gemini."""
    prompt = f"""
    Please generate concise placeholder text in {language} for a document of type '{doc_type}'. 
    This text will be used to create a placeholder PDF for a job application form field that requires this type of document, but where the applicant doesn't have the specific document ready or it's optional.
    
    The text should clearly state its placeholder nature, for example: 
    '[Placeholder {doc_type} - Generated Content]' followed by 1-2 sentences indicating that the actual document will be submitted later if required.
    Keep the text brief and professional.
    
    Example for 'Cover Letter':
    [Platzhalter Anschreiben - Generierter Inhalt]
    Sehr geehrte Damen und Herren,
    anbei erhalten Sie wie gewünscht die erforderlichen Unterlagen. Das spezifische Anschreiben wird bei Bedarf nachgereicht.
    Mit freundlichen Grüßen,
    [Ihr Name]

    Now generate the text for: {doc_type}
    """
    logging.info(f"Generating placeholder text for document type: {doc_type} in {language}")
    return generate_text(prompt)

# --- NEW FUNCTION --- 
def suggest_job_titles_from_resume(resume_text: str, num_suggestions: int = 3) -> list[str]: # Default to 3 suggestions
    """Analyzes resume text using Gemini and suggests relevant Werkstudent role types."""
    if not resume_text:
        logging.error("Resume text is empty. Cannot suggest job titles.")
        return []

    prompt = f"""
    Analyze the following resume text (from file ann.pdf) for a candidate seeking **Werkstudent** (Working Student) positions in Germany, primarily in the Köln/Düsseldorf/Bonn region.
    
    Based on the skills (especially technical skills like programming languages, frameworks, tools), projects, and any mentioned experience, suggest {num_suggestions} specific *types* of Werkstudent roles this candidate would be well-suited for.
    
    Focus on roles matching the technical profile. Examples might include:
    * Werkstudent Softwareentwicklung (Java/Python/etc.)
    * Werkstudent Webentwicklung (Frontend/Backend/Fullstack)
    * Werkstudent Data Science / Data Analysis
    * Werkstudent IT-Support
    * Werkstudent DevOps
    * Werkstudent Mobile Development (React Native/iOS/Android)
    * Werkstudent Machine Learning
    
    Tailor the suggestions to the specific skills found in the resume below. 

    Please return ONLY a comma-separated list of the suggested Werkstudent role types. Do not include explanations, numbering, or any other text.
    Example format: Werkstudent Softwareentwicklung Python, Werkstudent Data Analysis, Werkstudent Webentwicklung Frontend

    Resume Text:
    --- START RESUME ---
    {resume_text}
    --- END RESUME ---

    Suggested Werkstudent Role Types (comma-separated):
    """

    logging.info(f"Asking Gemini to suggest {num_suggestions} Werkstudent role types based on resume...")
    response_text = generate_text(prompt)

    if not response_text:
        logging.error("Did not receive a valid response from Gemini for Werkstudent role suggestions.")
        return []

    # Parse the comma-separated list
    suggested_titles = [title.strip() for title in response_text.split(',') if title.strip() and ('werkstudent' in title.lower() or 'working student' in title.lower())] # Ensure suggestions are relevant
    
    if not suggested_titles:
        logging.warning(f"Gemini response did not contain a parseable comma-separated list of Werkstudent roles: {response_text}")
        # Fallback split by newline
        suggested_titles = [title.strip() for title in response_text.split('\n') if title.strip() and ('werkstudent' in title.lower() or 'working student' in title.lower())]
        if suggested_titles:
            logging.info(f"Parsed Werkstudent roles using newline split fallback: {suggested_titles}")
        else:
            logging.error("Could not parse Werkstudent roles from Gemini response.")
            return []

    logging.info(f"Gemini suggested Werkstudent role types: {suggested_titles}")
    return suggested_titles[:num_suggestions] # Return only the requested number 