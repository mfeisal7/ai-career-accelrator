# app.py
import json
import re
import uuid
from io import BytesIO

import streamlit as st

from agents import (
    extract_text_from_pdf,
    analyze_job,
    rewrite_resume,
    generate_cover_letter,
    generate_emails,
)
from docx import Document
from fpdf import FPDF

# IntaSend backend helper
from payments import trigger_mpesa_payment, check_payment_status

# Payments DB
from payments_db import init_db, create_payment, is_user_paid, mark_invoice_paid

# Webhook server (FastAPI) runner
import threading
from webhook_server import run as run_webhook_server


# ============================================================
# One-time initialization: DB + Webhook server
# ============================================================

# Ensure SQLite is ready
init_db()


@st.cache_resource(show_spinner=False)
def _start_webhook_server_once() -> bool:
    """
    Start the FastAPI webhook server exactly once per process
    using a daemon thread.
    """
    thread = threading.Thread(
        target=run_webhook_server,
        kwargs={"host": "0.0.0.0", "port": 8000},
        daemon=True,
    )
    thread.start()
    return True


_start_webhook_server_once()


# ============================================================
# Session / User ID helpers
# ============================================================

def get_or_create_user_id() -> str:
    """
    Generate a persistent user_id for this browser.

    - Stored in st.session_state["user_id"]
    - Also persisted in URL query params using st.query_params
    - Best-effort non-HTTP-only cookie set via JS
    """
    # If already in session, return it
    if "user_id" in st.session_state and st.session_state["user_id"]:
        return st.session_state["user_id"]

    # Read from URL query params (new Streamlit API)
    query_params = st.query_params
    existing_uid = query_params.get("user_id")

    if existing_uid:
        user_id = existing_uid
    else:
        # Generate new UUID
        user_id = str(uuid.uuid4())
        # Update URL query params
        st.query_params.update({"user_id": user_id})

    # Persist in session
    st.session_state["user_id"] = user_id

    # Best-effort cookie (not httpOnly, but better than nothing)
    st.markdown(
        f"""
        <script>
        (function() {{
            const cookies = document.cookie || "";
            if (!cookies.includes("user_id=")) {{
                const expires = new Date();
                expires.setFullYear(expires.getFullYear() + 1);
                document.cookie =
                    "user_id={user_id};expires=" +
                    expires.toUTCString() +
                    ";path=/;SameSite=Lax";
            }}
        }})();
        </script>
        """,
        unsafe_allow_html=True,
    )

    return user_id


# ============================================================
# Session State Initialization
# ============================================================

def init_state() -> None:
    """
    Initialize Streamlit session state with default values
    for all application artifacts.
    """
    defaults = {
        "resume_text": "",
        "job_description": "",
        "job_analysis_json": {},
        "final_resume_text": "",
        "cover_letter_text": "",
        "follow_up_emails": [],
        "last_uploaded_filename": "",      # track last uploaded resume file name
        "resume_input_mode": "Upload PDF Resume",  # resume input mode
        # Payment-related UI flags (state about *expecting* a webhook / manual check)
        "waiting_for_payment": False,
        "pending_invoice_id": None,
        # Simple rate limiting: how many full analyses this session has done
        "analysis_uses": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# ============================================================
# 6. CONVERSION UTILITIES
# ============================================================

def _add_markdown_runs_to_paragraph(paragraph, text: str) -> None:
    """
    Add runs to a python-docx paragraph, applying bold to segments wrapped in **...**.
    Example: "Hello **World**" -> "Hello " (normal) + "World" (bold).
    """
    parts = re.split(r"(\*\*.*?\*\*)", text)
    for part in parts:
        if not part:
            continue
        bold_match = re.fullmatch(r"\*\*(.*?)\*\*", part)
        if bold_match:
            run_text = bold_match.group(1)
            run = paragraph.add_run(run_text)
            run.bold = True
        else:
            paragraph.add_run(part)


def to_docx(markdown_text: str) -> BytesIO:
    """
    Convert a Markdown-like string into a DOCX file.
    """
    doc = Document()

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip("\n")

        # Blank line -> empty paragraph for spacing
        if not line.strip():
            doc.add_paragraph("")
            continue

        stripped = line.lstrip()

        # Heading level 2 (###)
        if stripped.startswith("### "):
            text = stripped[4:]
            p = doc.add_paragraph()
            p.style = "Heading 2"
            _add_markdown_runs_to_paragraph(p, text)

        # Heading level 1 (##)
        elif stripped.startswith("## "):
            text = stripped[3:]
            p = doc.add_paragraph()
            p.style = "Heading 1"
            _add_markdown_runs_to_paragraph(p, text)

        # Bullet points (- or *)
        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:]
            p = doc.add_paragraph(style="List Bullet")
            _add_markdown_runs_to_paragraph(p, text)

        # Normal paragraph
        else:
            p = doc.add_paragraph()
            _add_markdown_runs_to_paragraph(p, stripped)

    buffer = BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer


def _strip_markdown_bold(text: str) -> str:
    """Remove **bold** markers for PDF rendering."""
    return text.replace("**", "")


def to_pdf(markdown_text: str) -> bytes:
    """
    Convert a Markdown-like string into a styled PDF using fpdf2.
    """
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    effective_width = pdf.epw  # safe printable width

    def sanitize(text: str) -> str:
        """
        Make text safe for the built-in Helvetica font (Latin-1 only).
        """
        text = _strip_markdown_bold(text)
        text = text.replace("\t", "    ")

        replacements = {
            "“": '"',
            "”": '"',
            "„": '"',
            "‟": '"',
            "‘": "'",
            "’": "'",
            "‚": "'",
            "–": "-",
            "—": "-",
            "…": "...",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)

        text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", text)
        text = text.encode("latin-1", "replace").decode("latin-1")
        return text

    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip("\n")

        if not line.strip():
            pdf.set_x(pdf.l_margin)
            pdf.ln(4)
            continue

        stripped = line.lstrip()

        if stripped.startswith("### "):
            text = sanitize(stripped[4:])
            pdf.set_text_color(0, 0, 128)
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(effective_width, 8, text)
            pdf.ln(2)

        elif stripped.startswith("## "):
            text = sanitize(stripped[3:])
            pdf.set_text_color(0, 0, 128)
            pdf.set_font("Helvetica", "B", 16)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(effective_width, 8, text)
            pdf.ln(2)

        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = sanitize(stripped[2:])
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 12)
            bullet_indent = 4
            pdf.set_x(pdf.l_margin + bullet_indent)
            pdf.multi_cell(effective_width - bullet_indent, 6, "- " + text)

        else:
            text = sanitize(stripped)
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "", 12)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(effective_width, 6, text)

    # fpdf2 >= 2.7.9 returns a bytearray for dest="S"
    return bytes(pdf.output(dest="S"))


# ============================================================
# Phone validation helper (Kenya)
# ============================================================

def _is_valid_kenyan_phone(phone: str) -> bool:
    """
    Basic Kenyan mobile number validation.

    Accept:
      - 07XXXXXXXX
      - 01XXXXXXXX
      - 2547XXXXXXXX
      - 2541XXXXXXXX
      - +2547XXXXXXXX
      - +2541XXXXXXXX
    """
    if not phone:
        return False

    # strip spaces and hyphens
    cleaned = re.sub(r"[ \-]", "", phone)

    pattern = r"^(07\d{8}|01\d{8}|2547\d{8}|2541\d{8}|\+2547\d{8}|\+2541\d{8})$"
    return re.fullmatch(pattern, cleaned) is not None


# ============================================================
# Payment UI Helpers
# ============================================================

def render_payment_gate(feature_name: str, amount: int, user_id: str) -> None:
    """
    Reusable payment gate for any premium feature.

    Flow:
      1. User enters phone, clicks "Pay KES X" -> trigger_mpesa_payment()
      2. Show "STK push sent! Complete on your phone then click below"
      3. Show "I have paid – Check Status" button
      4. On click, call check_payment_status(invoice_id) once:
         - If True: mark_invoice_paid + success + st.rerun()
         - If False: show error
         - If None: show 'still pending' warning
    """
    slug = re.sub(r"[^a-z0-9]+", "_", feature_name.lower()).strip("_") or "premium"

    phone_key = f"mpesa_phone_{slug}"
    pay_button_key = f"pay_{slug}"
    status_button_key = f"check_status_{slug}"

    st.markdown("### 🔓 Premium Unlock")
    st.write(f"Unlock **all {feature_name.lower()} downloads** (Markdown, Word, PDF) for:")
    st.write(f"**KES {amount:,}**")

    phone_input = st.text_input(
        "M-Pesa Number (via IntaSend)",
        placeholder="0712345678",
        key=phone_key,
    )

    if st.button(f"Pay KES {amount:,}", key=pay_button_key):
        if not phone_input:
            st.error("Please enter your M-Pesa phone number.")
        else:
            cleaned_phone = re.sub(r"[ \-]", "", phone_input)

            if not _is_valid_kenyan_phone(cleaned_phone):
                st.error(
                    "Please enter a valid Kenyan mobile number, e.g. "
                    "0712345678 or +254712345678."
                )
            else:
                with st.spinner("Sending IntaSend payment request to your phone..."):
                    invoice_id = trigger_mpesa_payment(
                        phone_number=cleaned_phone,
                        amount=amount,
                    )

                if not invoice_id:
                    st.error("Unable to initiate payment via IntaSend. Please try again.")
                else:
                    create_payment(
                        user_id=user_id,
                        phone=cleaned_phone,
                        invoice_id=invoice_id,
                        amount=float(amount),
                    )
                    st.session_state["waiting_for_payment"] = True
                    st.session_state["pending_invoice_id"] = invoice_id

                    st.info(
                        "📲 STK push sent! Complete payment on your phone, then click "
                        "**'I have paid – Check Status'** below."
                    )

    # Manual status check (one-shot, no polling loop)
    invoice_id = st.session_state.get("pending_invoice_id")
    if invoice_id:
        if st.button("✅ I have paid – Check Status", key=status_button_key):
            with st.spinner("Checking payment status..."):
                status = check_payment_status(invoice_id)

            if status is True:
                mark_invoice_paid(invoice_id)
                st.session_state["waiting_for_payment"] = False
                st.session_state["pending_invoice_id"] = None
                st.success("Payment confirmed! Refreshing...")
                st.rerun()
            elif status is False:
                st.error(
                    "Payment failed or was cancelled. Please try again or initiate a new STK push."
                )
            else:
                st.warning(
                    "Payment is still pending. If you've just approved it on your phone, "
                    "wait a few seconds and click the button again."
                )


def premium_download_section(
    title: str,
    markdown_content: str,
    filename_base: str,
    amount: int = 1500,
    *,
    user_is_paid: bool,
    user_id: str,
) -> None:
    """
    Reusable premium section:

    - Copy-protected preview of markdown_content
    - If user is paid -> show 3 download buttons (MD, DOCX, PDF)
    - If not paid -> show M-Pesa payment form + check status flow
    """
    if not markdown_content.strip():
        st.info(f"Your {title.lower()} will appear here after you run the analysis in the Setup tab.")
        return

    st.markdown(
        f"Below is your **{title}**. "
        "Preview is free. To download in any format, unlock Premium access."
    )

    # Copy-protected preview wrapper
    st.markdown('<div class="no-copy-container">', unsafe_allow_html=True)
    # For both resume and cover letter, treat as Markdown for display
    st.markdown(markdown_content)
    st.markdown("</div>", unsafe_allow_html=True)

    col_md, col_docx, col_pdf = st.columns(3)

    if user_is_paid:
        # Premium: ALL downloads (Markdown, DOCX, PDF)
        docx_bytes = to_docx(markdown_content)
        pdf_bytes = to_pdf(markdown_content)

        with col_md:
            st.download_button(
                label="⬇️ Download as Markdown",
                data=markdown_content,
                file_name=f"{filename_base}.md",
                mime="text/markdown",
            )

        with col_docx:
            st.download_button(
                label="⬇️ Download as Word (DOCX)",
                data=docx_bytes,
                file_name=f"{filename_base}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

        with col_pdf:
            st.download_button(
                label="⬇️ Download as PDF",
                data=pdf_bytes,
                file_name=f"{filename_base}.pdf",
                mime="application/pdf",
            )
    else:
        with col_docx:
            render_payment_gate(feature_name=title, amount=amount, user_id=user_id)

        with col_pdf:
            st.info(f"Premium required to download your {title.lower()} in any format.")


# ============================================================
# Main App
# ============================================================

st.set_page_config(page_title="AI Career Accelerator", layout="wide")
init_state()

# Ensure we have a persistent user id
user_id = get_or_create_user_id()
user_is_paid = is_user_paid(user_id)

# If we were waiting for payment and DB now says paid (via webhook),
# show confirmation and rerun once.
if st.session_state.get("waiting_for_payment") and user_is_paid:
    st.success("Payment confirmed! Refreshing...")
    st.session_state["waiting_for_payment"] = False
    st.session_state["pending_invoice_id"] = None
    st.rerun()

# ====== Copy Protection CSS (Preview Only) ==================
st.markdown(
    """
    <style>
    /* A reusable "no-copy" wrapper for preview content */
    .no-copy-container, .no-copy-container * {
        -webkit-user-select: none !important;  /* Safari/Chrome */
        -moz-user-select: none !important;     /* Firefox */
        -ms-user-select: none !important;      /* IE/Edge */
        user-select: none !important;
        -webkit-touch-callout: none;           /* iOS long-press */
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🚀 AI Career Accelerator")
st.caption("Upload your resume, paste a job description, and generate a complete application kit in minutes.")


# ============================================================
# Sidebar - Resume Input
# ============================================================

with st.sidebar:
    st.header("1. Upload & Configure")

    input_mode = st.radio(
        "How do you want to provide your details?",
        ["Upload PDF Resume", "Enter Text Manually"],
        key="resume_input_mode",
    )

    if input_mode == "Upload PDF Resume":
        uploaded_resume = st.file_uploader(
            "Upload your Resume (PDF)",
            type=["pdf"],
            help="Only machine-readable PDF resumes are supported (no scanned images).",
        )

        if uploaded_resume is not None:
            current_filename = getattr(uploaded_resume, "name", "")

            should_extract = (
                not st.session_state.resume_text.strip()
                or current_filename != st.session_state.last_uploaded_filename
            )

            if should_extract:
                with st.spinner("Extracting text from resume..."):
                    extracted_text = extract_text_from_pdf(uploaded_resume)

                if not extracted_text.strip():
                    st.error(
                        "I couldn't extract any text from this PDF. "
                        "Please ensure it's not a scanned image or try another file."
                    )
                    st.session_state.resume_text = ""
                    st.session_state.last_uploaded_filename = ""
                else:
                    st.session_state.resume_text = extracted_text
                    st.session_state.last_uploaded_filename = current_filename
                    st.success(f"Resume '{current_filename}' loaded and parsed successfully!")

        if st.session_state.resume_text.strip():
            st.caption("✅ Resume text is loaded. You can switch to manual mode to tweak it if you like.")

    else:
        st.text_area(
            "Enter your resume details",
            key="resume_text",
            height=250,
            placeholder="Paste your LinkedIn About section or rough work history here...",
        )
        st.caption("Tip: A rough outline is enough. The AI will polish it into a professional resume.")


# ============================================================
# Main Tabs
# ============================================================

tab_setup, tab_resume, tab_cover, tab_emails = st.tabs(
    ["Setup", "Resume", "Cover Letter", "Email Strategy"]
)

# ------------------------------------------------------------
# TAB 1: Setup (Job Description & Analysis)
# ------------------------------------------------------------
with tab_setup:
    st.header("2. Job Description & Analysis")

    st.text_area(
        "Paste the full Job Description here",
        key="job_description",
        height=350,
        placeholder="Paste the complete job posting...",
    )

    col_btn, col_status = st.columns([1, 3])

    with col_btn:
        analyze_btn = st.button(
            "Analyze Job & Generate Kit",
            type="primary",
            use_container_width=True,
        )

    if analyze_btn:
        # Simple rate limit: 3 free runs per session for non-paid users
        if not user_is_paid and st.session_state.get("analysis_uses", 0) >= 3:
            st.error(
                "You have reached the limit of 3 free analyses. "
                "Unlock Premium to generate unlimited tailored resumes and cover letters."
            )
        elif not st.session_state.resume_text.strip():
            st.error("Please provide your resume details (upload a PDF or enter text manually).")
        elif not st.session_state.job_description.strip():
            st.error("Please paste the full job description.")
        else:
            try:
                with st.spinner("Analyzing job description..."):
                    job_analysis = analyze_job(
                        st.session_state.job_description,
                    )
                    st.session_state.job_analysis_json = job_analysis

                with st.spinner("Rewriting your resume for this role."):
                    rewritten_resume = rewrite_resume(
                        st.session_state.resume_text,
                        st.session_state.job_analysis_json,
                    )
                st.session_state.final_resume_text = rewritten_resume

                with st.spinner("Generating tailored cover letter."):
                    cover_letter = generate_cover_letter(
                        st.session_state.resume_text,
                        st.session_state.job_analysis_json,
                    )
                st.session_state.cover_letter_text = cover_letter

                with st.spinner("Creating follow-up email strategy."):
                    emails = generate_emails(
                        st.session_state.job_analysis_json,
                    )
                st.session_state.follow_up_emails = emails

                # Increment analysis uses only on successful full run
                st.session_state["analysis_uses"] = st.session_state.get("analysis_uses", 0) + 1

                st.success("✅ All done! Check the other tabs for your Application Kit.")
                st.balloons()

            except Exception as e:
                msg = str(e)
                if "Service temporarily unavailable" in msg:
                    st.error("Service temporarily unavailable")
                else:
                    st.error(f"❌ Error during processing: {msg}")

    if st.session_state.job_analysis_json:
        st.subheader("Job Analysis Summary")

        analysis = st.session_state.job_analysis_json
        col1, col2, col3 = st.columns(3)

        with col1:
            st.metric("Role", analysis.get("role_name", "N/A"))
        with col2:
            st.metric("Hard Skills", len(analysis.get("hard_skills", [])))
        with col3:
            st.metric("Total Keywords", len(analysis.get("keywords", [])))

        with st.expander("View Full Analysis JSON"):
            st.json(analysis)


# ------------------------------------------------------------
# TAB 2: Tailored Resume (Premium section)
# ------------------------------------------------------------
with tab_resume:
    st.header("3. Tailored Resume")
    premium_download_section(
        title="ATS-Optimized Resume",
        markdown_content=st.session_state.final_resume_text or "",
        filename_base="tailored_resume",
        amount=1500,
        user_is_paid=user_is_paid,
        user_id=user_id,
    )


# ------------------------------------------------------------
# TAB 3: Custom Cover Letter (Premium section)
# ------------------------------------------------------------
with tab_cover:
    st.header("4. Custom Cover Letter")
    premium_download_section(
        title="Custom Cover Letter",
        markdown_content=st.session_state.cover_letter_text or "",
        filename_base="cover_letter",
        amount=1500,
        user_is_paid=user_is_paid,
        user_id=user_id,
    )


# ------------------------------------------------------------
# TAB 4: Follow-Up Email Strategy
# ------------------------------------------------------------
with tab_emails:
    st.header("5. Follow-Up Email Strategy")

    emails = st.session_state.follow_up_emails or []

    if emails:
        st.markdown(
            "Use this **email sequence** to follow up strategically after applying and interviewing."
        )
        for idx, email in enumerate(emails, start=1):
            label = email.get("label", f"email_{idx}")
            subject = email.get("subject", "No subject")
            body = email.get("body", "")

            with st.expander(f"{idx}. {label} – {subject}"):
                st.markdown(f"**Subject:** {subject}")
                st.markdown("---")
                st.text_area(
                    "Email body",
                    value=body,
                    height=250,
                    key=f"email_body_{idx}",
                )
                st.code(body)
    else:
        st.info("Your follow-up email sequence will appear here after you run the analysis in the Setup tab.")
