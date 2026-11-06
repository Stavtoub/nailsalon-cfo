# NailSalon CFO — Mandatory Agent Rules

## 1. Always Auto-Sync to GitHub (Live Streamlit Cloud)
* **CRITICAL USER RULE:** The user **NEVER** runs or views the application locally (`localhost:8501`). The user only tests and views the application online on **Streamlit Community Cloud**.
* **AUTOMATIC ACTION:** Whenever any changes or bug fixes are made to `app.py` or other project files, you **MUST ALWAYS** run:
  ```bash
  python sync_github.py
  ```
  immediately before concluding the task.
* Never ask the user to test locally. Ensure the commit is pushed and advise the user to refresh the live web app in 30–60 seconds.

## 2. Secrets & Security
* Never commit or expose credentials from `.streamlit/secrets.toml`.
* `sync_github.py` reads `GITHUB_TOKEN` safely and only syncs public code files.
