# AI Career Accelerator

Transform your job applications with AI-powered, ATS-optimized resumes, cover letters, and follow-up emails — built for the **Kenyan** job market.

- ✅ ATS-friendly resume rewriting
- ✅ Tailored cover letters
- ✅ Strategic follow-up email sequences
- ✅ M-Pesa Premium unlock (KES 1,500)
- ✅ Built for roles at Safaricom, KCB, Equity, Deloitte, PwC, UN, Microsoft ADC, Twiga, and more.

---

## 1. Tech Stack

- **Python 3.10+**
- **Streamlit** (frontend app)
- **Google Gemini** (LLM via `google-generativeai`)
- **SQLite** (payments DB)
- **FastAPI + Uvicorn** (IntaSend webhook server)
- **IntaSend** M-Pesa STK push integration

---

## 2. Quick Start (Local Dev)

```bash
git clone https://github.com/yourusername/career-accelerator.git
cd career-accelerator

python -m venv venv
# macOS / Linux:
source venv/bin/activate
# Windows (PowerShell):
# .\venv\Scripts\Activate.ps1

pip install -r requirements.txt
