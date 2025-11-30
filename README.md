🚀 AI Career Accelerator

Transform your job applications with AI-powered resume rewriting, cover letter creation, and follow-up strategies — all in one seamless Streamlit app.

💡 What This App Does

The AI Career Accelerator leverages the power of Google's Gemini 1.5/2.0 Flash models to act as your personal career consultant. It takes your existing resume and a target job description to generate a complete, ATS-optimized application kit in seconds.

✅ Core Features

📄 Intelligent Resume Parsing

Extracts clean text from PDF resumes or accepts manual input.

Uses regex-hardened JSON parsing to ensure data accuracy.

✍️ ATS-Optimized Rewrite

Rewrites your resume content to match the specific keywords and "pain points" of the job description.

Outputs clean Markdown that passes Applicant Tracking Systems.

📨 Cover Letter Generator

Drafts a persuasive, role-specific cover letter that connects your experience to the company's needs.

📫 Strategic Follow-Up Emails

Generates a 3-email sequence (Application follow-up, Post-interview thank you, Check-in) tailored to the role.

💾 Multi-Format Export

Download your documents in Markdown, Word (DOCX), or PDF formats.

🛠️ Installation

Clone the repository

git clone [https://github.com/YOUR_USERNAME/career-accelerator.git](https://github.com/YOUR_USERNAME/career-accelerator.git)
cd career-accelerator


Create a Virtual Environment (Recommended)

# Windows
python -m venv venv
venv\Scripts\activate

# Mac/Linux
python3 -m venv venv
source venv/bin/activate


Install Dependencies

pip install -r requirements.txt


⚙️ Configuration

This app requires a Google Gemini API Key. You can configure it in two ways:

Option 1: Secure (Recommended for Local/Deployment)

Create a secrets file to store your key securely.

Create a folder named .streamlit in the root directory.

Create a file named secrets.toml inside it.

Add your key:

# .streamlit/secrets.toml
GEMINI_API_KEY = "AIzaSy..."


Option 2: Manual Input (Easy Start)

If no secrets file is found, the app will provide a text input field in the sidebar for you to paste your key manually.

🔑 Get your free API key here: Google AI Studio

🚀 Usage

Run the Streamlit app:

streamlit run app.py


Open your browser to http://localhost:8501.

Sidebar: Select "Upload PDF Resume" or "Enter Text Manually".

Main Window: Paste the Job Description you are applying for.

Click "Analyze & Generate Kit".

Navigate through the tabs to view and download your optimized documents.

📂 Project Structure

career-accelerator/
├── app.py               # Frontend UI (Streamlit)
├── agents.py            # AI Logic & Backend (Gemini)
├── requirements.txt     # Python Dependencies
└── .gitignore           # Security rules (Ignores secrets & uploads)


📄 License

This project is licensed under the MIT License - see the LICENSE file for details.