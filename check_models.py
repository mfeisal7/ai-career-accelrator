"""Utility script to verify Gemini API connectivity and list available models."""
import os

import google.generativeai as genai

try:
    # Optional: use .env files if python-dotenv is installed
    from dotenv import load_dotenv  # type: ignore[import]
except ImportError:  # pragma: no cover - purely optional dependency
    load_dotenv = None  # type: ignore[assignment]

if load_dotenv is not None:
    # Try to load environment variables from a .env file, if present
    load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("Error: GEMINI_API_KEY not found in environment variables.")
else:
    genai.configure(api_key=api_key)
    print("Available Gemini models (with generateContent support):")
    try:
        for model in genai.list_models():
            if "generateContent" in getattr(model, "supported_generation_methods", []):
                print(f"- {model.name}")
    except Exception as exc:
        print(f"Error connecting to Gemini: {exc}")
