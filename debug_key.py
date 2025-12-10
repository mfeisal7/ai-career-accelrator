import os
import streamlit as st

st.title("🔑 API Key Diagnostic")

st.write("Checking where your app is finding the key...")

# 1. Check Environment Variables
env_key = os.getenv("GEMINI_API_KEY")
if env_key:
    st.error(f"⚠️ FOUND in Environment Variables: `{env_key[:4]}...` (Length: {len(env_key)})")
    st.warning("The app is using this Environment Variable, ignoring secrets.toml.")
else:
    st.info("✅ NOT found in Environment Variables (Good for local dev).")

# 2. Check Streamlit Secrets
try:
    secret_key = st.secrets["GEMINI_API_KEY"]
    if secret_key:
        st.success(f"✅ FOUND in secrets.toml: `{secret_key[:4]}...` (Length: {len(secret_key)})")
    else:
        st.error("❌ Found in secrets.toml, but it is empty!")
except FileNotFoundError:
    st.error("❌ secrets.toml NOT found. Check if the folder is named `.streamlit` (with a dot) and file is `secrets.toml`.")
except KeyError:
    st.error("❌ secrets.toml found, but 'GEMINI_API_KEY' is missing inside it.")
except Exception as e:
    st.error(f"❌ Error reading secrets: {e}")

st.write("---")
st.caption("If you see the key in Environment Variables, you must delete that variable from your system or .env file.")