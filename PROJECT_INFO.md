# 💅 NailSalon CFO — Project Architecture & Deployment Memory

> **IMPORTANT MEMORY FOR AI ASSISTANTS & NEW SESSIONS:**  
> Read this file immediately upon opening a new chat session. It contains the cloud infrastructure, repository URLs, database connections, and deployment procedures for the NailSalon CFO application.

---

## 🌐 1. Live Deployment & Repositories

| Component | Platform / URL | Details |
| :--- | :--- | :--- |
| **GitHub Repository** | [`https://github.com/Stavtoub/nailsalon-cfo`](https://github.com/Stavtoub/nailsalon-cfo) | Primary source repo for cloud deployment |
| **Branch** | `main` | Production branch tracked by Streamlit Cloud |
| **Main File** | `app.py` | Core Streamlit application entrypoint |
| **Cloud Hosting** | **Streamlit Community Cloud** ([share.streamlit.io](https://share.streamlit.io/)) | Auto-deploys upon new commit on `main` branch |
| **Live Web App** | Connected to `Stavtoub/nailsalon-cfo` | Accessible from smartphone, tablet, desktop |
| **Database** | **Supabase Cloud (PostgreSQL)** | Persistent real-time database |
| **AI CFO / Analytics** | **Google Gemini API** (`google-genai`) | Virtual CFO Deep Audit, Shift Rebalancing |

---

## 🔄 2. How Cloud Deployment Works

> [!IMPORTANT]
> **MANDATORY RULE:** The user **NEVER** tests or runs the app locally. Every modification must be pushed directly to GitHub via `python sync_github.py` at the end of every task so that Streamlit Community Cloud auto-deploys to production.

The live cloud application on Streamlit Cloud is linked directly to GitHub:
$$\text{Local Code } (\texttt{app.py}) \xrightarrow{\texttt{sync\_github.py}} \text{GitHub } (\texttt{Stavtoub/nailsalon-cfo}) \xrightarrow{\text{Auto-Sync}} \text{Streamlit Cloud (Live Web App)}$$

Whenever code changes are made locally to [`app.py`](app.py):
1. **Local verification:** Syntax & unit logic checked with scratch scripts.
2. **Auto-Push to Cloud:** Execute `python sync_github.py` to push changes to [`https://github.com/Stavtoub/nailsalon-cfo`](https://github.com/Stavtoub/nailsalon-cfo).
3. **Auto-Deploy:** Streamlit Community Cloud automatically detects the new commit on `main` and rebuilds the live application in ~30–60 seconds.

---

## 📤 3. How to Update the Live Web App

### Method A: Web Upload (Direct on GitHub in 10 seconds)
1. Open the repository: [`https://github.com/Stavtoub/nailsalon-cfo`](https://github.com/Stavtoub/nailsalon-cfo)
2. Click **Add file** $\rightarrow$ **Upload files**.
3. Drag and drop the updated local [`app.py`](app.py) from:
   `c:\Users\TOS\Desktop\NailSalon_CFO\app.py`
4. Click the green button **Commit changes**.
5. Streamlit Cloud automatically redeploys within 1 minute!

### Method B: Via Git CLI (if installed)
```bash
git add app.py requirements.txt
git commit -m "UI Overhaul: 4 Executive Hubs & Period Comparison"
git push origin main
```

---

## 🔒 4. Secrets & Security Configuration

* **Never commit secrets to GitHub:**
  * `.streamlit/secrets.toml` is strictly ignored by `.gitignore`.
  * Local CSV files in `uploads/` are strictly ignored by `.gitignore`.
* **Streamlit Cloud Secrets:**
  Configured in Streamlit Cloud Dashboard $\rightarrow$ **App Settings** $\rightarrow$ **Secrets**:
  ```toml
  [supabase]
  url = "https://qxvlfvevxfxpewlkpxgq.supabase.co"
  key = "..."

  [gemini]
  api_key = "..."
  ```

---

## 🏛️ 5. Application Architecture Summary

* **4 Executive Hubs:**
  1. **📊 1. Οικονομικό Ταμπλό (CFO):** Overview KPIs, Κανόνας 100€, Διάγνωση, P&L Bridge, 5-Level Comparison Engine (Day/Week/Month/Year/Custom), Profit Simulator & Virtual CFO Deep Audit.
  2. **👥 2. Ομάδα, Βάρδιες & Idle Time:** Staff Payroll & Performance, Idle Timeline (Gantt & Heatmap), Smart Roster Sandbox & AI Shift Rebalancer.
  3. **💅 3. Υπηρεσίες, Treatwell & Ταμείο:** Service Profitability Matrix, Treatwell Reconciliation & Channel Group, Retail POS.
  4. **⚙️ 4. Κέντρο Ελέγχου & Ρυθμίσεις:** Weekly Shifts Editor, CSV Importer, Expenses & Invoices, Accountant Payroll, Service Catalog.
* **Non-Calculated Direct Toggle:**
  Tracks completed Direct bookings entered with 0.00 € in Treatwell using catalog estimation without altering official turnover.
