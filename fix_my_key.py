import os

# 1. Define the correct path
current_dir = os.getcwd()
streamlit_folder = os.path.join(current_dir, ".streamlit")
secrets_file = os.path.join(streamlit_folder, "secrets.toml")

print(f"📂 Project Folder: {current_dir}")
print(f"📂 Target File:    {secrets_file}")
print("-" * 50)

# 2. Ask for the key
new_key = input("PASTE YOUR NEW API KEY HERE (Press Enter): ").strip()

# Clean up quotes if the user pasted them by accident
new_key = new_key.replace('"', '').replace("'", "")

if not new_key.startswith("AIza"):
    print("❌ ERROR: That doesn't look like a Google API key. It should start with 'AIza'.")
    exit()

# 3. Create folder if missing
if not os.path.exists(streamlit_folder):
    os.makedirs(streamlit_folder)
    print("✅ Created .streamlit folder")

# 4. Write the file
content = f'GEMINI_API_KEY = "{new_key}"'
with open(secrets_file, "w") as f:
    f.write(content)

print("-" * 50)
print("✅ SUCCESS! creating secrets.toml")
print(f"📝 Wrote content: {content}")
print("🚀 You can now run 'streamlit run app.py'")