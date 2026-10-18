import streamlit as st
from supabase import create_client, Client
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import json
import re
import os
import unicodedata
from datetime import datetime, timedelta, date

# Έλεγχος αν υπάρχει η βιβλιοθήκη του Gemini
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

# --- 1. ΡΥΘΜΙΣΗ ΣΕΛΙΔΑΣ & CUSTOM STYLING ---
st.set_page_config(
    page_title="Nail Salon CFO & Shift Optimizer",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.html("""
<style>
    .cfo-card {
        padding: 16px 20px;
        border-radius: 12px;
        margin-bottom: 12px;
        font-size: 0.93rem;
        line-height: 1.55;
        border-width: 1px;
        border-style: solid;
        height: 100%;
    }
    .cfo-card-red {
        background-color: rgba(239, 68, 68, 0.08);
        border-color: rgba(239, 68, 68, 0.30);
        border-left: 5px solid #ef4444;
    }
    .cfo-card-amber {
        background-color: rgba(245, 158, 11, 0.08);
        border-color: rgba(245, 158, 11, 0.30);
        border-left: 5px solid #f59e0b;
    }
    .cfo-card-green {
        background-color: rgba(16, 185, 129, 0.08);
        border-color: rgba(16, 185, 129, 0.30);
        border-left: 5px solid #10b981;
    }
    .cfo-card-blue {
        background-color: rgba(59, 130, 246, 0.08);
        border-color: rgba(59, 130, 246, 0.30);
        border-left: 5px solid #3b82f6;
    }
    .cfo-card-purple {
        background-color: rgba(139, 92, 246, 0.08);
        border-color: rgba(139, 92, 246, 0.30);
        border-left: 5px solid #8b5cf6;
    }
</style>
""")

# --- 2. ΒΟΗΘΗΤΙΚΕΣ ΣΥΝΑΡΤΗΣΕΙΣ ΚΑΝΟΝΙΚΟΠΟΙΗΣΗΣ & ΒΑΡΔΙΩΝ ---
DAYS_GR = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο"]
MONTHS_GR = {
    1: "Ιανουάριος", 2: "Φεβρουάριος", 3: "Μάρτιος", 4: "Απρίλιος",
    5: "Μάιος", 6: "Ιούνιος", 7: "Ιούλιος", 8: "Αύγουστος",
    9: "Σεπτέμβριος", 10: "Οκτώβριος", 11: "Νοέμβριος", 12: "Δεκέμβριος"
}

DEFAULT_SCREENSHOT_SCHEDULE = [
    {"staff_name": "Νάντια",      "Δευτέρα": "18:00 - 20:30", "Τρίτη": "",              "Τετάρτη": "17:30 - 20:30", "Πέμπτη": "18:00 - 20:00", "Παρασκευή": "18:00 - 20:30", "Σάββατο": ""},
    {"staff_name": "Κατερίνα",    "Δευτέρα": "09:00 - 15:00", "Τρίτη": "14:30 - 20:30", "Τετάρτη": "09:00 - 15:00", "Πέμπτη": "14:30 - 20:30", "Παρασκευή": "09:00 - 15:00", "Σάββατο": ""},
    {"staff_name": "Κατερίνα Π.", "Δευτέρα": "12:30 - 20:30", "Τρίτη": "10:00 - 18:00", "Τετάρτη": "10:00 - 18:00", "Πέμπτη": "10:00 - 18:00", "Παρασκευή": "10:00 - 18:00", "Σάββατο": ""},
    {"staff_name": "Δόμνα",       "Δευτέρα": "",              "Τρίτη": "12:30 - 20:30", "Τετάρτη": "12:30 - 20:30", "Πέμπτη": "12:30 - 20:30", "Παρασκευή": "12:30 - 20:30", "Σάββατο": "09:00 - 16:00"},
    {"staff_name": "Δημήτρης",    "Δευτέρα": "09:00 - 20:30", "Τρίτη": "09:00 - 20:30", "Τετάρτη": "09:00 - 20:30", "Πέμπτη": "09:00 - 20:30", "Παρασκευή": "09:00 - 20:30", "Σάββατο": "09:00 - 16:00"},
    {"staff_name": "Αντιγόνη",    "Δευτέρα": "",              "Τρίτη": "16:30 - 20:30", "Τετάρτη": "16:30 - 20:30", "Πέμπτη": "16:30 - 20:30", "Παρασκευή": "16:30 - 20:30", "Σάββατο": "12:00 - 16:00"}
]

def normalize_greek_name(text):
    if pd.isna(text) or not str(text).strip():
        return "ΑΓΝΩΣΤΟΣ"
    text = str(text).strip()
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', text).upper()

def slugify(text):
    if not text:
        return "UNKNOWN"
    text = normalize_greek_name(text)
    text = re.sub(r'[^a-zA-Z0-9\u0370-\u03FF]', '_', text)
    return re.sub(r'_+', '_', text).strip('_')

def normalize_to_monday_str(date_str):
    try:
        dt = pd.to_datetime(date_str, dayfirst=not re.match(r'^\d{4}-', str(date_str).strip()), errors="coerce")
        if pd.isna(dt):
            dt = pd.to_datetime(date_str, errors="coerce")
        if pd.notna(dt):
            mon_d = dt.date() - timedelta(days=dt.date().weekday())
            return mon_d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return "2026-10-19"

def parse_shift_intervals(shift_str):
    if not shift_str or pd.isna(shift_str):
        return []
    s = str(shift_str).strip()
    if not s or "κλειστ" in s.lower() or "ρεπ" in s.lower() or s == "-":
        return []
    intervals = []
    matches = re.findall(r'(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})', s)
    for h1, m1, h2, m2 in matches:
        st_h = int(h1) + int(m1) / 60.0
        en_h = int(h2) + int(m2) / 60.0
        if en_h > st_h:
            intervals.append((st_h, en_h))
    return intervals

def calc_shift_hours(shift_str):
    return round(sum(en - st for st, en in parse_shift_intervals(shift_str)), 2)

def deduplicate_and_clean_schedule_rows(rows_list):
    if not isinstance(rows_list, list):
        return []
    merged_by_name = {}
    for item in rows_list:
        if not isinstance(item, dict):
            continue
        name = str(item.get("staff_name", "")).strip()
        if not name:
            continue
        norm_key = name.lower()
        if norm_key not in merged_by_name:
            merged_by_name[norm_key] = {"staff_name": name}
            for d in DAYS_GR:
                merged_by_name[norm_key][d] = str(item.get(d, "") or "").strip()
        else:
            for d in DAYS_GR:
                existing_val = merged_by_name[norm_key].get(d, "")
                new_val = str(item.get(d, "") or "").strip()
                if not existing_val and new_val:
                    merged_by_name[norm_key][d] = new_val

    cleaned = []
    for norm_key, row_obj in merged_by_name.items():
        if "δημ" not in norm_key:
            tot_h = sum(calc_shift_hours(row_obj.get(d, "")) for d in DAYS_GR)
            if tot_h >= 64.0:
                for d in DAYS_GR:
                    row_obj[d] = ""
        cleaned.append(row_obj)
    return cleaned

def calc_schedule_tech_hours(rows_list):
    if not isinstance(rows_list, list):
        return 0.0
    return round(sum(
        sum(calc_shift_hours(r.get(d, "")) for d in DAYS_GR)
        for r in rows_list
        if isinstance(r, dict) and "δημ" not in str(r.get("staff_name", "")).lower()
    ), 2)

# --- 3. ΑΣΦΑΛΗΣ ΑΝΑΓΝΩΣΗ SECRETS, ΚΛΕΙΔΩΜΑ ΚΩΔΙΚΟΥ (APP_PASSWORD) & ΣΥΝΔΕΣΗ SUPABASE ---
def get_secret_safe(primary_key: str, sub_key: str = "") -> str:
    """Διαβάζει με ασφάλεια κλειδιά είτε από το root των Secrets είτε μέσα από την ενότητα [supabase]."""
    try:
        if primary_key in st.secrets:
            val = st.secrets[primary_key]
            if isinstance(val, str) and val.strip():
                return val.strip()
        if "supabase" in st.secrets:
            sup = st.secrets["supabase"]
            if primary_key in sup and str(sup[primary_key]).strip():
                return str(sup[primary_key]).strip()
            if sub_key and sub_key in sup and str(sup[sub_key]).strip():
                return str(sup[sub_key]).strip()
    except Exception:
        pass
    return ""

app_password_secret = get_secret_safe("APP_PASSWORD", "app_password")
if app_password_secret and not st.session_state.get("app_authenticated", False):
    _, col_auth, _ = st.columns([1, 1.3, 1])
    with col_auth:
        st.write("")
        st.write("")
        with st.container(border=True):
            st.subheader("🔒 Κλειδωμένη Πρόσβαση — Nail Salon CFO")
            st.caption("Εισάγετε τον Κωδικό Πρόσβασης της εφαρμογής για να προβάλετε τα οικονομικά δεδομένα.")
            entered_pwd = st.text_input("Κωδικός Πρόσβασης", type="password", key="app_pwd_gate_input")
            btn_login = st.button("Είσοδος στην Εφαρμογή", type="primary", icon=":material/lock_open:", width="stretch")
            if btn_login or entered_pwd:
                if entered_pwd == app_password_secret:
                    st.session_state["app_authenticated"] = True
                    st.rerun()
                else:
                    st.error("❌ Λάθος κωδικός πρόσβασης. Δοκιμάστε ξανά.")
    st.stop()

@st.cache_resource
def init_connection():
    url = get_secret_safe("SUPABASE_URL", "url")
    key = get_secret_safe("SUPABASE_KEY", "key")
    return create_client(url, key)

try:
    supabase: Client = init_connection()
except Exception as e:
    st.error(f"Σφάλμα σύνδεσης με Supabase: {e}", icon=":material/error:")
    st.stop()

@st.cache_data(ttl=3600, show_spinner="Φόρτωση πλήρους ιστορικού από τη βάση...")
def load_all_db_data(cache_v: int = 3):
    """
    Φορτώνει όλα τα δεδομένα μία φορά ανά ώρα (ttl=3600) ώστε να μην καταναλώνει Egress στο Free Plan της Supabase.
    Χρησιμοποιεί αυτόματη σελιδοποίηση (.range) για να ξεπερνά το όριο των 1.000 γραμμών του Supabase API.
    """
    staff = pd.DataFrame(supabase.table("config_staff").select("*").execute().data)
    services_raw = pd.DataFrame(supabase.table("config_services").select("*").execute().data)
    expenses = pd.DataFrame(supabase.table("expenses_log").select("*").execute().data)

    # Σελιδοποίηση (Pagination) για ανάκτηση όλων των ραντεβού (π.χ. 17.573+ εγγραφές)
    all_bookings_rows = []
    offset = 0
    batch_size = 1000
    while True:
        res_batch = supabase.table("bookings_master").select("*").range(offset, offset + batch_size - 1).execute().data
        if not res_batch:
            break
        all_bookings_rows.extend(res_batch)
        if len(res_batch) < batch_size:
            break
        offset += batch_size

    bookings = pd.DataFrame(all_bookings_rows)

    schedules_dict = {}
    app_settings_dict = {}
    if not services_raw.empty and 'category' in services_raw.columns:
        sched_rows = services_raw[services_raw['category'] == '__SHIFT_SCHEDULE__']
        for _, r in sched_rows.iterrows():
            try:
                raw_list = json.loads(r.get('price_variants_json', '[]'))
                clean_list = deduplicate_and_clean_schedule_rows(raw_list)
                if calc_schedule_tech_hours(clean_list) > 0 or str(r['service_name']) == 'WEEK_DEFAULT':
                    schedules_dict[str(r['service_name'])] = clean_list
            except Exception:
                pass

        cfg_rows = services_raw[services_raw['category'] == '__APP_CONFIG__']
        for _, r in cfg_rows.iterrows():
            app_settings_dict[str(r['service_name'])] = str(r.get('price_variants_json', '') or '')

        services = services_raw[~services_raw['category'].isin(['__SHIFT_SCHEDULE__', '__APP_CONFIG__'])].copy()
    else:
        services = services_raw

    return staff, services, expenses, bookings, schedules_dict, app_settings_dict

df_staff_db, df_services_db, df_expenses_db, df_bookings_db, db_schedules, db_app_settings = load_all_db_data()

def refresh_data():
    st.cache_data.clear()
    st.rerun()

def save_gemini_key_persistent(new_key: str):
    """
    Αποθηκεύει το Gemini API Key μόνο στο τοπικό .streamlit/secrets.toml (όταν τρέχει τοπικά).
    Στο Cloud διαβάζεται απευθείας από τα κρυπτογραφημένα Streamlit Secrets χωρίς να εκτίθεται στη βάση ή στον browser.
    """
    clean_k = str(new_key or "").strip()
    secrets_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".streamlit", "secrets.toml")
    if os.path.exists(secrets_path) and clean_k:
        try:
            with open(secrets_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "GEMINI_API_KEY" in content:
                content = re.sub(r'GEMINI_API_KEY\s*=\s*"[^"]*"', f'GEMINI_API_KEY = "{clean_k}"', content)
            else:
                content = content.rstrip() + f'\nGEMINI_API_KEY = "{clean_k}"\n'
            with open(secrets_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception:
            pass

def save_app_config_json(config_key: str, payload_obj):
    """Αποθηκεύει μόνιμα JSON δεδομένα (π.χ. τελευταία έκθεση Deep Audit ή Hybrid AI CFO) στη βάση."""
    try:
        json_str = json.dumps(payload_obj, ensure_ascii=False)
        supabase.table("config_services").upsert([{
            "service_name": config_key,
            "category": "__APP_CONFIG__",
            "base_duration": 0,
            "gross_price": 0.0,
            "material_cost_estimate": 0.0,
            "price_variants_json": json_str
        }]).execute()
        db_app_settings[config_key] = json_str
    except Exception:
        pass

def load_app_config_json(config_key: str):
    """Διαβάζει αποθηκευμένο JSON από το db_app_settings."""
    raw_val = db_app_settings.get(config_key, "")
    if not raw_val:
        return None
    try:
        return json.loads(raw_val)
    except Exception:
        return None

# --- 4. ΠΟΛΥ-ΠΑΡΟΧΟΣ ΚΛΗΤΗΡΑΣ AI (GEMINI PRO/FLASH, OPENAI CHATGPT, ANTHROPIC CLAUDE) ---
import base64
import urllib.request
import urllib.error

DEFAULT_MODELS_BY_PROVIDER = {
    "Google Gemini": [
        "gemini-2.5-pro",
        "gemini-3.1-pro-preview",
        "gemini-pro-latest",
        "gemini-2.5-flash",
        "gemini-3.0-flash",
        "gemini-3-flash-preview",
        "gemini-flash-latest"
    ],
    "OpenAI (ChatGPT)": [
        "gpt-5",
        "o3",
        "gpt-4.1",
        "gpt-4o",
        "o4-mini",
        "gpt-4o-mini"
    ],
    "Anthropic (Claude)": [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-3-7-sonnet-latest",
        "claude-3-5-sonnet-latest"
    ]
}

def detect_ai_provider(api_key: str, model_name: str, explicit_provider: str = "") -> str:
    m_low = str(model_name or "").strip().lower()
    k_str = str(api_key or "").strip()
    if m_low.startswith("claude-"):
        return "Anthropic (Claude)"
    if m_low.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "OpenAI (ChatGPT)"
    if m_low.startswith("gemini"):
        return "Google Gemini"
    if k_str.startswith("sk-ant-"):
        return "Anthropic (Claude)"
    if k_str.startswith("sk-"):
        return "OpenAI (ChatGPT)"
    if explicit_provider in DEFAULT_MODELS_BY_PROVIDER:
        return explicit_provider
    return "Google Gemini"

def fetch_live_models_from_api(api_key: str, provider_name: str) -> list:
    """Ρωτάει ζωντανά το API του παρόχου και επιστρέφει όλα τα ενεργά μοντέλα που υποστηρίζει το κλειδί."""
    clean_k = str(api_key or "").strip()
    if not clean_k:
        return []
    discovered = []
    try:
        if provider_name == "OpenAI (ChatGPT)" or clean_k.startswith("sk-") and not clean_k.startswith("sk-ant-"):
            req = urllib.request.Request(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {clean_k}", "User-Agent": "NailSalon-CFO"},
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("data", []):
                    mid = str(item.get("id", ""))
                    if any(mid.startswith(p) for p in ("gpt-", "o1", "o3", "o4", "chatgpt")) and not any(x in mid for x in ("audio", "realtime", "tts", "whisper", "transcribe", "embedding", "instruct", "search")):
                        discovered.append(mid)
            discovered.sort(reverse=True)
        elif provider_name == "Anthropic (Claude)" or clean_k.startswith("sk-ant-"):
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": clean_k,
                    "anthropic-version": "2023-06-01",
                    "User-Agent": "NailSalon-CFO"
                },
                method="GET"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("data", []):
                    mid = str(item.get("id", ""))
                    if mid:
                        discovered.append(mid)
        else:
            if GENAI_AVAILABLE:
                client = genai.Client(api_key=clean_k)
                for m in client.models.list():
                    m_id = getattr(m, "name", "").replace("models/", "")
                    if "gemini" in m_id and "embedding" not in m_id and "imagen" not in m_id and "veo" not in m_id:
                        discovered.append(m_id)
                # Προτεραιότητα στα pro μοντέλα
                discovered.sort(key=lambda x: (0 if "2.5-pro" in x else (1 if "pro" in x else 2), x))
    except Exception:
        pass
    return discovered

def _extract_multimodal_parts(contents):
    """Μετατρέπει το contents σε (text_prompt, image_bytes, mime_type) αν περιέχει εικόνα."""
    if isinstance(contents, str):
        return contents, None, None
    text_parts = []
    img_bytes = None
    mime_t = "image/png"
    if isinstance(contents, (list, tuple)):
        for item in contents:
            if isinstance(item, str):
                text_parts.append(item)
            elif hasattr(item, "inline_data") and getattr(item, "inline_data", None) is not None:
                inl = item.inline_data
                img_bytes = getattr(inl, "data", None)
                mime_t = getattr(inl, "mime_type", "image/png") or "image/png"
            elif isinstance(item, dict) and "data" in item:
                img_bytes = item["data"]
                mime_t = item.get("mime_type", "image/png")
    return "\n".join(text_parts), img_bytes, mime_t

def call_gemini_smart(api_key, contents, expect_json=False, preferred_model="gemini-2.5-pro", provider="auto"):
    """
    Κεντρικός δρομολογητής AI CFO:
    Υποστηρίζει Google Gemini (Pro & Flash), OpenAI ChatGPT (gpt-5, o3, gpt-4.1, gpt-4o) και Anthropic Claude (Sonnet/Opus),
    καθώς και οποιοδήποτε μελλοντικό/custom μοντέλο.
    """
    prov = detect_ai_provider(api_key, preferred_model, provider)

    # 1. OpenAI (ChatGPT)
    if prov == "OpenAI (ChatGPT)":
        text_prompt, img_bytes, mime_t = _extract_multimodal_parts(contents)
        candidate_models = [preferred_model, "gpt-4o", "gpt-4.1", "gpt-4o-mini"]
        seen = set()
        ordered_models = [m for m in candidate_models if m and not (m in seen or seen.add(m))]
        last_err = None
        for m_name in ordered_models:
            try:
                if img_bytes:
                    b64_img = base64.b64encode(img_bytes).decode("ascii")
                    user_content = [
                        {"type": "text", "text": text_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_t};base64,{b64_img}"}}
                    ]
                else:
                    user_content = text_prompt

                payload = {
                    "model": m_name,
                    "messages": [
                        {"role": "system", "content": "Είσαι ο Senior CFO του Nail Salon. Απαντάς πάντα με απόλυτη μαθηματική ακρίβεια στα Ελληνικά."},
                        {"role": "user", "content": user_content}
                    ]
                }
                if expect_json and not img_bytes and not m_name.startswith(("o1", "o3")):
                    # Αν το prompt ζητάει JSON λίστα ή αντικείμενο, προσθέτουμε σαφή οδηγία
                    payload["messages"][0]["content"] += " Επέστρεψε αποκλειστικά έγκυρο JSON χωρίς markdown backticks."

                req = urllib.request.Request(
                    "https://api.openai.com/v1/chat/completions",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {api_key.strip()}",
                        "Content-Type": "application/json",
                        "User-Agent": "NailSalon-CFO"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=90) as resp:
                    res_data = json.loads(resp.read().decode("utf-8"))
                    return res_data["choices"][0]["message"]["content"], m_name
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                last_err = RuntimeError(f"OpenAI HTTP {e.code} ({m_name}): {err_body}")
                if e.code in (404, 400) and "model" in err_body.lower():
                    continue
                raise last_err
            except Exception as e:
                last_err = e
                raise e
        raise last_err

    # 2. Anthropic (Claude)
    if prov == "Anthropic (Claude)":
        text_prompt, img_bytes, mime_t = _extract_multimodal_parts(contents)
        candidate_models = [preferred_model, "claude-sonnet-4-6", "claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest"]
        seen = set()
        ordered_models = [m for m in candidate_models if m and not (m in seen or seen.add(m))]
        last_err = None
        for m_name in ordered_models:
            try:
                if img_bytes:
                    b64_img = base64.b64encode(img_bytes).decode("ascii")
                    user_content = [
                        {"type": "image", "source": {"type": "base64", "media_type": mime_t, "data": b64_img}},
                        {"type": "text", "text": text_prompt}
                    ]
                else:
                    user_content = text_prompt

                payload = {
                    "model": m_name,
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": user_content}]
                }
                req = urllib.request.Request(
                    "https://api.anthropic.com/v1/messages",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "x-api-key": api_key.strip(),
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                        "User-Agent": "NailSalon-CFO"
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=90) as resp:
                    res_data = json.loads(resp.read().decode("utf-8"))
                    return res_data["content"][0]["text"], m_name
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                last_err = RuntimeError(f"Anthropic HTTP {e.code} ({m_name}): {err_body}")
                if e.code in (404, 400) and "model" in err_body.lower():
                    continue
                raise last_err
            except Exception as e:
                last_err = e
                raise e
        raise last_err

    # 3. Google Gemini (Pro & Flash με έξυπνο fallback αν υπάρξει 404 ή Free-Tier Quota 429 σε συγκεκριμένο μοντέλο)
    client = genai.Client(api_key=api_key)
    candidate_models = [
        preferred_model,
        "gemini-2.5-pro",
        "gemini-pro-latest",
        "gemini-2.5-flash",
        "gemini-3.0-flash",
        "gemini-3-flash-preview",
        "gemini-flash-latest"
    ]
    seen = set()
    ordered_models = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

    cfg = types.GenerateContentConfig(response_mime_type="application/json") if expect_json else None
    last_err = None

    for m_name in ordered_models:
        try:
            resp = client.models.generate_content(model=m_name, contents=contents, config=cfg)
            return resp.text, m_name
        except Exception as e:
            last_err = e
            err_str = str(e)
            if any(tok in err_str for tok in ("404", "NOT_FOUND", "no longer available", "429", "RESOURCE_EXHAUSTED", "quota")):
                continue
            else:
                raise e

    try:
        for m in client.models.list():
            m_id = getattr(m, "name", "").replace("models/", "")
            if "gemini" in m_id and ("pro" in m_id or "flash" in m_id) and "embedding" not in m_id:
                try:
                    resp = client.models.generate_content(model=m_id, contents=contents, config=cfg)
                    return resp.text, m_id
                except Exception:
                    continue
    except Exception:
        pass

    raise last_err

def clean_json_response(raw_text):
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', str(raw_text).strip(), flags=re.MULTILINE)
    return json.loads(cleaned)

def resolve_week_schedule_smart(monday_str, db_sched_dict, use_rolling_fallback=True):
    if monday_str in ("DEFAULT", "WEEK_DEFAULT"):
        if "WEEK_DEFAULT" in db_sched_dict and calc_schedule_tech_hours(db_sched_dict["WEEK_DEFAULT"]) > 0:
            return db_sched_dict["WEEK_DEFAULT"], True, "WEEK_DEFAULT"
        return DEFAULT_SCREENSHOT_SCHEDULE, False, "DEFAULT_BUILTIN"

    target_key = f"WEEK_{monday_str}" if not str(monday_str).startswith("WEEK_") else str(monday_str)
    if target_key in db_sched_dict and calc_schedule_tech_hours(db_sched_dict[target_key]) > 0:
        return db_sched_dict[target_key], True, target_key

    if use_rolling_fallback:
        dated_keys = sorted([
            k for k in db_sched_dict.keys()
            if re.match(r'^WEEK_\d{4}-\d{2}-\d{2}$', k) and calc_schedule_tech_hours(db_sched_dict[k]) > 0
        ])
        prev_keys = [k for k in dated_keys if k <= target_key]
        if prev_keys:
            best_prev = prev_keys[-1]
            return db_sched_dict[best_prev], False, best_prev
        if dated_keys:
            return db_sched_dict[dated_keys[0]], False, dated_keys[0]

    if "WEEK_DEFAULT" in db_sched_dict and calc_schedule_tech_hours(db_sched_dict["WEEK_DEFAULT"]) > 0:
        return db_sched_dict["WEEK_DEFAULT"], False, "WEEK_DEFAULT"

    return DEFAULT_SCREENSHOT_SCHEDULE, False, "DEFAULT_BUILTIN"

def save_multiple_week_schedules_to_db(schedules_map, update_default=False):
    dedup_map = {}
    last_rows = None

    for raw_key, s_rows in schedules_map.items():
        clean_rows = deduplicate_and_clean_schedule_rows(s_rows)
        if not clean_rows:
            continue
        raw_str = str(raw_key).strip()
        if raw_str in ("WEEK_DEFAULT", "DEFAULT"):
            if calc_schedule_tech_hours(clean_rows) > 0:
                dedup_map["WEEK_DEFAULT"] = clean_rows
            continue

        if calc_schedule_tech_hours(clean_rows) <= 0:
            continue

        date_part = re.sub(r'^WEEK_?', '', raw_str, flags=re.IGNORECASE).strip()
        clean_mon = normalize_to_monday_str(date_part)
        clean_k = f"WEEK_{clean_mon}"
        dedup_map[clean_k] = clean_rows
        last_rows = clean_rows

    if update_default and last_rows and calc_schedule_tech_hours(last_rows) > 0:
        dedup_map["WEEK_DEFAULT"] = last_rows

    payloads = [
        {
            "service_name": k,
            "category": "__SHIFT_SCHEDULE__",
            "base_duration": 0,
            "gross_price": 0.0,
            "material_cost_estimate": 0.0,
            "price_variants_json": json.dumps(v, ensure_ascii=False)
        }
        for k, v in dedup_map.items()
    ]

    if payloads:
        for i in range(0, len(payloads), 50):
            supabase.table("config_services").upsert(payloads[i:i+50]).execute()
    return len(dedup_map)

def generate_dynamic_shift_insights(sched_cap_matrix, demand_matrix, staff_per_hour_grid, avg_hourly_rate=7.5):
    excess_matrix = sched_cap_matrix - demand_matrix
    overstaffed_blocks = []
    understaffed_blocks = []

    for d_idx, d_name in enumerate(DAYS_GR):
        h_idx = 0
        while h_idx < 12:
            if excess_matrix[d_idx, h_idx] >= 0.70:
                start_idx = h_idx
                while h_idx + 1 < 12 and excess_matrix[d_idx, h_idx + 1] >= 0.70:
                    h_idx += 1
                end_idx = h_idx
                avg_sched = float(np.mean(sched_cap_matrix[d_idx, start_idx:end_idx+1]))
                avg_dem = float(np.mean(demand_matrix[d_idx, start_idx:end_idx+1]))
                avg_exc = float(np.mean(excess_matrix[d_idx, start_idx:end_idx+1]))
                total_wasted_h = float(np.sum(excess_matrix[d_idx, start_idx:end_idx+1]))
                block_staff = sorted(list(set(s for idx in range(start_idx, end_idx+1) for s in staff_per_hour_grid[d_idx][idx])))
                overstaffed_blocks.append({
                    "Ημέρα": d_name,
                    "Ζώνη Ώρας": f"{start_idx+9:02d}:00–{end_idx+10:02d}:00",
                    "Διάρκεια (h)": end_idx - start_idx + 1,
                    "Μ.Ο. Βάρδιας": round(avg_sched, 1),
                    "Μ.Ο. Ζήτησης": round(avg_dem, 1),
                    "Πλεόνασμα (Τεχν.)": round(avg_exc, 1),
                    "Χαμένες Ώρες/Εβδ.": round(total_wasted_h, 1),
                    "Κόστος/Εβδ. (€)": round(total_wasted_h * avg_hourly_rate, 2),
                    "Τεχνίτριες στη Βάρδια": ", ".join(block_staff) if block_staff else "-"
                })
            h_idx += 1

        h_idx = 0
        while h_idx < 12:
            if excess_matrix[d_idx, h_idx] <= -0.30:
                start_idx = h_idx
                while h_idx + 1 < 12 and excess_matrix[d_idx, h_idx + 1] <= -0.30:
                    h_idx += 1
                end_idx = h_idx
                avg_sched = float(np.mean(sched_cap_matrix[d_idx, start_idx:end_idx+1]))
                avg_dem = float(np.mean(demand_matrix[d_idx, start_idx:end_idx+1]))
                avg_def = float(np.mean(-excess_matrix[d_idx, start_idx:end_idx+1]))
                total_missing_h = float(np.sum(-excess_matrix[d_idx, start_idx:end_idx+1]))
                block_staff = sorted(list(set(s for idx in range(start_idx, end_idx+1) for s in staff_per_hour_grid[d_idx][idx])))
                understaffed_blocks.append({
                    "Ημέρα": d_name,
                    "Ζώνη Ώρας": f"{start_idx+9:02d}:00–{end_idx+10:02d}:00",
                    "Διάρκεια (h)": end_idx - start_idx + 1,
                    "Μ.Ο. Βάρδιας": round(avg_sched, 1),
                    "Μ.Ο. Ζήτησης": round(avg_dem, 1),
                    "Έλλειμμα (Τεχν.)": round(avg_def, 1),
                    "Ώρες Υπερφόρτωσης/Εβδ.": round(total_missing_h, 1),
                    "Τεχνίτριες στη Βάρδια": ", ".join(block_staff) if block_staff else "Καμία (0)"
                })
            h_idx += 1

    overstaffed_blocks.sort(key=lambda x: x["Χαμένες Ώρες/Εβδ."], reverse=True)
    understaffed_blocks.sort(key=lambda x: x["Ώρες Υπερφόρτωσης/Εβδ."], reverse=True)
    return overstaffed_blocks, understaffed_blocks

# --- 5. ΕΞΥΠΝΟΣ ΕΝΤΟΠΙΣΜΟΣ ΠΛΗΡΩΝ ΕΒΔΟΜΑΔΩΝ, ΜΗΝΩΝ, ΕΤΩΝ & ΟΡΙΩΝ ΒΑΣΗΣ ---
if not df_bookings_db.empty:
    df_bookings_db['appointment_date_dt'] = pd.to_datetime(df_bookings_db['appointment_date'])
    df_bookings_db['monday_date_str'] = df_bookings_db['appointment_date_dt'].dt.date.apply(
        lambda d: (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    )
    db_min_d = df_bookings_db['appointment_date_dt'].min().date()
    db_max_d = df_bookings_db['appointment_date_dt'].max().date()

    comp_temp = df_bookings_db[df_bookings_db['status_group'] == 'COMPLETED']
    wk_counts = comp_temp.groupby('monday_date_str').size().to_dict()
    full_week_mondays = sorted([
        datetime.strptime(m_s, "%Y-%m-%d").date()
        for m_s, cnt in wk_counts.items() if cnt >= 25
    ])
    if full_week_mondays:
        full_cycle_start_d = full_week_mondays[0]
        full_cycle_end_d = full_week_mondays[-1] + timedelta(days=6)
    else:
        full_cycle_start_d = db_min_d
        full_cycle_end_d = db_max_d
else:
    db_min_d = date(2024, 7, 1)
    db_max_d = date(2026, 10, 25)
    full_cycle_start_d = date(2024, 7, 1)
    full_cycle_end_d = date(2026, 10, 25)
    wk_counts = {}
    full_week_mondays = []

history_span_days = (full_cycle_end_d - full_cycle_start_d).days + 1
# Εφόσον στη βάση υπάρχει ιστορικό >= 75 ημερών (τώρα έχουμε 2,5 έτη / 17.938 ραντεβού), η προεπιλογή είναι 100%!
default_tw_real_new_pct = 100 if history_span_days >= 75 else 35

# Ανάκτηση αποθηκευμένων API Keys αποκλειστικά από τα κρυπτογραφημένα Secrets (χωρίς έκθεση στον browser)
secret_gemini_key = get_secret_safe("GEMINI_API_KEY", "gemini_api_key")
secret_openai_key = get_secret_safe("OPENAI_API_KEY", "openai_api_key")
secret_anthropic_key = get_secret_safe("ANTHROPIC_API_KEY", "anthropic_api_key")

# --- 6. ΠΛΑΪΝΗ ΜΠΑΡΑ (SIDEBAR) ΓΙΑ ΠΑΡΑΜΕΤΡΟΥΣ ΦΠΑ / TREATWELL / AI ---
with st.sidebar:
    st.subheader("Παράμετροι Υπολογισμών", icon=":material/tune:")

    with st.container(border=True):
        st.markdown("**Φορολογικά & Προμήθειες Treatwell**")
        vat_rate_pct = st.number_input("Συντελεστής ΦΠΑ (%)", min_value=0.0, max_value=30.0, value=24.0, step=1.0)
        tw_new_comm_pct = st.slider("Προμήθεια Νέου Πελάτη TW (%)", 0.0, 50.0, 35.0, 0.5)
        tw_ret_comm_pct = st.slider("Προμήθεια Επαναλαμβανόμενου TW (%)", 0.0, 20.0, 2.0, 0.5)
        tw_widget_comm_pct = st.slider("Προμήθεια Widget / Social (%)", 0.0, 20.0, 0.0, 0.5)
        tw_real_new_share_pct = st.slider(
            "% κρατήσεων 'T...' 1ης εμφάνισης που είναι ΠΡΑΓΜΑΤΙΚΑ νέοι πελάτες",
            min_value=10, max_value=100, value=default_tw_real_new_pct, step=5,
            help="Επειδή έχετε ανεβάσει ολόκληρο το ιστορικό 2,5 ετών (17.938 ραντεβού), το σύστημα γνωρίζει ήδη ποιες πελάτισσες έχουν ξανάρθει! Αφήστε το στο 100%."
        )

    with st.container(border=True):
        st.markdown("**Κανόνες Βαρδιών**")
        use_rolling_schedule = st.checkbox(
            "Κληρονόμηση προηγούμενου προγράμματος (Rolling Schedule)",
            value=True,
            help="Αν μια εβδομάδα δεν έχει δικό της αποθηκευμένο πρόγραμμα, χρησιμοποιεί το πιο πρόσφατο προηγούμενο."
        )
        auto_skip_zero_weeks = st.checkbox(
            "Εξαίρεση εβδομάδων με 0 ραντεβού ανά τεχνίτρια (Άδεια / Πριν την πρόσληψη)",
            value=True,
            help="Αν μια τεχνίτρια έχει 0 ραντεβού σε ολόκληρη την εβδομάδα (π.χ. πριν προσληφθεί ή σε ετήσια άδεια), δεν χρεώνεται μισθοδοσία γι' αυτή την εβδομάδα."
        )

    with st.container(border=True):
        st.markdown("**Πάροχος & Μοντέλο AI CFO**")
        ai_provider_choice = st.selectbox(
            "Πάροχος AI",
            options=["Google Gemini", "OpenAI (ChatGPT)", "Anthropic (Claude)"],
            index=0,
            key="ai_provider_select"
        )

        active_secret_key = (
            secret_gemini_key if ai_provider_choice == "Google Gemini"
            else (secret_openai_key if ai_provider_choice == "OpenAI (ChatGPT)" else secret_anthropic_key)
        )

        if active_secret_key:
            st.caption(f"🔒 **Το API Key ({ai_provider_choice}) είναι κλειδωμένο στα Secrets**.")
            override_gemini_key = st.text_input(
                "Προσωρινό άλλο API Key (προαιρετικό)",
                value="",
                type="password",
                placeholder="Κλειδωμένο στα Secrets",
                help="Το βασικό σας κλειδί διαβάζεται με ασφάλεια από τα Secrets και δεν εμφανίζεται ποτέ στο 'ματάκι' του browser."
            )
            gemini_api_key = override_gemini_key.strip() if override_gemini_key.strip() else active_secret_key
        else:
            gemini_api_key = st.text_input(
                f"API Key ({ai_provider_choice})",
                value="",
                type="password",
                help="Ορίστε το GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY στα Secrets του Streamlit Cloud ή εισάγετέ το εδώ."
            )
            if gemini_api_key and ai_provider_choice == "Google Gemini":
                save_gemini_key_persistent(gemini_api_key)

        if "live_models_by_prov" not in st.session_state:
            st.session_state["live_models_by_prov"] = {}

        if st.button("🔄 Ζωντανή Φόρτωση Μοντέλων από API", width="stretch", key="btn_fetch_live_models"):
            if not gemini_api_key:
                st.warning("Εισάγετε πρώτα API Key για να ανακτηθούν τα μοντέλα.")
            else:
                fetched_list = fetch_live_models_from_api(gemini_api_key, ai_provider_choice)
                if fetched_list:
                    st.session_state["live_models_by_prov"][ai_provider_choice] = fetched_list
                    st.toast(f"Βρέθηκαν {len(fetched_list)} ενεργά μοντέλα στο API!", icon=":material/check_circle:")
                else:
                    st.warning("Δεν βρέθηκαν επιπλέον μοντέλα ή το κλειδί δεν επέστρεψε λίστα.")

        base_models = DEFAULT_MODELS_BY_PROVIDER.get(ai_provider_choice, ["gemini-2.5-pro"])
        live_models = st.session_state["live_models_by_prov"].get(ai_provider_choice, [])
        merged_models = []
        for m_item in (base_models + live_models):
            if m_item not in merged_models:
                merged_models.append(m_item)
        CUSTOM_MODEL_OPT = "✍️ Άλλο / Χειροκίνητο Όνομα Μοντέλου..."
        merged_models.append(CUSTOM_MODEL_OPT)

        chosen_model_opt = st.selectbox(
            "Μοντέλο AI (Προεπιλογή: Κορυφαίο Pro/Reasoning)",
            options=merged_models,
            index=0,
            help="Επιλέξτε από τη λίστα, πατήστε 'Ζωντανή Φόρτωση' για να δείτε όλα τα νέα μοντέλα του API, ή επιλέξτε 'Άλλο' για να γράψετε οποιοδήποτε όνομα μοντέλου!"
        )
        if chosen_model_opt == CUSTOM_MODEL_OPT:
            custom_m_typed = st.text_input(
                "Πληκτρολογήστε το ακριβές ID μοντέλου",
                value=base_models[0],
                placeholder="π.χ. gemini-2.5-pro, o3, gpt-5, claude-sonnet-4-6"
            )
            selected_gemini_model = custom_m_typed.strip() or base_models[0]
        else:
            selected_gemini_model = chosen_model_opt

    if st.button("Ανανέωση Δεδομένων Βάσης", icon=":material/refresh:", width="stretch"):
        refresh_data()

    if app_password_secret:
        if st.button("Κλείδωμα Εφαρμογής (Logout)", icon=":material/lock:", width="stretch"):
            st.session_state["app_authenticated"] = False
            st.rerun()

# --- 7. ΚΑΤΑΛΟΓΟΣ & ΕΠΕΞΕΡΓΑΣΙΑ ΠΟΛΛΑΠΛΩΝ CSV ---
def seed_initial_catalog():
    staff_data = [
        {"staff_name": "Νάντια", "hourly_cost": 7.50, "weekly_hours_declared": 14},
        {"staff_name": "Κατερίνα", "hourly_cost": 7.50, "weekly_hours_declared": 30},
        {"staff_name": "Κατερίνα Π.", "hourly_cost": 7.50, "weekly_hours_declared": 40},
        {"staff_name": "Δόμνα", "hourly_cost": 7.50, "weekly_hours_declared": 39},
        {"staff_name": "Αντιγόνη", "hourly_cost": 7.50, "weekly_hours_declared": 20},
        {"staff_name": "Δημήτρης", "hourly_cost": 0.00, "weekly_hours_declared": 64.5}
    ]
    services_data = [
        {"service_name": "Μανικιούρ χωρίς βαφή", "category": "Μανικιούρ", "base_duration": 20, "gross_price": 9.0, "material_cost_estimate": 0.60, "price_variants_json": "[]"},
        {"service_name": "Λιμάρισμα & Βάψιμο με Χρώμα Διαρκείας", "category": "Μανικιούρ", "base_duration": 20, "gross_price": 7.0, "material_cost_estimate": 0.60, "price_variants_json": "[]"},
        {"service_name": "Μανικιούρ με Χρώμα Διαρκείας", "category": "Μανικιούρ", "base_duration": 30, "gross_price": 12.0, "material_cost_estimate": 1.10, "price_variants_json": "[]"},
        {"service_name": "Μανικιουρ Ημιμόνιμο", "category": "Μανικιούρ", "base_duration": 50, "gross_price": 17.0, "material_cost_estimate": 1.80, "price_variants_json": json.dumps([{"label": "Με γαλλικό σχέδιο", "price": 20.0, "duration_minutes": 70}, {"label": "Combi", "price": 22.0, "duration_minutes": 75}])},
        {"service_name": "Μανικιουρ Ημιμόνιμο Ενισχυμένο", "category": "Μανικιούρ", "base_duration": 60, "gross_price": 20.0, "material_cost_estimate": 2.30, "price_variants_json": json.dumps([{"label": "Με γαλλικό σχέδιο", "price": 23.0, "duration_minutes": 75}, {"label": "Combi", "price": 25.0, "duration_minutes": 80}])},
        {"service_name": "Λιμάρισμα & Βάψιμο Ημιμόνιμο", "category": "Μανικιούρ", "base_duration": 30, "gross_price": 12.0, "material_cost_estimate": 1.30, "price_variants_json": json.dumps([{"label": "Express Ενισχυμένο", "price": 14.0, "duration_minutes": 40}, {"label": "Με γαλλικό σχέδιο", "price": 15.0, "duration_minutes": 45}, {"label": "Express Ενισχυμένο Με γαλλικό σχέδιο", "price": 17.0, "duration_minutes": 55}])},
        {"service_name": "Ενίσχυση φυσικού νυχιού Gel - Acrygel", "category": "Μανικιούρ", "base_duration": 60, "gross_price": 30.0, "material_cost_estimate": 3.80, "price_variants_json": json.dumps([{"label": "Με γαλλικό σχέδιο", "price": 33.0, "duration_minutes": 75}, {"label": "Combi", "price": 35.0, "duration_minutes": 90}])},
        {"service_name": "Συντήρηση Gel", "category": "Μανικιούρ", "base_duration": 75, "gross_price": 28.0, "material_cost_estimate": 3.50, "price_variants_json": json.dumps([{"label": "Με γαλλικό σχέδιο", "price": 31.0, "duration_minutes": 90}, {"label": "XL(3+)", "price": 33.0, "duration_minutes": 110}, {"label": "Combi", "price": 33.0, "duration_minutes": 110}])},
        {"service_name": "Συντήρηση με Acrygel", "category": "Μανικιούρ", "base_duration": 90, "gross_price": 28.0, "material_cost_estimate": 3.60, "price_variants_json": json.dumps([{"label": "Με γαλλικό σχέδιο", "price": 31.0, "duration_minutes": 105}, {"label": "XL(3+)", "price": 33.0, "duration_minutes": 105}, {"label": "Combi", "price": 33.0, "duration_minutes": 105}])},
        {"service_name": "Επιμήκυνση Gel - Acrygel", "category": "Μανικιούρ", "base_duration": 90, "gross_price": 38.0, "material_cost_estimate": 4.80, "price_variants_json": json.dumps([{"label": "Με γαλλικό σχέδιο", "price": 41.0, "duration_minutes": 90}, {"label": "XL(3+)", "price": 43.0, "duration_minutes": 105}, {"label": "Combi", "price": 43.0, "duration_minutes": 105}])},
        {"service_name": "XL(3+)", "category": "Μανικιούρ", "base_duration": 15, "gross_price": 5.0, "material_cost_estimate": 0.50, "price_variants_json": "[]"},
        {"service_name": "Πέρλα - Σκόνη", "category": "Μανικιούρ", "base_duration": 5, "gross_price": 2.0, "material_cost_estimate": 0.30, "price_variants_json": "[]"},
        {"service_name": "Αφαίρεση Ημιμόνιμου", "category": "Μανικιούρ", "base_duration": 10, "gross_price": 3.0, "material_cost_estimate": 0.40, "price_variants_json": "[]"},
        {"service_name": "Αφαίρεση Gel", "category": "Μανικιούρ", "base_duration": 15, "gross_price": 5.0, "material_cost_estimate": 0.60, "price_variants_json": "[]"},
        {"service_name": "Διόρθωση Νυχιού", "category": "Μανικιούρ", "base_duration": 10, "gross_price": 2.0, "material_cost_estimate": 0.50, "price_variants_json": json.dumps([{"label": "Gel/Acrygel", "price": 3.0, "duration_minutes": 15}])},
        {"service_name": "Nail Art", "category": "Μανικιούρ", "base_duration": 15, "gross_price": 3.0, "material_cost_estimate": 0.50, "price_variants_json": "[]"},
        {"service_name": "Μανικιούρ Αντρικό", "category": "Μανικιούρ", "base_duration": 30, "gross_price": 9.0, "material_cost_estimate": 0.60, "price_variants_json": "[]"},
        {"service_name": "Λιμάρισμα & Βάψιμο", "category": "Πεντικιούρ", "base_duration": 20, "gross_price": 10.0, "material_cost_estimate": 0.80, "price_variants_json": "[]"},
        {"service_name": "Πεντικιούρ Express", "category": "Πεντικιούρ", "base_duration": 30, "gross_price": 15.0, "material_cost_estimate": 1.20, "price_variants_json": "[]"},
        {"service_name": "Πεντικιούρ Χωρίς βαφή", "category": "Πεντικιούρ", "base_duration": 30, "gross_price": 14.0, "material_cost_estimate": 1.20, "price_variants_json": json.dumps([{"label": "Spa", "price": 18.0, "duration_minutes": 35}])},
        {"service_name": "Πεντικιούρ με χρώμα διαρκείας", "category": "Πεντικιούρ", "base_duration": 45, "gross_price": 17.0, "material_cost_estimate": 1.60, "price_variants_json": json.dumps([{"label": "Spa", "price": 20.0, "duration_minutes": 50}])},
        {"service_name": "Πεντικιούρ Ημιμόνιμο", "category": "Πεντικιούρ", "base_duration": 45, "gross_price": 20.0, "material_cost_estimate": 2.20, "price_variants_json": json.dumps([{"label": "Spa", "price": 23.0, "duration_minutes": 50}])},
        {"service_name": "Πεντικιούρ Θεραπευτικό", "category": "Πεντικιούρ", "base_duration": 60, "gross_price": 30.0, "material_cost_estimate": 2.80, "price_variants_json": "[]"},
        {"service_name": "Jelly Spa", "category": "Πεντικιούρ", "base_duration": 15, "gross_price": 3.0, "material_cost_estimate": 0.80, "price_variants_json": "[]"},
        {"service_name": "Γαλλικό Σχέδιο", "category": "Πεντικιούρ", "base_duration": 15, "gross_price": 3.0, "material_cost_estimate": 0.30, "price_variants_json": "[]"},
        {"service_name": "Πεντικιούρ Αντρικό", "category": "Πεντικιούρ", "base_duration": 30, "gross_price": 18.0, "material_cost_estimate": 1.30, "price_variants_json": "[]"},
        {"service_name": "Πεντικιουρ Ημιμόνιμο & Brow Lamination", "category": "Πακέτο", "base_duration": 90, "gross_price": 35.0, "material_cost_estimate": 4.50, "price_variants_json": "[]"},
        {"service_name": "Brow Lamination", "category": "Βλεφαρίδες/Φρύδια", "base_duration": 45, "gross_price": 28.0, "material_cost_estimate": 3.20, "price_variants_json": "[]"},
        {"service_name": "Lash Lift", "category": "Βλεφαρίδες/Φρύδια", "base_duration": 45, "gross_price": 25.0, "material_cost_estimate": 3.00, "price_variants_json": "[]"},
        {"service_name": "Lash Lift & Tint", "category": "Βλεφαρίδες/Φρύδια", "base_duration": 60, "gross_price": 30.0, "material_cost_estimate": 3.50, "price_variants_json": "[]"},
        {"service_name": "WEEK_DEFAULT", "category": "__SHIFT_SCHEDULE__", "base_duration": 0, "gross_price": 0.0, "material_cost_estimate": 0.0, "price_variants_json": json.dumps(DEFAULT_SCREENSHOT_SCHEDULE, ensure_ascii=False)}
    ]
    supabase.table("config_staff").upsert(staff_data).execute()
    supabase.table("config_services").upsert(services_data).execute()

def process_and_calculate_bookings(df_raw, df_services, df_staff, df_existing_bookings):
    srv_map = {re.sub(r'\s+', ' ', str(row['service_name'])).strip().lower(): row.to_dict() for _, row in df_services.iterrows()}
    staff_map = {str(row['staff_name']).strip().lower(): float(row.get('hourly_cost', 7.5)) for _, row in df_staff.iterrows()}

    first_visit_map = {}
    if not df_existing_bookings.empty and 'status_group' in df_existing_bookings.columns:
        completed_existing = df_existing_bookings[df_existing_bookings['status_group'] == 'COMPLETED']
        for _, r in completed_existing.iterrows():
            c_name = normalize_greek_name(r['customer_name'])
            a_date = str(r['appointment_date'])
            if c_name not in first_visit_map or a_date < first_visit_map[c_name]:
                first_visit_map[c_name] = a_date

    parsed_rows = []
    for idx, row in df_raw.iterrows():
        raw_oid = row.get("Αριθμός παραγγελίας #") if "Αριθμός παραγγελίας #" in row else row.get("Αριθμός παραγγελίας")
        order_id = "" if pd.isna(raw_oid) else str(raw_oid).strip()
        if order_id.lower() == "nan":
            order_id = ""

        raw_srv = re.sub(r'\s+', ' ', str(row.get("Υπηρεσία", ""))).strip()
        raw_stf = row.get("Υπάλληλος") if "Υπάλληλος" in row and pd.notna(row.get("Υπάλληλος")) else row.get("Εξυπηρετεί", "")
        staff_name = str(raw_stf).strip()
        cust_name = normalize_greek_name(row.get("Πελάτης", "Άγνωστος"))
        raw_stat = row.get("Status") if "Status" in row and pd.notna(row.get("Status")) else row.get("Κατάσταση", "")
        status_raw = str(raw_stat).strip()

        appt_dt = pd.to_datetime(row.get("Ημερομηνία ραντεβού"), format="%d/%m/%Y %H:%M", errors="coerce")
        if pd.isna(appt_dt):
            appt_dt = pd.to_datetime(row.get("Ημερομηνία ραντεβού"), dayfirst=True, errors="coerce") or datetime.now()

        order_dt = pd.to_datetime(row.get("Ημερομηνία παραγγελίας"), format="%d/%m/%Y %H:%M", errors="coerce")
        if pd.isna(order_dt):
            order_dt = appt_dt

        appt_date_str = appt_dt.strftime("%Y-%m-%d")
        s_norm = normalize_greek_name(status_raw)
        if "ΑΚΥΡ" in s_norm or "CANCEL" in s_norm:
            status_group = "CANCELLED"
        elif "ΜΗ ΕΜΦ" in s_norm or "NO-SHOW" in s_norm or "NO SHOW" in s_norm:
            status_group = "NO_SHOW"
        elif "ΕΠΙΒΕΒΑΙΩ" in s_norm or "CONFIRM" in s_norm:
            status_group = "CONFIRMED"
        else:
            status_group = "COMPLETED"

        if status_group == "COMPLETED":
            if cust_name not in first_visit_map or appt_date_str < first_visit_map[cust_name]:
                first_visit_map[cust_name] = appt_date_str

        parsed_rows.append({
            "order_id": order_id, "raw_srv": raw_srv, "staff_name": staff_name,
            "cust_name": cust_name, "status_raw": status_raw, "status_group": status_group,
            "appt_dt": appt_dt, "order_dt": order_dt, "raw_price": row.get("Αξία", ""),
            "src_file": row.get("_src_file", "0")
        })

    parsed_rows.sort(key=lambda x: x["appt_dt"])

    final_records = {}
    item_counters = {}

    for item in parsed_rows:
        order_id = item["order_id"]
        raw_srv = item["raw_srv"]
        cust_name = item["cust_name"]
        staff_name = item["staff_name"]
        appt_dt = item["appt_dt"]
        order_dt = item["order_dt"]
        src_file = item.get("src_file", "0")

        if order_id:
            base_key = f"{order_id}_{slugify(raw_srv)}"
        else:
            base_key = f"DIR_{appt_dt.strftime('%Y%m%d%H%M')}_{slugify(cust_name)}_{slugify(raw_srv)}"

        counter_key = (src_file, base_key)
        item_counters[counter_key] = item_counters.get(counter_key, 0) + 1
        unique_id = f"{base_key}_{item_counters[counter_key]}"

        srv_cfg = srv_map.get(raw_srv.lower(), None)
        p_str = str(item["raw_price"]).replace("€", "").replace(",", ".").strip()
        try:
            csv_price = float(p_str) if p_str and p_str.lower() != "nan" else None
        except Exception:
            csv_price = None

        if srv_cfg:
            clean_cat = srv_cfg.get("category", "Άλλο")
            base_dur = int(srv_cfg.get("base_duration", 45))
            base_p = float(srv_cfg.get("gross_price", 0.0))
            mat_cost = float(srv_cfg.get("material_cost_estimate", 1.0))
            if item["status_group"] == "COMPLETED":
                gross_price = csv_price if (csv_price is not None and csv_price >= 0) else 0.0
            else:
                gross_price = csv_price if (csv_price is not None and csv_price > 0) else base_p
            duration = base_dur
            try:
                variants = json.loads(srv_cfg.get("price_variants_json", "[]"))
                if isinstance(variants, list):
                    for v in variants:
                        if abs(float(v.get("price", 0)) - gross_price) < 0.05:
                            duration = int(v.get("duration_minutes", base_dur))
                            break
            except Exception:
                pass
        else:
            clean_cat = "Άλλο"
            duration = 45
            gross_price = csv_price if csv_price is not None else 0.0
            mat_cost = 1.50

        appt_date_str = appt_dt.strftime("%Y-%m-%d")
        is_first = (first_visit_map.get(cust_name) == appt_date_str)

        if order_id.upper().startswith("T"):
            if is_first:
                channel = "Treatwell (Νέος)"
                comm_pct = tw_new_comm_pct
            else:
                channel = "Treatwell (Επαναλ.)"
                comm_pct = tw_ret_comm_pct
        elif order_id.upper().startswith("W"):
            channel = "Widget (Social)"
            comm_pct = tw_widget_comm_pct
        else:
            channel = "Direct (Τηλέφωνο)"
            comm_pct = 0.0

        if item["status_group"] == "COMPLETED":
            net_rev = round(gross_price / (1.0 + (vat_rate_pct / 100.0)), 2)
            vat_amt = round(gross_price - net_rev, 2)
            comm_fee = round(gross_price * (comm_pct / 100.0), 2)
            hourly_rate = staff_map.get(staff_name.lower(), 7.5)
            labor_c = round((duration / 60.0) * hourly_rate, 2)
            net_prof = round(net_rev - comm_fee - mat_cost - labor_c, 2)
        else:
            net_rev = vat_amt = comm_fee = mat_cost = labor_c = net_prof = 0.0

        final_records[unique_id] = {
            "unique_id": unique_id, "order_id": order_id, "channel": channel,
            "order_date": order_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "appointment_date": appt_date_str, "appointment_time": appt_dt.strftime("%H:%M:%S"),
            "staff_name": staff_name, "customer_name": cust_name, "raw_service": raw_srv,
            "clean_category": clean_cat, "duration_minutes": duration, "gross_price": gross_price,
            "vat_amount": vat_amt, "net_revenue": net_rev, "commission_pct": comm_pct,
            "commission_fee": comm_fee, "material_cost": mat_cost, "labor_cost": labor_c,
            "net_profit": net_prof, "status_raw": item["status_raw"],
            "status_group": item["status_group"], "is_first_visit": is_first
        }

    return list(final_records.values())

# --- 8. ΚΕΦΑΛΙΔΑ & ΠΛΟΗΓΗΣΗ ΠΕΡΙΟΔΩΝ ΜΕ ΒΕΛΑΚΙΑ (◀ / ▶) ---
saved_weeks_count = len([k for k in db_schedules.keys() if re.match(r'^WEEK_\d{4}-\d{2}-\d{2}$', k)])
total_completed_db = len(df_bookings_db[df_bookings_db['status_group'] == 'COMPLETED']) if not df_bookings_db.empty else 0

col_title, col_badge = st.columns([3.1, 1.3], vertical_alignment="center")
with col_title:
    st.title("Nail Salon CFO — Οικονομική Ακτινογραφία", icon=":material/monitoring:")
    st.caption("Ξεκάθαρη απάντηση στο ερώτημα: «Γιατί ενώ έχουμε μεγάλο τζίρο, μένει ελάχιστο καθαρό κέρδος;»")
with col_badge:
    with st.container(border=True):
        st.markdown(f"**Βάση Ενεργή** :green-badge[{len(df_bookings_db):,} Ραντεβού]")
        st.caption(f"{total_completed_db:,} Ολοκληρωμένα • {saved_weeks_count} Προγράμματα")

# Κατασκευή Ταξινομημένων Επιλογών (Φθίνουσα σειρά: πιο πρόσφατα πρώτα!)
month_options_map = {}
week_options_map = {}
year_options_map = {}
all_mondays_set = set()

if not df_bookings_db.empty:
    comp_all_df = df_bookings_db[df_bookings_db['status_group'] == 'COMPLETED'].copy()

    # 1. Εβδομάδες (Φθίνουσα σειρά, με προεπιλογή την πιο πρόσφατη Πλήρη Εβδομάδα >= 25 ραντεβού)
    default_week_idx = 0
    found_full_wk = False
    for idx_w, (m_str, cnt) in enumerate(sorted(wk_counts.items(), reverse=True)):
        m_d = datetime.strptime(m_str, "%Y-%m-%d").date()
        all_mondays_set.add(m_d)
        sun_d = m_d + timedelta(days=6)
        iso_yr, iso_wk, _ = m_d.isocalendar()
        has_saved = f"WEEK_{m_str}" in db_schedules
        status_tag = "✅ Πλήρης" if cnt >= 25 else f"⚠️ Μερική/Προ-κρατήσεις ({cnt})"
        sched_tag = "Ακριβές Ωράριο" if has_saved else "Auto Ωράριο"
        w_lbl = f"Εβδ. {iso_wk}/{iso_yr}: {m_d.strftime('%d/%m')} – {sun_d.strftime('%d/%m/%Y')} ({cnt} ραντ. • {status_tag} • {sched_tag})"
        week_options_map[w_lbl] = (m_d, sun_d, f"Εβδομάδα {m_d.strftime('%d/%m')} – {sun_d.strftime('%d/%m/%Y')}")
        if not found_full_wk and cnt >= 25:
            default_week_idx = idx_w
            found_full_wk = True

    # 2. Μήνες (Φθίνουσα σειρά: 2026-10, 2026-09, ... 2024-07!)
    comp_all_df['ym_tuple'] = list(zip(comp_all_df['appointment_date_dt'].dt.year, comp_all_df['appointment_date_dt'].dt.month))
    ym_groups = {ym: grp for ym, grp in comp_all_df.groupby('ym_tuple')}
    default_month_idx = 0
    found_full_mo = False
    valid_mo_idx = 0
    for (yr, mo) in sorted(ym_groups.keys(), reverse=True):
        grp = ym_groups[(yr, mo)]
        if len(grp) < 10:
            continue
        cal_start = date(int(yr), int(mo), 1)
        cal_end = date(int(yr), 12, 31) if int(mo) == 12 else (date(int(yr), int(mo) + 1, 1) - timedelta(days=1))
        # Αν είναι ο τελευταίος ενεργός μήνας (π.χ. Οκτώβριος 2026), κόβουμε στο τέλος της τελευταίας πλήρους εβδομάδας ώστε να μη χρεώνει κενές μελλοντικές μέρες
        actual_min_d = grp['appointment_date_dt'].dt.date.min()
        actual_max_d = grp['appointment_date_dt'].dt.date.max()
        m_min_d = max(cal_start, actual_min_d) if len(grp) < 150 else cal_start
        m_max_d = min(cal_end, full_cycle_end_d) if (int(yr) == full_cycle_end_d.year and int(mo) == full_cycle_end_d.month) else min(cal_end, actual_max_d)
        if m_max_d < m_min_d:
            m_max_d = actual_max_d
        cnt_in_mo = len(grp[(grp['appointment_date_dt'].dt.date >= m_min_d) & (grp['appointment_date_dt'].dt.date <= m_max_d)])
        status_m = "✅ Πλήρης" if cnt_in_mo >= 100 else f"⚠️ Μερικός ({cnt_in_mo} ραντ.)"
        lbl = f"{MONTHS_GR[int(mo)]} {int(yr)} ({m_min_d.strftime('%d/%m')} – {m_max_d.strftime('%d/%m/%Y')} • {cnt_in_mo} ραντ. • {status_m})"
        month_options_map[lbl] = (m_min_d, m_max_d, f"{MONTHS_GR[int(mo)]} {int(yr)} ({m_min_d.strftime('%d/%m')}–{m_max_d.strftime('%d/%m/%Y')})")
        if not found_full_mo and cnt_in_mo >= 100:
            default_month_idx = valid_mo_idx
            found_full_mo = True
        valid_mo_idx += 1

    # 3. Έτη (Φθίνουσα σειρά: 2026, 2025, 2024 + Όλη η Διετία)
    for yr in sorted(comp_all_df['appointment_date_dt'].dt.year.unique(), reverse=True):
        grp_y = comp_all_df[comp_all_df['appointment_date_dt'].dt.year == yr]
        y_min = max(date(int(yr), 1, 1), full_cycle_start_d)
        y_max = min(date(int(yr), 12, 31), full_cycle_end_d)
        cnt_y = len(grp_y[(grp_y['appointment_date_dt'].dt.date >= y_min) & (grp_y['appointment_date_dt'].dt.date <= y_max)])
        y_lbl = f"Έτος {int(yr)} ({y_min.strftime('%d/%m/%Y')} – {y_max.strftime('%d/%m/%Y')} • {cnt_y:,} ραντεβού)"
        year_options_map[y_lbl] = (y_min, y_max, f"Έτος {int(yr)} ({y_min.strftime('%d/%m/%Y')} – {y_max.strftime('%d/%m/%Y')})")
    all_yr_lbl = f"Ολόκληρη η Περίοδος ({full_cycle_start_d.strftime('%d/%m/%Y')} – {full_cycle_end_d.strftime('%d/%m/%Y')} • {len(comp_all_df):,} ραντεβού)"
    year_options_map[all_yr_lbl] = (full_cycle_start_d, full_cycle_end_d, f"Σύνολο Ιστορικού ({full_cycle_start_d.strftime('%d/%m/%Y')} – {full_cycle_end_d.strftime('%d/%m/%Y')})")

for k in db_schedules.keys():
    if re.match(r'^WEEK_\d{4}-\d{2}-\d{2}$', k):
        try:
            all_mondays_set.add(datetime.strptime(k.replace("WEEK_", ""), "%Y-%m-%d").date())
        except Exception:
            pass
sorted_mondays = sorted(list(all_mondays_set), reverse=True)

# --- ΜΠΑΡΑ ΕΠΙΛΟΓΗΣ ΠΕΡΙΟΔΟΥ ΜΕ ΚΟΥΜΠΙΑ ΒΕΛΩΝ (◀ ΠΑΛΑΙΟΤΕΡΗ | ΕΠΙΛΟΓΗ | ΝΕΟΤΕΡΗ ▶) ---
with st.container(border=True):
    c_p1, c_p2 = st.columns([1.8, 2.4], vertical_alignment="center")
    with c_p1:
        period_category = st.segmented_control(
            "📅 1. Τύπος Χρονικής Περιόδου",
            options=[
                "📆 Ανά Μήνα",
                "🗓️ Ανά Εβδομάδα",
                "📅 Ανά Έτος",
                "✨ Κύκλοι / Custom"
            ],
            default="📆 Ανά Μήνα",
            key="main_period_cat"
        )

    with c_p2:
        if period_category == "📆 Ανά Μήνα" and month_options_map:
            mo_keys = list(month_options_map.keys())
            if "sel_month_idx" not in st.session_state or st.session_state["sel_month_idx"] >= len(mo_keys):
                st.session_state["sel_month_idx"] = default_month_idx

            b_prev, b_sel, b_next = st.columns([0.55, 3.4, 0.55], vertical_alignment="bottom")
            with b_prev:
                # Επειδή η λίστα είναι φθίνουσα (πιο πρόσφατος μήνας στο index 0), το '◀ Παλαιότερος' αυξάνει το index (+1)
                if st.button("◀", help="Προηγούμενος (Παλαιότερος) Μήνας", disabled=(st.session_state["sel_month_idx"] >= len(mo_keys) - 1), width="stretch", key="btn_mo_prev"):
                    st.session_state["sel_month_idx"] = min(len(mo_keys) - 1, st.session_state["sel_month_idx"] + 1)
                    st.rerun()
            with b_next:
                if st.button("▶", help="Επόμενος (Νεότερος) Μήνας", disabled=(st.session_state["sel_month_idx"] <= 0), width="stretch", key="btn_mo_next"):
                    st.session_state["sel_month_idx"] = max(0, st.session_state["sel_month_idx"] - 1)
                    st.rerun()
            with b_sel:
                chosen_mo_lbl = st.selectbox(
                    "2. Επιλέξτε Μήνα (ή πατήστε ◀ / ▶)",
                    options=mo_keys,
                    index=st.session_state["sel_month_idx"],
                    key="mo_dropdown_widget"
                )
                new_m_idx = mo_keys.index(chosen_mo_lbl)
                if new_m_idx != st.session_state["sel_month_idx"]:
                    st.session_state["sel_month_idx"] = new_m_idx
                    st.rerun()
            start_d, end_d, period_label = month_options_map[mo_keys[st.session_state["sel_month_idx"]]]

        elif period_category == "🗓️ Ανά Εβδομάδα" and week_options_map:
            wk_keys = list(week_options_map.keys())
            if "sel_week_idx" not in st.session_state or st.session_state["sel_week_idx"] >= len(wk_keys):
                st.session_state["sel_week_idx"] = default_week_idx

            b_prev, b_sel, b_next = st.columns([0.55, 3.4, 0.55], vertical_alignment="bottom")
            with b_prev:
                if st.button("◀", help="Προηγούμενη (Παλαιότερη) Εβδομάδα", disabled=(st.session_state["sel_week_idx"] >= len(wk_keys) - 1), width="stretch", key="btn_wk_prev"):
                    st.session_state["sel_week_idx"] = min(len(wk_keys) - 1, st.session_state["sel_week_idx"] + 1)
                    st.rerun()
            with b_next:
                if st.button("▶", help="Επόμενη (Νεότερη) Εβδομάδα", disabled=(st.session_state["sel_week_idx"] <= 0), width="stretch", key="btn_wk_next"):
                    st.session_state["sel_week_idx"] = max(0, st.session_state["sel_week_idx"] - 1)
                    st.rerun()
            with b_sel:
                chosen_wk_lbl = st.selectbox(
                    "2. Επιλέξτε Εβδομάδα (ή πατήστε ◀ / ▶)",
                    options=wk_keys,
                    index=st.session_state["sel_week_idx"],
                    key="wk_dropdown_widget"
                )
                new_w_idx = wk_keys.index(chosen_wk_lbl)
                if new_w_idx != st.session_state["sel_week_idx"]:
                    st.session_state["sel_week_idx"] = new_w_idx
                    st.rerun()
            start_d, end_d, period_label = week_options_map[wk_keys[st.session_state["sel_week_idx"]]]

        elif period_category == "📅 Ανά Έτος" and year_options_map:
            yr_keys = list(year_options_map.keys())
            if "sel_year_idx" not in st.session_state or st.session_state["sel_year_idx"] >= len(yr_keys):
                st.session_state["sel_year_idx"] = 0

            b_prev, b_sel, b_next = st.columns([0.55, 3.4, 0.55], vertical_alignment="bottom")
            with b_prev:
                if st.button("◀", help="Προηγούμενο Έτος", disabled=(st.session_state["sel_year_idx"] >= len(yr_keys) - 1), width="stretch", key="btn_yr_prev"):
                    st.session_state["sel_year_idx"] = min(len(yr_keys) - 1, st.session_state["sel_year_idx"] + 1)
                    st.rerun()
            with b_next:
                if st.button("▶", help="Επόμενο Έτος", disabled=(st.session_state["sel_year_idx"] <= 0), width="stretch", key="btn_yr_next"):
                    st.session_state["sel_year_idx"] = max(0, st.session_state["sel_year_idx"] - 1)
                    st.rerun()
            with b_sel:
                chosen_yr_lbl = st.selectbox(
                    "2. Επιλέξτε Έτος (ή πατήστε ◀ / ▶)",
                    options=yr_keys,
                    index=st.session_state["sel_year_idx"],
                    key="yr_dropdown_widget"
                )
                new_y_idx = yr_keys.index(chosen_yr_lbl)
                if new_y_idx != st.session_state["sel_year_idx"]:
                    st.session_state["sel_year_idx"] = new_y_idx
                    st.rerun()
            start_d, end_d, period_label = year_options_map[yr_keys[st.session_state["sel_year_idx"]]]

        else:
            n_full = len(full_week_mondays)
            preset_sub = st.selectbox(
                "2. Επιλογή Κύκλου ή Προσαρμοσμένου Εύρους",
                options=[
                    "Τελευταίες 4 Πλήρεις Εβδομάδες (1 Μήνας)",
                    "Τελευταίες 12 Πλήρεις Εβδομάδες (Τρίμηνο)",
                    "Τελευταίες 26 Πλήρεις Εβδομάδες (Εξάμηνο)",
                    "Τελευταίες 52 Πλήρεις Εβδομάδες (1 Έτος)",
                    f"Όλες οι Πλήρεις Εβδομάδες ({full_cycle_start_d.strftime('%d/%m/%Y')} – {full_cycle_end_d.strftime('%d/%m/%Y')} • {n_full} εβδ.)",
                    "Προσαρμοσμένο Εύρος Ημερομηνιών (Custom)"
                ],
                index=0
            )
            if preset_sub.startswith("Τελευταίες 4") and len(full_week_mondays) >= 4:
                start_d, end_d = full_week_mondays[-4], full_cycle_end_d
                period_label = f"Τελευταίες 4 Πλήρεις Εβδομάδες ({start_d.strftime('%d/%m/%Y')} – {end_d.strftime('%d/%m/%Y')})"
            elif preset_sub.startswith("Τελευταίες 12") and len(full_week_mondays) >= 12:
                start_d, end_d = full_week_mondays[-12], full_cycle_end_d
                period_label = f"Τελευταίο Τρίμηνο ({start_d.strftime('%d/%m/%Y')} – {end_d.strftime('%d/%m/%Y')})"
            elif preset_sub.startswith("Τελευταίες 26") and len(full_week_mondays) >= 26:
                start_d, end_d = full_week_mondays[-26], full_cycle_end_d
                period_label = f"Τελευταίο Εξάμηνο ({start_d.strftime('%d/%m/%Y')} – {end_d.strftime('%d/%m/%Y')})"
            elif preset_sub.startswith("Τελευταίες 52") and len(full_week_mondays) >= 52:
                start_d, end_d = full_week_mondays[-52], full_cycle_end_d
                period_label = f"Τελευταίο Έτος ({start_d.strftime('%d/%m/%Y')} – {end_d.strftime('%d/%m/%Y')})"
            elif preset_sub.startswith("Προσαρμοσμένο"):
                custom_pair = st.date_input("Επιλέξτε Από – Έως", value=(full_cycle_start_d, full_cycle_end_d), min_value=db_min_d, max_value=db_max_d)
                if isinstance(custom_pair, (tuple, list)) and len(custom_pair) == 2:
                    start_d, end_d = custom_pair[0], custom_pair[1]
                else:
                    start_d, end_d = full_cycle_start_d, full_cycle_end_d
                period_label = f"Προσαρμοσμένο ({start_d.strftime('%d/%m/%Y')} – {end_d.strftime('%d/%m/%Y')})"
            else:
                start_d, end_d = full_cycle_start_d, full_cycle_end_d
                period_label = f"Όλες οι Πλήρεις Εβδομάδες ({start_d.strftime('%d/%m/%Y')} – {end_d.strftime('%d/%m/%Y')})"

# --- 9. ΚΕΝΤΡΙΚΗ ΜΗΧΑΝΗ ΥΠΟΛΟΓΙΣΜΟΥ P&L, ΒΑΡΔΙΩΝ & ABC ΚΟΣΤΟΛΟΓΗΣΗΣ ---
if not df_bookings_db.empty:
    active_comp_all = df_bookings_db[df_bookings_db['status_group'] == 'COMPLETED']
    active_staff_weeks_set = set(
        zip(active_comp_all['staff_name'].str.strip().str.lower(), active_comp_all['monday_date_str'])
    )
    week_active_date_bounds = {}
    for m_str, grp in active_comp_all.groupby('monday_date_str'):
        d_min_w = grp['appointment_date_dt'].dt.date.min()
        d_max_w = grp['appointment_date_dt'].dt.date.max()
        week_active_date_bounds[m_str] = (d_min_w, d_max_w, len(grp))

    mask = (df_bookings_db['appointment_date_dt'].dt.date >= start_d) & (df_bookings_db['appointment_date_dt'].dt.date <= end_d)
    df_filtered = df_bookings_db[mask].copy()

    if not df_filtered.empty:
        comp_mask = df_filtered['status_group'] == 'COMPLETED'
        df_filtered.loc[comp_mask, 'net_revenue'] = (df_filtered.loc[comp_mask, 'gross_price'] / (1.0 + vat_rate_pct / 100.0)).round(2)
        df_filtered.loc[comp_mask, 'vat_amount'] = (df_filtered.loc[comp_mask, 'gross_price'] - df_filtered.loc[comp_mask, 'net_revenue']).round(2)

        tw_new_mask = comp_mask & (df_filtered['channel'] == 'Treatwell (Νέος)')
        effective_new_comm = (tw_new_comm_pct * (tw_real_new_share_pct / 100.0)) + (tw_ret_comm_pct * (1.0 - tw_real_new_share_pct / 100.0))
        df_filtered.loc[tw_new_mask, 'commission_fee'] = (df_filtered.loc[tw_new_mask, 'gross_price'] * (effective_new_comm / 100.0)).round(2)

        tw_ret_mask = comp_mask & (df_filtered['channel'] == 'Treatwell (Επαναλ.)')
        df_filtered.loc[tw_ret_mask, 'commission_fee'] = (df_filtered.loc[tw_ret_mask, 'gross_price'] * (tw_ret_comm_pct / 100.0)).round(2)

        wid_mask = comp_mask & (df_filtered['channel'] == 'Widget (Social)')
        df_filtered.loc[wid_mask, 'commission_fee'] = (df_filtered.loc[wid_mask, 'gross_price'] * (tw_widget_comm_pct / 100.0)).round(2)

        df_filtered.loc[comp_mask, 'net_profit'] = (
            df_filtered.loc[comp_mask, 'net_revenue']
            - df_filtered.loc[comp_mask, 'commission_fee']
            - df_filtered.loc[comp_mask, 'material_cost']
            - df_filtered.loc[comp_mask, 'labor_cost']
        ).round(2)

    df_comp = df_filtered[df_filtered['status_group'] == 'COMPLETED'].copy()
    df_noshow = df_filtered[df_filtered['status_group'] == 'NO_SHOW'].copy()
    df_cancelled = df_filtered[df_filtered['status_group'] == 'CANCELLED'].copy()

    gross_rev = float(df_comp['gross_price'].sum()) if not df_comp.empty else 0.0
    vat_total = float(df_comp['vat_amount'].sum()) if not df_comp.empty else 0.0
    net_rev = float(df_comp['net_revenue'].sum()) if not df_comp.empty else 0.0
    comm_total = float(df_comp['commission_fee'].sum()) if not df_comp.empty else 0.0
    mat_total = float(df_comp['material_cost'].sum()) if not df_comp.empty else 0.0
    labor_prod_total = float(df_comp['labor_cost'].sum()) if not df_comp.empty else 0.0

    # --- ΗΜΕΡΑ-ΠΡΟΣ-ΗΜΕΡΑ & ΩΡΑ-ΠΡΟΣ-ΩΡΑ ΜΗΧΑΝΗ ΒΑΡΔΙΩΝ ---
    staff_scheduled_hours_map = {}
    staff_active_weeks_count = {}
    staff_cost_map = {}
    for _, s_row in df_staff_db.iterrows():
        s_key = str(s_row['staff_name']).strip()
        staff_scheduled_hours_map[s_key] = 0.0
        staff_active_weeks_count[s_key] = set()
        staff_cost_map[s_key.lower()] = float(s_row.get('hourly_cost', 7.5))

    period_sched_total_matrix = np.zeros((6, 12))
    period_weekday_counts = np.zeros((6, 1))
    period_staff_per_hour_grid = [[set() for _ in range(12)] for _ in range(6)]
    period_weeks_seen = set()
    period_exact_weeks_seen = set()
    daily_payroll_by_monday = {}
    active_workdays_count = 0

    curr_d = start_d
    while curr_d <= end_d:
        wd = curr_d.weekday()
        if wd <= 5:
            mon_str = (curr_d - timedelta(days=wd)).strftime("%Y-%m-%d")
            if auto_skip_zero_weeks and mon_str not in week_active_date_bounds:
                curr_d += timedelta(days=1)
                continue

            if mon_str in week_active_date_bounds:
                w_min_d, w_max_d, w_cnt = week_active_date_bounds[mon_str]
                if w_cnt < 25 and (curr_d < w_min_d or curr_d > w_max_d):
                    curr_d += timedelta(days=1)
                    continue

            active_workdays_count += 1
            period_weekday_counts[wd, 0] += 1.0
            day_col_name = DAYS_GR[wd]
            period_weeks_seen.add(mon_str)

            wk_rows, is_exact_wk, src_key = resolve_week_schedule_smart(mon_str, db_schedules, use_rolling_schedule)
            if is_exact_wk:
                period_exact_weeks_seen.add(mon_str)
            wk_map = {str(r.get("staff_name", "")).strip().lower(): r for r in wk_rows}

            for _, s_row in df_staff_db.iterrows():
                s_name = str(s_row['staff_name']).strip()
                s_low = s_name.lower()
                h_cost = float(s_row.get('hourly_cost', 7.5))

                if auto_skip_zero_weeks and h_cost > 0:
                    if (s_low, mon_str) not in active_staff_weeks_set:
                        continue

                if s_low in wk_map:
                    shift_str = wk_map[s_low].get(day_col_name, "")
                    day_h = calc_shift_hours(shift_str)
                    iv_list = parse_shift_intervals(shift_str)
                else:
                    day_h = float(s_row.get('weekly_hours_declared', 30)) / 6.0
                    iv_list = []

                staff_scheduled_hours_map[s_name] = staff_scheduled_hours_map.get(s_name, 0.0) + day_h
                if day_h > 0:
                    staff_active_weeks_count[s_name].add(mon_str)
                    daily_payroll_by_monday[mon_str] = daily_payroll_by_monday.get(mon_str, 0.0) + (day_h * h_cost)

                if h_cost > 0:
                    for st_h, en_h in iv_list:
                        for h_idx, h in enumerate(range(9, 21)):
                            ov = max(0.0, min(h + 1.0, en_h) - max(float(h), st_h))
                            if ov > 0:
                                period_sched_total_matrix[wd, h_idx] += ov
                            if ov >= 0.5:
                                period_staff_per_hour_grid[wd][h_idx].add(s_name)
        curr_d += timedelta(days=1)

    staff_perf_list = []
    total_tech_prod_h = 0.0
    total_tech_idle_h = 0.0
    total_tech_decl_h = 0.0

    for _, s_row in df_staff_db.iterrows():
        s_name = str(s_row['staff_name']).strip()
        h_cost = float(s_row.get('hourly_cost', 7.5))
        raw_decl_hours = round(staff_scheduled_hours_map.get(s_name, 0.0), 1)

        s_df = df_comp[df_comp['staff_name'].str.lower() == s_name.lower()] if not df_comp.empty else pd.DataFrame()
        prod_hours = (s_df['duration_minutes'].sum() / 60.0) if not s_df.empty else 0.0
        s_prod_labor = float(s_df['labor_cost'].sum()) if not s_df.empty else 0.0

        # Παραλείπουμε πρώην τεχνίτριες που δεν είχαν ούτε βάρδια ούτε ραντεβού στην επιλεγμένη περίοδο
        if h_cost > 0 and raw_decl_hours <= 0 and len(s_df) == 0:
            continue

        if h_cost > 0:
            decl_hours = max(raw_decl_hours, round(prod_hours, 1))
            idle_hours = max(0.0, decl_hours - prod_hours)
            idle_cost_s = round(idle_hours * h_cost, 2)
            decl_cost = round(s_prod_labor + idle_cost_s, 2)
            util_pct = (prod_hours / decl_hours * 100.0) if decl_hours > 0 else 0.0
            total_tech_prod_h += prod_hours
            total_tech_idle_h += idle_hours
            total_tech_decl_h += decl_hours
        else:
            decl_hours = raw_decl_hours
            idle_hours = 0.0
            idle_cost_s = 0.0
            decl_cost = 0.0
            util_pct = 0.0

        s_gross = float(s_df['gross_price'].sum()) if not s_df.empty else 0.0
        s_net_rev = float(s_df['net_revenue'].sum()) if not s_df.empty else 0.0
        s_comm = float(s_df['commission_fee'].sum()) if not s_df.empty else 0.0
        s_mat = float(s_df['material_cost'].sum()) if not s_df.empty else 0.0
        s_true_net_contrib = s_net_rev - s_comm - s_mat - decl_cost
        short_appts = len(s_df[s_df['duration_minutes'] <= 15]) if not s_df.empty else 0

        staff_perf_list.append({
            "Τεχνίτρια": s_name,
            "Ρόλος": "Τεχνίτρια" if h_cost > 0 else "Διοίκηση/Υποδοχή",
            "Ενεργές Εβδ.": len(staff_active_weeks_count.get(s_name, set())),
            "Ώρες Βάρδιας": round(decl_hours, 1),
            "Παραγωγικές Ώρες": round(prod_hours, 1),
            "Νεκρές Ώρες (Idle)": round(idle_hours, 1),
            "Πληρότητα (%)": round(util_pct, 1),
            "Μισθοδοσία (€)": round(decl_cost, 2),
            "Κόστος Νεκρού Χρόνου (€)": round(idle_cost_s, 2),
            "Μικτός Τζίρος (€)": round(s_gross, 2),
            "Καθαρός Τζίρος (€)": round(s_net_rev, 2),
            "Προμήθειες TW (€)": round(s_comm, 2),
            "Υλικά (€)": round(s_mat, 2),
            "Πραγματικό Ισοζύγιο (€)": round(s_true_net_contrib, 2),
            "Ραντεβού": len(s_df),
            "Μικρά Ραντεβού (≤15')": short_appts
        })

    df_staff_perf = pd.DataFrame(staff_perf_list)
    df_tech_perf = df_staff_perf[df_staff_perf["Ρόλος"] == "Τεχνίτρια"].copy() if not df_staff_perf.empty else pd.DataFrame()

    overall_occ_pct = (total_tech_prod_h / total_tech_decl_h * 100.0) if total_tech_decl_h > 0 else 0.0
    net_rev = round(gross_rev - vat_total, 2)
    idle_labor_cost = round(float(df_tech_perf["Κόστος Νεκρού Χρόνου (€)"].sum()), 2) if not df_tech_perf.empty else 0.0
    total_declared_payroll = round(labor_prod_total + idle_labor_cost, 2)

    monthly_fixed = float(df_expenses_db['amount'].sum()) if not df_expenses_db.empty else 1050.0
    effective_calendar_days = max(1.0, active_workdays_count * (7.0 / 6.0))
    fixed_expenses = round(monthly_fixed * (effective_calendar_days / 30.4375), 2)

    profit_before_idle_and_fixed = round(net_rev - comm_total - mat_total - labor_prod_total, 2)
    total_all_costs = round(comm_total + mat_total + total_declared_payroll + fixed_expenses, 2)
    true_net_profit = round(net_rev - total_all_costs, 2)
    margin_pct = (true_net_profit / gross_rev * 100.0) if gross_rev > 0 else 0.0
    payroll_ratio_pct = (total_declared_payroll / net_rev * 100.0) if net_rev > 0 else 0.0
    completed_count = len(df_comp)
    aov = (gross_rev / completed_count) if completed_count > 0 else 0.0
    gross_per_prod_h = (gross_rev / total_tech_prod_h) if total_tech_prod_h > 0 else 0.0
    net_per_prod_h = (net_rev / total_tech_prod_h) if total_tech_prod_h > 0 else 0.0

    # ΔΙΟΡΘΩΜΕΝΗ ABC ΚΟΣΤΟΛΟΓΗΣΗ ΥΠΗΡΕΣΙΩΝ (ΧΩΡΙΣ ΔΙΠΛΟ-ΜΕΤΡΗΣΗ IDLE TIME ΠΑΝΩ ΣΤΙΣ ΥΠΗΡΕΣΙΕΣ!)
    total_prod_minutes = float(df_comp['duration_minutes'].sum()) if not df_comp.empty else 0.0
    standard_capacity_minutes = max(total_prod_minutes, total_tech_decl_h * 60.0 * 0.72, 1.0)
    fixed_overhead_per_min = fixed_expenses / standard_capacity_minutes

    if not df_comp.empty:
        srv_matrix = df_comp.groupby('raw_service').agg(
            Ραντεβού=('unique_id', 'count'),
            Μικτός_Τζίρος=('gross_price', 'sum'),
            Καθαρός_Τζίρος=('net_revenue', 'sum'),
            Προμήθειες=('commission_fee', 'sum'),
            Υλικά=('material_cost', 'sum'),
            Εργασία=('labor_cost', 'sum'),
            Άμεσο_Κέρδος=('net_profit', 'sum'),
            Λεπτά_Εργασίας=('duration_minutes', 'sum')
        ).reset_index()
        srv_matrix['Μέση_Διάρκεια_Λεπτά'] = (srv_matrix['Λεπτά_Εργασίας'] / srv_matrix['Ραντεβού']).round(0)
        srv_matrix['Καθαρό_Έσοδο_Ώρας'] = (srv_matrix['Καθαρός_Τζίρος'] / (srv_matrix['Λεπτά_Εργασίας'].replace(0, np.nan) / 60.0)).fillna(0).round(2)
        srv_matrix['Αναλογία_Παγίων'] = (srv_matrix['Λεπτά_Εργασίας'] * fixed_overhead_per_min).round(2)
        srv_matrix['Πλήρες_Καθαρό_Κέρδος'] = (srv_matrix['Άμεσο_Κέρδος'] - srv_matrix['Αναλογία_Παγίων']).round(2)
        srv_matrix['Άμεσο_Κέρδος_Ραντεβού'] = (srv_matrix['Άμεσο_Κέρδος'] / srv_matrix['Ραντεβού']).round(2)
        srv_matrix['Πλήρες_Κέρδος_Ραντεβού'] = (srv_matrix['Πλήρες_Καθαρό_Κέρδος'] / srv_matrix['Ραντεβού']).round(2)
        srv_matrix['Κέρδος_ανά_Λεπτό'] = (srv_matrix['Άμεσο_Κέρδος'] / srv_matrix['Λεπτά_Εργασίας'].replace(0, np.nan)).fillna(0).round(3)
        srv_matrix = srv_matrix.sort_values(by='Μικτός_Τζίρος', ascending=False)
    else:
        srv_matrix = pd.DataFrame()

    if completed_count < 25 and (end_d - start_d).days <= 7:
        st.warning(
            f"⚠️ **Προσοχή:** Η επιλεγμένη περίοδος (`{period_label}`) έχει μόνο **{completed_count} ολοκληρωμένα ραντεβού** "
            f"(πιθανόν μελλοντική εβδομάδα προ-κρατήσεων ή μερική εβδομάδα). Πατήστε **`◀`** για να μεταβείτε στην προηγούμενη πλήρη εβδομάδα!",
            icon=":material/warning:"
        )
    else:
        st.caption(
            f"📍 **Ενεργή Περίοδος:** `{period_label}` • **{completed_count:,} ολοκληρωμένα ραντεβού** • "
            f"Έσοδο Παραγ. Ώρας: **{gross_per_prod_h:.2f} €/h** (**{net_per_prod_h:.2f} €/h** προ ΦΠΑ) • "
            f"Βάρδιες Τεχνιτριών: **{total_tech_decl_h:,.1f}h** (Σε ραντεβού: **{total_tech_prod_h:,.1f}h** | Νεκρός Χρόνος: **{total_tech_idle_h:,.1f}h** | Πληρότητα: **{overall_occ_pct:.1f}%**)"
        )

# --- 9Β. ΒΟΗΘΗΤΙΚΗ ΣΥΝΑΡΤΗΣΗ ΥΠΟΛΟΓΙΣΜΟΥ P&L ΟΠΟΙΑΣΔΗΠΟΤΕ ΠΕΡΙΟΔΟΥ (ΓΙΑ ΣΥΓΚΡΙΣΗ ΜΗΝΩΝ / ΕΤΩΝ) ---
def compute_period_pnl_summary(p_start_d: date, p_end_d: date, p_label: str):
    p_mask = (df_bookings_db['appointment_date_dt'].dt.date >= p_start_d) & (df_bookings_db['appointment_date_dt'].dt.date <= p_end_d)
    p_df = df_bookings_db[p_mask].copy()
    p_comp = p_df[p_df['status_group'] == 'COMPLETED'].copy()
    if p_comp.empty:
        return None

    p_comp['net_revenue'] = (p_comp['gross_price'] / (1.0 + vat_rate_pct / 100.0)).round(2)
    p_comp['vat_amount'] = (p_comp['gross_price'] - p_comp['net_revenue']).round(2)
    effective_new_comm = (tw_new_comm_pct * (tw_real_new_share_pct / 100.0)) + (tw_ret_comm_pct * (1.0 - tw_real_new_share_pct / 100.0))
    p_comp.loc[p_comp['channel'] == 'Treatwell (Νέος)', 'commission_fee'] = (p_comp.loc[p_comp['channel'] == 'Treatwell (Νέος)', 'gross_price'] * (effective_new_comm / 100.0)).round(2)
    p_comp.loc[p_comp['channel'] == 'Treatwell (Επαναλ.)', 'commission_fee'] = (p_comp.loc[p_comp['channel'] == 'Treatwell (Επαναλ.)', 'gross_price'] * (tw_ret_comm_pct / 100.0)).round(2)
    p_comp.loc[p_comp['channel'] == 'Widget (Social)', 'commission_fee'] = (p_comp.loc[p_comp['channel'] == 'Widget (Social)', 'gross_price'] * (tw_widget_comm_pct / 100.0)).round(2)

    p_gross = round(float(p_comp['gross_price'].sum()), 2)
    p_vat = round(float(p_comp['vat_amount'].sum()), 2)
    p_net = round(p_gross - p_vat, 2)
    p_comm = round(float(p_comp['commission_fee'].sum()), 2)
    p_mat = round(float(p_comp['material_cost'].sum()), 2)
    p_prod_lab = round(float(p_comp['labor_cost'].sum()), 2)
    p_prod_h = float(p_comp['duration_minutes'].sum()) / 60.0

    p_sched_map = {str(r['staff_name']).strip(): 0.0 for _, r in df_staff_db.iterrows()}
    p_workdays = 0
    c_d = p_start_d
    while c_d <= p_end_d:
        wd = c_d.weekday()
        if wd <= 5:
            m_s = (c_d - timedelta(days=wd)).strftime("%Y-%m-%d")
            if auto_skip_zero_weeks and m_s not in week_active_date_bounds:
                c_d += timedelta(days=1)
                continue
            if m_s in week_active_date_bounds:
                w_min_d, w_max_d, w_cnt = week_active_date_bounds[m_s]
                if w_cnt < 25 and (c_d < w_min_d or c_d > w_max_d):
                    c_d += timedelta(days=1)
                    continue
            p_workdays += 1
            d_col = DAYS_GR[wd]
            wk_r, _, _ = resolve_week_schedule_smart(m_s, db_schedules, use_rolling_schedule)
            wk_m = {str(r.get("staff_name", "")).strip().lower(): r for r in wk_r}
            for _, s_row in df_staff_db.iterrows():
                s_n = str(s_row['staff_name']).strip()
                s_l = s_n.lower()
                h_c = float(s_row.get('hourly_cost', 7.5))
                if auto_skip_zero_weeks and h_c > 0 and (s_l, m_s) not in active_staff_weeks_set:
                    continue
                if s_l in wk_m:
                    d_h = calc_shift_hours(wk_m[s_l].get(d_col, ""))
                else:
                    d_h = float(s_row.get('weekly_hours_declared', 30)) / 6.0
                if d_h > 0:
                    p_sched_map[s_n] = p_sched_map.get(s_n, 0.0) + d_h
        c_d += timedelta(days=1)

    p_decl_h_tot = 0.0
    p_idle_h_tot = 0.0
    p_idle_cost_tot = 0.0
    for _, s_row in df_staff_db.iterrows():
        s_n = str(s_row['staff_name']).strip()
        h_c = float(s_row.get('hourly_cost', 7.5))
        if h_c <= 0:
            continue
        raw_dh = round(p_sched_map.get(s_n, 0.0), 1)
        s_sub = p_comp[p_comp['staff_name'].str.lower() == s_n.lower()]
        pr_h = (s_sub['duration_minutes'].sum() / 60.0) if not s_sub.empty else 0.0
        if raw_dh <= 0 and len(s_sub) == 0:
            continue
        dh = max(raw_dh, round(pr_h, 1))
        ih = max(0.0, dh - pr_h)
        p_decl_h_tot += dh
        p_idle_h_tot += ih
        p_idle_cost_tot += round(ih * h_c, 2)

    p_idle_cost_tot = round(p_idle_cost_tot, 2)
    p_payroll_tot = round(p_prod_lab + p_idle_cost_tot, 2)
    p_occ = (p_prod_h / p_decl_h_tot * 100.0) if p_decl_h_tot > 0 else 0.0
    p_eff_days = max(1.0, p_workdays * (7.0 / 6.0))
    p_fixed = round(monthly_fixed * (p_eff_days / 30.4375), 2)
    p_true_net = round(p_net - p_comm - p_mat - p_payroll_tot - p_fixed, 2)
    p_margin = (p_true_net / p_gross * 100.0) if p_gross > 0 else 0.0
    p_cnt = len(p_comp)
    p_aov = (p_gross / p_cnt) if p_cnt > 0 else 0.0
    p_net_per_h = (p_net / p_prod_h) if p_prod_h > 0 else 0.0
    p_gross_per_h = (p_gross / p_prod_h) if p_prod_h > 0 else 0.0

    return {
        "Περίοδος": p_label,
        "Ραντεβού": p_cnt,
        "Μικτός Τζίρος (€)": p_gross,
        "Καθαρός Τζίρος προ ΦΠΑ (€)": p_net,
        "ΦΠΑ (€)": p_vat,
        "Μέση Απόδειξη AOV (€)": round(p_aov, 2),
        "Παραγωγικές Ώρες (h)": round(p_prod_h, 1),
        "Ώρες Βάρδιας (h)": round(p_decl_h_tot, 1),
        "Νεκρές Ώρες (h)": round(p_idle_h_tot, 1),
        "Πληρότητα (%)": round(p_occ, 1),
        "Μικτό Έσοδο / Παραγ. Ώρα (€/h)": round(p_gross_per_h, 2),
        "Καθαρό Έσοδο / Παραγ. Ώρα (€/h)": round(p_net_per_h, 2),
        "Προμήθειες TW (€)": p_comm,
        "Αναλώσιμα Υλικά (€)": p_mat,
        "Εργασία σε Ραντεβού (€)": p_prod_lab,
        "Κόστος Νεκρού Χρόνου (€)": p_idle_cost_tot,
        "Συνολική Μισθοδοσία (€)": p_payroll_tot,
        "Πάγια Έξοδα (€)": p_fixed,
        "Καθαρό Κέρδος (€)": p_true_net,
        "Περιθώριο Κέρδους (%)": round(p_margin, 1)
    }

# --- 10. ΔΥΝΑΜΙΚΗ ΜΗΧΑΝΗ ΑΞΙΟΛΟΓΗΣΗΣ 6 ΟΙΚΟΝΟΜΙΚΩΝ ΔΕΙΚΤΩΝ (HYBRID ENGINE PART A) ---
def build_dynamic_cfo_diagnostics():
    """
    Αξιολογεί δυναμικά τους 6 βασικούς οικονομικούς δείκτες για την επιλεγμένη περίοδο,
    με #1 δομικό δείκτη το Καθαρό Έσοδο ανά Παραγωγική Ώρα (Υποκοστολόγηση Χρόνου σε Ημιμόνιμο & Gel).
    """
    diagnostics = []
    if gross_rev <= 0:
        return diagnostics

    p_idle_pct = (idle_labor_cost / gross_rev) * 100.0
    p_comm_pct = (comm_total / gross_rev) * 100.0
    p_fixed_pct = (fixed_expenses / gross_rev) * 100.0

    # 1. ΧΑΜΗΛΟ ΚΑΘΑΡΟ ΕΣΟΔΟ ΑΝΑ ΠΑΡΑΓΩΓΙΚΗ ΩΡΑ (ΥΠΟΚΟΣΤΟΛΟΓΗΣΗ ΧΡΟΝΟΥ ΣΕ ΗΜΙΜΟΝΙΜΟ & GEL)
    target_net_per_h = 21.00
    free_appts_cnt = len(df_comp[df_comp['gross_price'] <= 0.01]) if not df_comp.empty else 0
    low_hourly_srvs_str = ""
    if not srv_matrix.empty:
        vol_thresh = max(3, int(completed_count * 0.04))
        core_low_df = srv_matrix[(srv_matrix['Ραντεβού'] >= vol_thresh) & (srv_matrix['Καθαρό_Έσοδο_Ώρας'] < 18.5)].sort_values('Ραντεβού', ascending=False)
        if not core_low_df.empty:
            low_hourly_srvs_str = ", ".join([
                f"<b>{r['raw_service']}</b> ({int(r['Ραντεβού'])} ραντ., {int(r['Μέση_Διάρκεια_Λεπτά'])}' ➔ <b>{r['Καθαρό_Έσοδο_Ώρας']:.2f}€/h</b> προ ΦΠΑ)"
                for _, r in core_low_df.head(3).iterrows()
            ])

    if total_tech_prod_h > 0 and net_per_prod_h < 20.50:
        hourly_gap_eur = round((target_net_per_h - net_per_prod_h) * total_tech_prod_h, 2)
        mat_per_h = mat_total / total_tech_prod_h
        comm_per_h = comm_total / total_tech_prod_h
        fix_per_h = fixed_expenses / total_tech_prod_h
        rem_per_h = net_per_prod_h - 7.50 - mat_per_h - comm_per_h - fix_per_h
        free_note = f"<br>• Εκτελέστηκαν επίσης <b>{free_appts_cnt} δωρεάν ραντεβού (0€)</b> που κατανάλωσαν χρόνο & υλικά." if free_appts_cnt >= 5 else ""
        diagnostics.append({
            "severity_rank": 2 if net_per_prod_h < 18.50 else 1,
            "impact_eur": hourly_gap_eur,
            "css_class": "cfo-card-red" if net_per_prod_h < 18.50 else "cfo-card-amber",
            "badge": "🔴 #1 Δομική Πληγή Κερδοφορίας (Τιμή vs Χρόνος)" if net_per_prod_h < 18.50 else "🟠 Χαμηλή Απόδοση Ώρας",
            "title": f"Χαμηλό Έσοδο ανά Παραγωγική Ώρα ({net_per_prod_h:.2f} €/h προ ΦΠΑ | -{hourly_gap_eur:,.0f} €)",
            "body": (
                f"Όταν η καρέκλα δουλεύει 100% ασταμάτητα, <b>1 ώρα ραντεβού</b> φέρνει <b>{gross_per_prod_h:.2f} €/h με ΦΠΑ</b> "
                f"(μόλις <b>{net_per_prod_h:.2f} €/h χωρίς ΦΠΑ</b> έναντι υγιούς στόχου <b>21,00 €/h</b>). "
                f"Αφαιρώντας τεχνίτρια (<b>-7,50€/h</b>), υλικά (<b>-{mat_per_h:.2f}€/h</b>), TW (<b>-{comm_per_h:.2f}€/h</b>) & πάγια (<b>-{fix_per_h:.2f}€/h</b>), "
                f"μένουν μόλις <b>{rem_per_h:+.2f} €/ώρα</b>!<br>"
                f"• Υπηρεσίες όγκου που ρίχνουν το ωριαίο έσοδο: {low_hourly_srvs_str or 'Βασικές υπηρεσίες μεγάλου χρόνου'}."
                f"{free_note}"
            ),
            "action": "Αύξηση +2€ έως +3€ σε Ημιμόνιμο Ενισχυμένο / Συντήρηση Gel (ή μείωση διάρκειας κατά 10'-15') — αποδίδει 4x περισσότερο κέρδος από τη μείωση νεκρών ωρών!"
        })
    else:
        diagnostics.append({
            "severity_rank": 0,
            "impact_eur": 0.0,
            "css_class": "cfo-card-green",
            "badge": "🟢 Υγιής Δείκτης",
            "title": f"Υψηλό Καθαρό Έσοδο ανά Παραγωγική Ώρα ({net_per_prod_h:.2f} €/h προ ΦΠΑ)",
            "body": (
                f"Κάθε παραγωγική ώρα ραντεβού αποδίδει <b>{gross_per_prod_h:.2f} €/h με ΦΠΑ</b> (<b>{net_per_prod_h:.2f} €/h προ ΦΠΑ</b>), "
                f"καλύπτοντας άνετα το ωρομίσθιο τεχνίτριας (7,50€/h), τα υλικά και τα πάγια."
            ),
            "action": "Διατηρήστε τη σχέση τιμής/χρόνου στις βασικές υπηρεσίες σας."
        })

    # 2. ΝΕΚΡΟΣ ΧΡΟΝΟΣ ΒΑΡΔΙΩΝ (IDLE TIME) — ΡΕΑΛΙΣΤΙΚΗ ΣΤΑΘΜΙΣΗ (65%-70% ΠΛΗΡΟΤΗΤΑ ΕΙΝΑΙ ΦΥΣΙΟΛΟΓΙΚΟ ΤΑΒΑΝΙ ΚΛΑΔΟΥ)
    top_idle_staff = df_tech_perf.sort_values("Κόστος Νεκρού Χρόνου (€)", ascending=False) if not df_tech_perf.empty else pd.DataFrame()
    top_idle_names = ", ".join([
        f"{r['Τεχνίτρια']} ({r['Νεκρές Ώρες (Idle)']:.0f}h / -{r['Κόστος Νεκρού Χρόνου (€)']:,.0f}€)"
        for _, r in top_idle_staff.head(2).iterrows() if r['Νεκρές Ώρες (Idle)'] > 2
    ]) if not top_idle_staff.empty else ""
    recoverable_idle_eur = round(max(0.0, (total_tech_decl_h * 0.70 - total_tech_prod_h) * 7.50), 2)

    if overall_occ_pct < 56.0:
        diagnostics.append({
            "severity_rank": 2,
            "impact_eur": round(max(recoverable_idle_eur, idle_labor_cost * 0.5), 2),
            "css_class": "cfo-card-red",
            "badge": "🔴 Χαμηλή Πληρότητα Βαρδιών (<56%)",
            "title": f"Υπερβάλλων Νεκρός Χρόνος Βαρδιών (Πληρότητα {overall_occ_pct:.1f}% | -{idle_labor_cost:,.0f} €)",
            "body": (
                f"Από τις <b>{total_tech_decl_h:,.1f} ώρες βάρδιας</b>, οι τεχνίτριες είχαν ραντεβού τις <b>{total_tech_prod_h:,.1f} ώρες</b> "
                f"(Πληρότητα <b>{overall_occ_pct:.1f}%</b> έναντι φυσιολογικού 65%-70%).<br>"
                f"• Μεγαλύτερος νεκρός χρόνος: <b>{top_idle_names or '-'}</b>."
            ),
            "action": "Συμπτύξτε τις πρωινές/μεσημεριανές βάρδιες χαμηλής κίνησης στην Καρτέλα 3."
        })
    elif overall_occ_pct < 64.0:
        diagnostics.append({
            "severity_rank": 1,
            "impact_eur": round(max(recoverable_idle_eur, idle_labor_cost * 0.30), 2),
            "css_class": "cfo-card-amber",
            "badge": "🟠 Λειτουργικός Χρόνος Αναμονής",
            "title": f"Μέτρια Πληρότητα Βαρδιών ({overall_occ_pct:.1f}% | Ανακτήσιμο όφελος ~{max(recoverable_idle_eur, idle_labor_cost * 0.30):,.0f} €)",
            "body": (
                f"Η πληρότητα (<b>{overall_occ_pct:.1f}%</b>) πλησιάζει το φυσιολογικό όριο κλάδου (65%–70%). "
                f"Από το συνολικό κόστος κενών ωρών (<b>-{idle_labor_cost:,.0f} €</b>), το ~70% είναι αναγκαίος χρόνος διαθεσιμότητας μεταξύ ραντεβού.<br>"
                f"• Περιθώριο βελτιστοποίησης σε: <b>{top_idle_names or '-'}</b>."
            ),
            "action": "Μικρή προσαρμογή 2-3 ωρών/εβδ. στις νεκρές ζώνες χωρίς να μειωθεί η διαθεσιμότητα στις ώρες αιχμής."
        })
    else:
        diagnostics.append({
            "severity_rank": 0,
            "impact_eur": 0.0,
            "css_class": "cfo-card-green",
            "badge": "🟢 Υγιής Πληρότητα Κλάδου (≥64%)",
            "title": f"Φυσιολογική Πληρότητα Βαρδιών ({overall_occ_pct:.1f}%)",
            "body": (
                f"Με <b>{overall_occ_pct:.1f}% πληρότητα</b> ({total_tech_prod_h:,.1f}h σε ραντεβού / {total_tech_decl_h:,.1f}h βάρδιας), "
                f"το κατάστημα δουλεύει ήδη κοντά στο πρακτικό ταβάνι (65%–72%) ενός φυσικού nail salon. "
                f"Οι κενές ώρες δεν είναι η κύρια αιτία χαμηλού κέρδους."
            ),
            "action": "Εστιάστε στην αύξηση του εσόδου ανά ώρα εργασίας (+2€ έως +3€ στις βασικές υπηρεσίες) αντί για περικοπή βαρδιών."
        })

    # 3. ΠΡΟΜΗΘΕΙΕΣ TREATWELL MARKETPLACE
    tw_new_df = df_comp[df_comp['channel'] == 'Treatwell (Νέος)']
    tw_ret_df = df_comp[df_comp['channel'] == 'Treatwell (Επαναλ.)']
    if p_comm_pct >= 4.0 or comm_total >= 150.0:
        sev = 2 if p_comm_pct >= 8.0 else 1
        diagnostics.append({
            "severity_rank": sev,
            "impact_eur": round(comm_total, 2),
            "css_class": "cfo-card-red" if sev == 2 else "cfo-card-amber",
            "badge": "🔴 Πληγή Κερδοφορίας" if sev == 2 else "🟠 Σημείο Προσοχής",
            "title": f"Προμήθειες Treatwell (-{comm_total:,.2f} € / {p_comm_pct:.1f}%)",
            "body": (
                f"Το Treatwell απορρόφησε <b>-{comm_total:,.2f} €</b> (<b>{p_comm_pct:.1f}%</b> του μικτού τζίρου) από "
                f"<b>{len(tw_new_df)} ραντεβού νέων πελατών</b> (35% προμήθεια) και <b>{len(tw_ret_df)} επαναλαμβανόμενων</b>.<br>"
                f"Κάθε νέο ραντεβού μέσω Marketplace στερεί ~7,00 € καθαρού κέρδους."
            ),
            "action": "Μετατρέψτε τις νέες πελάτισσες του Treatwell σε Direct (κλείσιμο επόμενου ραντεβού στο ταμείο με 0% προμήθεια)."
        })
    else:
        diagnostics.append({
            "severity_rank": 0,
            "impact_eur": 0.0,
            "css_class": "cfo-card-green",
            "badge": "🟢 Υγιής Δείκτης",
            "title": f"Χαμηλές Προμήθειες Treatwell (-{comm_total:,.2f} € / {p_comm_pct:.1f}%)",
            "body": (
                f"Οι προμήθειες Treatwell στην περίοδο αυτή ήταν μόλις <b>{p_comm_pct:.1f}% του τζίρου</b> (<b>-{comm_total:,.2f} €</b>). "
                f"Η πλειοψηφία των ραντεβού σας ήρθε από Direct/Τηλέφωνο, Widget (0%) ή επαναλαμβανόμενες πελάτισσες (2%)."
            ),
            "action": "Συνεχίστε την προώθηση κρατήσεων μέσω Direct και Social Widget (0% προμήθεια)."
        })

    # 4. ΔΩΡΕΑΝ & ΜΙΚΡΟ-ΥΠΗΡΕΣΙΕΣ (≤15')
    if not srv_matrix.empty:
        truly_unprofitable_srvs = srv_matrix[srv_matrix['Πλήρες_Κέρδος_Ραντεβού'] < 0.50].sort_values('Πλήρες_Καθαρό_Κέρδος', ascending=True)
        short_cnt = len(df_comp[df_comp['duration_minutes'] <= 15])
        short_rev = float(df_comp[df_comp['duration_minutes'] <= 15]['gross_price'].sum())
        low_margin_impact = abs(float(truly_unprofitable_srvs[truly_unprofitable_srvs['Πλήρες_Καθαρό_Κέρδος'] < 0]['Πλήρες_Καθαρό_Κέρδος'].sum())) + (free_appts_cnt * 4.0)

        if not truly_unprofitable_srvs.empty and (low_margin_impact >= 40.0 or short_cnt >= 15):
            bad_names = ", ".join([
                f"{r['raw_service']} ({int(r['Ραντεβού'])} ραντ., {r['Πλήρες_Κέρδος_Ραντεβού']:+.2f}€/ραντ.)"
                for _, r in truly_unprofitable_srvs.head(3).iterrows()
            ])
            diagnostics.append({
                "severity_rank": 2 if low_margin_impact >= 150.0 else 1,
                "impact_eur": round(low_margin_impact, 2),
                "css_class": "cfo-card-amber",
                "badge": "🟠 Δωρεάν & Μικρές Υπηρεσίες (≤15')",
                "title": f"{free_appts_cnt} Δωρεάν (0€) & {short_cnt} Μικρά Ραντεβού (~{low_margin_impact:,.0f} € κόστος)",
                "body": (
                    f"Εκτελέστηκαν <b>{free_appts_cnt} δωρεάν ραντεβού (0€)</b> και συνολικά <b>{short_cnt} μικρο-ραντεβού ≤15'</b> "
                    f"(Αφαιρέσεις/Διορθώσεις/Πέρλα, τζίρος μόλις {short_rev:,.0f} €) που καταναλώνουν χρόνο τεχνίτριας και υλικά.<br>"
                    f"• Υπηρεσίες με αρνητικό πλήρες αποτέλεσμα: <b>{bad_names}</b>."
                ),
                "action": "Περιορίστε τις δωρεάν διορθώσεις/αφαιρέσεις (0€) και ενσωματώστε τες ως πληρωμένο add-on."
            })

    # 5. ΤΕΧΝΙΤΡΙΕΣ ΜΕ ΑΡΝΗΤΙΚΟ ΟΙΚΟΝΟΜΙΚΟ ΙΣΟΖΥΓΙΟ
    neg_staff_df = df_tech_perf[df_tech_perf["Πραγματικό Ισοζύγιο (€)"] < -15.0].sort_values("Πραγματικό Ισοζύγιο (€)", ascending=True) if not df_tech_perf.empty else pd.DataFrame()
    if not neg_staff_df.empty:
        neg_sum_eur = abs(float(neg_staff_df["Πραγματικό Ισοζύγιο (€)"].sum()))
        neg_list_str = ", ".join([
            f"{r['Τεχνίτρια']} ({r['Πραγματικό Ισοζύγιο (€)']:+,.0f} €, πληρότητα {r['Πληρότητα (%)']:.0f}%)"
            for _, r in neg_staff_df.iterrows()
        ])
        diagnostics.append({
            "severity_rank": 2 if neg_sum_eur >= 250.0 else 1,
            "impact_eur": round(neg_sum_eur, 2),
            "css_class": "cfo-card-red" if neg_sum_eur >= 250.0 else "cfo-card-amber",
            "badge": "🔴 Έλλειμμα Απόδοσης Προσωπικού" if neg_sum_eur >= 250.0 else "🟠 Ανισορροπία Βαρδιών",
            "title": f"Τεχνίτριες με Αρνητικό Ισοζύγιο (-{neg_sum_eur:,.2f} €)",
            "body": (
                f"Για {len(neg_staff_df)} τεχνίτρια/ες, το κόστος μισθοδοσίας της βάρδιάς τους ξεπέρασε τον καθαρό τζίρο (μετά από ΦΠΑ, TW & υλικά) που έφεραν:<br>"
                f"• <b>{neg_list_str}</b>."
            ),
            "action": "Κατευθύνετε περισσότερα ραντεβού στις συγκεκριμένες τεχνίτριες ή προσαρμόστε τις ώρες βάρδιάς τους."
        })

    # 6. ΑΚΥΡΩΣΕΙΣ & NO-SHOWS
    total_attempted = completed_count + len(df_cancelled) + len(df_noshow)
    cancel_rate_pct = ((len(df_cancelled) + len(df_noshow)) / total_attempted * 100.0) if total_attempted > 0 else 0.0
    lost_gross_cancels = float(df_cancelled['gross_price'].sum() + df_noshow['gross_price'].sum()) if (not df_cancelled.empty or not df_noshow.empty) else 0.0
    if lost_gross_cancels <= 0 and (len(df_cancelled) + len(df_noshow)) > 0:
        lost_gross_cancels = (len(df_cancelled) + len(df_noshow)) * aov

    if cancel_rate_pct >= 12.0 and (len(df_cancelled) + len(df_noshow)) >= 8:
        recoverable_cancel_eur = round(lost_gross_cancels * 0.35, 2)
        diagnostics.append({
            "severity_rank": 1,
            "impact_eur": recoverable_cancel_eur,
            "css_class": "cfo-card-amber",
            "badge": "🟠 Διαρροή από Ακυρώσεις / No-Shows",
            "title": f"{len(df_cancelled)} Ακυρώσεις & {len(df_noshow)} No-Shows ({cancel_rate_pct:.1f}%)",
            "body": (
                f"Καταγράφηκαν <b>{len(df_cancelled)} ακυρώσεις</b> και <b>{len(df_noshow)} μη εμφανίσεις (No-Show)</b> "
                f"(συνολικής αξίας ραντεβού ~<b>{lost_gross_cancels:,.0f} €</b>), αφήνοντας κενά της τελευταίας στιγμής στις βάρδιες."
            ),
            "action": "Εφαρμόστε επιβεβαίωση 24 ώρες πριν και προπληρωμή/προκαταβολή σε πελάτισσες με ιστορικό No-Show."
        })

    # 7. ΒΑΡΟΣ ΠΑΓΙΩΝ ΕΞΟΔΩΝ
    if p_fixed_pct >= 18.0:
        excess_fixed = max(50.0, fixed_expenses - (gross_rev * 0.14))
        diagnostics.append({
            "severity_rank": 2 if p_fixed_pct >= 25.0 else 1,
            "impact_eur": round(excess_fixed, 2),
            "css_class": "cfo-card-blue",
            "badge": "🔵 Υψηλό Βάρος Παγίων",
            "title": f"Πάγια Έξοδα Περιόδου (-{fixed_expenses:,.2f} € / {p_fixed_pct:.1f}% του τζίρου)",
            "body": (
                f"Τα πάγια έξοδα (Ενοίκιο, ΔΕΚΟ, Λογιστής: <b>{monthly_fixed:,.0f} €/μήνα</b>) απορροφούν το <b>{p_fixed_pct:.1f}%</b> του μικτού τζίρου αυτής της περιόδου "
                f"(υγιές όριο κλάδου: ≤14%)."
            ),
            "action": "Απαιτείται αύξηση του μέσου εισιτηρίου (AOV) ή περισσότερα ραντεβού ανά ημέρα για να «σπάσουν» τα πάγια."
        })

    diagnostics.sort(key=lambda x: (1 if x["severity_rank"] > 0 else 0, x["impact_eur"]), reverse=True)
    return diagnostics

# --- 11. ΠΛΟΗΓΗΣΗ 6 ΕΣΤΙΑΣΜΕΝΩΝ ΚΑΡΤΕΛΩΝ (ΜΕ ΝΕΑ ΚΑΡΤΕΛΑ ΣΥΓΚΡΙΣΗΣ ΠΕΡΙΟΔΩΝ) ---
tab_cfo, tab_compare, tab_staff, tab_services, tab_sim, tab_admin = st.tabs([
    ":material/monitoring: 1. Γιατί Δεν Μένει Κέρδος; (Σύνοψη CFO)",
    ":material/compare_arrows: 2. Σύγκριση Περιόδων (Μήνες / Έτη)",
    ":material/groups: 3. Προσωπικό, Idle Time & Βάρδιες",
    ":material/spa: 4. Υπηρεσίες, Treatwell & Λιανική",
    ":material/tune: 5. Προσομοιωτής Κερδοφορίας & AI CFO",
    ":material/settings: 6. Δεδομένα, Βάρδιες & Ρυθμίσεις"
])

# =====================================================================
# TAB 1: ΓΙΑΤΙ ΔΕΝ ΜΕΝΕΙ ΚΕΡΔΟΣ; (ΣΥΝΟΨΗ CFO & ΥΒΡΙΔΙΚΗ ΔΙΑΓΝΩΣΗ)
# =====================================================================
with tab_cfo:
    if df_bookings_db.empty or df_comp.empty:
        st.info("Δεν υπάρχουν ολοκληρωμένα ραντεβού για την επιλεγμένη περίοδο. Επιλέξτε άλλη περίοδο με τα βελάκια ◀ / ▶.")
    else:
        # 1. Ο ΚΑΝΟΝΑΣ ΤΩΝ 100€ (ΠΟΥ ΠΑΝΕ ΤΑ ΛΕΦΤΑ;)
        with st.container(border=True):
            st.subheader("💶 Ο Κανόνας των 100 € — Πού πηγαίνουν τα χρήματα του ταμείου;", icon=":material/payments:")
            st.caption(f"Για κάθε **100,00 €** που πληρώνουν οι πελάτισσες στο ταμείο (`{period_label}`), δείτε πώς κατανέμεται ο τζίρος:")

            if gross_rev > 0:
                p_vat = (vat_total / gross_rev) * 100.0
                p_comm = (comm_total / gross_rev) * 100.0
                p_mat = (mat_total / gross_rev) * 100.0
                p_prod_lab = (labor_prod_total / gross_rev) * 100.0
                p_idle_lab = (idle_labor_cost / gross_rev) * 100.0
                p_fixed = (fixed_expenses / gross_rev) * 100.0
                p_net = (true_net_profit / gross_rev) * 100.0
            else:
                p_vat = p_comm = p_mat = p_prod_lab = p_idle_lab = p_fixed = p_net = 0.0

            fig_100 = go.Figure()
            segments_100 = [
                ("ΦΠΑ (24%)", p_vat, vat_total, "#64748b"),
                ("Προμήθειες Treatwell", p_comm, comm_total, "#f59e0b"),
                ("Αναλώσιμα Υλικά", p_mat, mat_total, "#8b5cf6"),
                ("Εργασία σε Ραντεβού", p_prod_lab, labor_prod_total, "#3b82f6"),
                ("Νεκρός Χρόνος Βαρδιών (Idle)", p_idle_lab, idle_labor_cost, "#ef4444"),
                ("Πάγια (Ενοίκιο/ΔΕΚΟ/Λογ.)", p_fixed, fixed_expenses, "#ec4899"),
            ]
            for seg_name, seg_pct, seg_eur, seg_col in segments_100:
                fig_100.add_trace(go.Bar(
                    y=["Κατανομή 100€"],
                    x=[round(seg_pct, 1)],
                    name=f"{seg_name}: {seg_pct:.1f}€ ({seg_eur:,.0f}€)",
                    orientation="h",
                    marker_color=seg_col,
                    text=[f"{seg_pct:.1f}€" if seg_pct >= 5 else ""],
                    textposition="inside",
                    hovertemplate=f"<b>{seg_name}</b><br>{seg_pct:.1f} € ανά 100 € τζίρου<br>Σύνολο: {seg_eur:,.2f} €<extra></extra>"
                ))
            if p_net > 0:
                fig_100.add_trace(go.Bar(
                    y=["Κατανομή 100€"],
                    x=[round(p_net, 1)],
                    name=f"Καθαρό Κέρδος: +{p_net:.1f}€ (+{true_net_profit:,.0f}€)",
                    orientation="h",
                    marker_color="#10b981",
                    text=[f"+{p_net:.1f}€" if p_net >= 4 else ""],
                    textposition="inside"
                ))
            fig_100.update_layout(
                barmode="stack",
                height=120,
                margin=dict(t=5, b=5, l=5, r=5),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(title="Ευρώ ανά 100 € Μικτού Τζίρου", ticksuffix=" €"),
                yaxis=dict(showticklabels=False),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
            )
            st.plotly_chart(fig_100, width="stretch")

            k1, k2, k3, k4 = st.columns(4)
            k1.metric(
                "1. Μικτός Τζίρος Ταμείου",
                f"{gross_rev:,.2f} €",
                f"Καθαρός προ ΦΠΑ: {net_rev:,.2f} € ({completed_count:,} ραντ.)",
                delta_color="off",
                border=True
            )
            k2.metric(
                "2. Προμήθειες TW & Υλικά",
                f"-{(comm_total + mat_total):,.2f} €",
                f"TW: -{comm_total:,.0f} € ({p_comm:.1f}%) | Υλικά: -{mat_total:,.0f} €",
                delta_color="inverse",
                border=True
            )
            k3.metric(
                "3. Μισθοδοσία & Πάγια",
                f"-{(total_declared_payroll + fixed_expenses):,.2f} €",
                f"Μισθοί: -{total_declared_payroll:,.0f} € (Idle: -{idle_labor_cost:,.0f} €) | Πάγια: -{fixed_expenses:,.0f} €",
                delta_color="inverse",
                border=True
            )
            k4.metric(
                "4. Τελικό Καθαρό Αποτέλεσμα",
                f"{true_net_profit:+,.2f} €",
                f"{p_net:+.1f} € ανά 100 € τζίρου (Πληρότητα {overall_occ_pct:.1f}%)",
                delta_color="normal" if true_net_profit >= 0 else "inverse",
                border=True
            )

        st.space("small")

        # 2. ΥΒΡΙΔΙΚΗ ΜΗΧΑΝΗ ΔΙΑΓΝΩΣΗΣ (ΜΕΡΟΣ Α: ΔΥΝΑΜΙΚΕΣ ΚΑΡΤΕΣ ΙΕΡΑΡΧΗΜΕΝΕΣ ΣΕ ΕΥΡΩ + ΥΓΙΕΙΣ ΔΕΙΚΤΕΣ)
        all_diagnostics = build_dynamic_cfo_diagnostics()
        wounds_cnt = sum(1 for d in all_diagnostics if d["severity_rank"] > 0)

        col_diag_h1, col_diag_h2 = st.columns([2.7, 1.3], vertical_alignment="center")
        with col_diag_h1:
            if wounds_cnt > 0:
                st.subheader(f"🔍 Δυναμική Διάγνωση Περιόδου ({wounds_cnt} Πληγές Κέρδους — Ιεραρχημένες σε Ευρώ)", icon=":material/troubleshoot:")
            else:
                st.subheader("🟢 Δυναμική Διάγνωση Περιόδου — Όλοι οι Δείκτες Υγιείς!", icon=":material/verified:")
        with col_diag_h2:
            ai_btn_clicked = st.button(
                "✨ Σύνθεση AI CFO γι' αυτή την Περίοδο",
                type="primary",
                icon=":material/auto_awesome:",
                width="stretch",
                key="btn_hybrid_ai_tab1"
            )

        top_3_cards = all_diagnostics[:3]
        col_pk1, col_pk2, col_pk3 = st.columns(3, gap="medium")
        for idx_c, (col_obj, d_card) in enumerate(zip([col_pk1, col_pk2, col_pk3], top_3_cards)):
            with col_obj:
                rank_prefix = f"#{idx_c + 1} " if d_card["severity_rank"] > 0 else ""
                st.html(f"""
                <div class="cfo-card {d_card['css_class']}">
                    <div style="font-size:0.78rem; text-transform:uppercase; letter-spacing:0.04em; opacity:0.85; margin-bottom:4px;">
                        {d_card['badge']}
                    </div>
                    <b style="font-size:1.02rem;">{rank_prefix}{d_card['title']}</b><br><br>
                    {d_card['body']}<br><br>
                    👉 <i>{d_card['action']}</i>
                </div>
                """)

        if len(all_diagnostics) > 3:
            with st.expander(f"📋 Προβολή και των {len(all_diagnostics)} Δυναμικών Δεικτών της Περιόδου ({period_label})"):
                rem_cols = st.columns(min(3, len(all_diagnostics) - 3), gap="medium")
                for idx_r, d_rem in enumerate(all_diagnostics[3:]):
                    with rem_cols[idx_r % len(rem_cols)]:
                        st.html(f"""
                        <div class="cfo-card {d_rem['css_class']}">
                            <div style="font-size:0.78rem; text-transform:uppercase; opacity:0.85; margin-bottom:4px;">{d_rem['badge']}</div>
                            <b>{d_rem['title']}</b><br><br>
                            {d_rem['body']}<br><br>
                            👉 <i>{d_rem['action']}</i>
                        </div>
                        """)

        # ΜΕΡΟΣ Β ΤΗΣ ΥΒΡΙΔΙΚΗΣ ΜΗΧΑΝΗΣ: ΕΞΑΤΟΜΙΚΕΥΜΕΝΕΣ ΚΑΡΤΕΣ AI CFO (ΜΟΝΙΜΗ ΑΠΟΘΗΚΕΥΣΗ ΣΤΗ ΒΑΣΗ & CACHE)
        if "hybrid_ai_cache" not in st.session_state:
            saved_hybrid_db = load_app_config_json("__LAST_HYBRID_CARDS__")
            st.session_state["hybrid_ai_cache"] = saved_hybrid_db if isinstance(saved_hybrid_db, dict) else {}

        cache_key_period = f"{start_d}_{end_d}_{completed_count}_{round(true_net_profit, 0)}"
        if ai_btn_clicked:
            if not gemini_api_key:
                st.error(f"Συμπληρώστε πρώτα το API Key ({ai_provider_choice}) στην Πλαϊνή Μπάρα (αριστερά)!")
            elif GENAI_AVAILABLE or ai_provider_choice != "Google Gemini":
                with st.spinner(f"Ο AI CFO ({selected_gemini_model}) συνδυάζει βάρδιες, υπηρεσίες και τεχνίτριες για την περίοδο {period_label}..."):
                    try:
                        ai_summary_payload = {
                            "period": period_label,
                            "appointments_completed": completed_count,
                            "cancelled": len(df_cancelled),
                            "no_shows": len(df_noshow),
                            "gross_revenue": round(gross_rev, 2),
                            "net_revenue": round(net_rev, 2),
                            "net_revenue_per_productive_hour": round(net_per_prod_h, 2),
                            "treatwell_commissions": round(comm_total, 2),
                            "materials_cost": round(mat_total, 2),
                            "productive_labor_cost": round(labor_prod_total, 2),
                            "idle_labor_cost": round(idle_labor_cost, 2),
                            "fixed_expenses": round(fixed_expenses, 2),
                            "true_net_profit": round(true_net_profit, 2),
                            "occupancy_pct": round(overall_occ_pct, 1),
                            "staff_performance": df_tech_perf[[
                                "Τεχνίτρια", "Ραντεβού", "Ώρες Βάρδιας", "Παραγωγικές Ώρες",
                                "Νεκρές Ώρες (Idle)", "Πληρότητα (%)", "Πραγματικό Ισοζύγιο (€)"
                            ]].to_dict(orient="records") if not df_tech_perf.empty else [],
                            "lowest_margin_services": srv_matrix.sort_values("Πλήρες_Καθαρό_Κέρδος").head(4)[
                                ["raw_service", "Ραντεβού", "Μικτός_Τζίρος", "Καθαρό_Έσοδο_Ώρας", "Πλήρες_Κέρδος_Ραντεβού"]
                            ].to_dict(orient="records") if not srv_matrix.empty else [],
                            "highest_margin_services": srv_matrix.sort_values("Πλήρες_Καθαρό_Κέρδος", ascending=False).head(3)[
                                ["raw_service", "Ραντεβού", "Μικτός_Τζίρος", "Καθαρό_Έσοδο_Ώρας", "Πλήρες_Κέρδος_Ραντεβού"]
                            ].to_dict(orient="records") if not srv_matrix.empty else []
                        }
                        prompt_hybrid = f"""
                        Είσαι ο Senior CFO του Nail Salon. Έχεις μπροστά σου τα ακριβή μαθηματικά δεδομένα της περιόδου: {period_label}.
                        ΜΗΝ επαναλάβεις απλώς τα νούμερα. Κάνε ΣΥΝΔΥΑΣΤΙΚΗ σκέψη (εστίασε στο χαμηλό καθαρό έσοδο ανά παραγωγική ώρα στις βασικές υπηρεσίες Ημιμόνιμο/Gel, ποιες υπηρεσίες χαλάνε τον χρόνο, ποιες τεχνίτριες έχουν αρνητικό ισοζύγιο και πώς κινήθηκε το Treatwell).
                        Επέστρεψε ΑΠΟΚΛΕΙΣΤΙΚΑ έγκυρο JSON (λίστα με 3 αντικείμενα) με την εξής δομή:
                        [
                          {{
                            "title": "Σύντομος τίτλος εύρηματος/δράσης #1",
                            "Finding": "2-3 προτάσεις συνδυαστικής διάγνωσης με ονόματα τεχνιτριών/υπηρεσιών της περιόδου",
                            "Action": "Συγκεκριμένη λύση και εκτιμώμενο όφελος σε Ευρώ"
                          }},
                          ... (3 κάρτες συνολικά)
                        ]
                        ΔΕΔΟΜΕΝΑ: {json.dumps(ai_summary_payload, ensure_ascii=False)}
                        """
                        raw_ai_json, used_m = call_gemini_smart(
                            api_key=gemini_api_key,
                            contents=prompt_hybrid,
                            expect_json=True,
                            preferred_model=selected_gemini_model
                        )
                        parsed_cards = clean_json_response(raw_ai_json)
                        if isinstance(parsed_cards, list) and len(parsed_cards) > 0:
                            st.session_state["hybrid_ai_cache"][cache_key_period] = {
                                "cards": parsed_cards,
                                "model": used_m,
                                "period": period_label,
                                "ts": datetime.now().strftime("%d/%m/%Y %H:%M")
                            }
                            save_app_config_json("__LAST_HYBRID_CARDS__", st.session_state["hybrid_ai_cache"])
                    except Exception as e:
                        st.error(f"Σφάλμα κατά την κλήση του Gemini AI: {e}")

        if cache_key_period in st.session_state["hybrid_ai_cache"]:
            cached_entry = st.session_state["hybrid_ai_cache"][cache_key_period]
            if isinstance(cached_entry, dict):
                ai_cards_list = cached_entry.get("cards", [])
                ai_model_used = cached_entry.get("model", "")
                ai_ts = cached_entry.get("ts", "")
            else:
                ai_cards_list, ai_model_used = cached_entry
                ai_ts = ""
            st.markdown(f"##### 🧠 Συνδυαστικό Σχέδιο Δράσης AI CFO για `{period_label}` *(Μοντέλο: `{ai_model_used}` {f'• Αποθηκεύτηκε: {ai_ts}' if ai_ts else ''})*")
            ai_cols = st.columns(min(3, len(ai_cards_list)), gap="medium")
            for idx_ai, c_item in enumerate(ai_cards_list[:3]):
                with ai_cols[idx_ai]:
                    st.html(f"""
                    <div class="cfo-card cfo-card-purple">
                        <div style="font-size:0.76rem; text-transform:uppercase; opacity:0.85; margin-bottom:4px;">✨ AI CFO Σύσταση #{idx_ai+1}</div>
                        <b style="font-size:1.0rem;">{c_item.get('title', '')}</b><br><br>
                        {c_item.get('Finding', '')}<br><br>
                        🎯 <b>Δράση & Όφελος:</b> <i>{c_item.get('Action', '')}</i>
                    </div>
                    """)

        st.space("small")

        # 3. ΚΑΤΑΡΡΑΚΤΗΣ P&L & ΧΡΟΝΙΚΗ ΕΞΕΛΙΞΗ (ΔΙΠΛΑ-ΔΙΠΛΑ)
        col_ch1, col_ch2 = st.columns([1.1, 1.2], gap="medium")

        with col_ch1:
            with st.container(border=True):
                st.subheader("📉 Καταρράκτης Κερδοφορίας (P&L Bridge)", icon=":material/waterfall_chart:")
                fig_water = go.Figure(go.Waterfall(
                    orientation="v",
                    measure=["absolute", "relative", "total", "relative", "relative", "relative", "total", "relative", "relative", "total"],
                    x=[
                        "Μικτός Τζίρος", "ΦΠΑ (-24%)", "Καθαρός Τζίρος",
                        "Προμήθειες TW", "Υλικά", "Εργασία Ραντεβού",
                        "Κέρδος Ραντεβού", "Νεκρός Χρόνος", "Πάγια Έξοδα", "Τελικό Αποτέλεσμα"
                    ],
                    y=[
                        gross_rev, -vat_total, 0,
                        -comm_total, -mat_total, -labor_prod_total,
                        0, -idle_labor_cost, -fixed_expenses, 0
                    ],
                    connector={"line": {"color": "#64748b", "width": 1.5}},
                    decreasing={"marker": {"color": "#ef4444"}},
                    increasing={"marker": {"color": "#10b981"}},
                    totals={"marker": {"color": "#3b82f6"}}
                ))
                fig_water.update_layout(height=360, margin=dict(t=20, b=20, l=10, r=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_water, width="stretch")

        with col_ch2:
            with st.container(border=True):
                use_monthly_trend = (end_d - start_d).days > 75
                st.subheader(
                    f"📈 Εξέλιξη ανά {'Μήνα' if use_monthly_trend else 'Εβδομάδα'} (Τζίρος vs Μισθοί vs Κέρδος)",
                    icon=":material/trending_up:"
                )
                if use_monthly_trend:
                    df_comp['ym_str'] = df_comp['appointment_date_dt'].dt.strftime('%Y-%m')
                    tr_grp = df_comp.groupby('ym_str').agg(
                        Καθαρός_Τζίρος=('net_revenue', 'sum'),
                        Προμήθειες=('commission_fee', 'sum'),
                        Υλικά=('material_cost', 'sum'),
                        Εργασία_Ραντεβού=('labor_cost', 'sum')
                    ).reset_index().sort_values('ym_str')
                    mo_pay = {}
                    for m_str, pay_val in daily_payroll_by_monday.items():
                        ym_k = m_str[:7]
                        mo_pay[ym_k] = mo_pay.get(ym_k, 0.0) + pay_val
                    tr_grp['Μισθοδοσία_Βαρδιών'] = [
                        round(max(float(r['Εργασία_Ραντεβού']), float(mo_pay.get(r['ym_str'], 0.0))), 2)
                        for _, r in tr_grp.iterrows()
                    ]
                    tr_grp['Πάγια'] = round(monthly_fixed, 2)
                    tr_grp['Καθαρό_Κέρδος'] = (
                        tr_grp['Καθαρός_Τζίρος'] - tr_grp['Προμήθειες'] - tr_grp['Υλικά'] - tr_grp['Μισθοδοσία_Βαρδιών'] - tr_grp['Πάγια']
                    ).round(2)
                    x_col_tr = 'ym_str'
                else:
                    tr_grp = df_comp.groupby('monday_date_str').agg(
                        Καθαρός_Τζίρος=('net_revenue', 'sum'),
                        Προμήθειες=('commission_fee', 'sum'),
                        Υλικά=('material_cost', 'sum'),
                        Εργασία_Ραντεβού=('labor_cost', 'sum')
                    ).reset_index().sort_values('monday_date_str')
                    tr_grp['Μισθοδοσία_Βαρδιών'] = [
                        round(max(float(r['Εργασία_Ραντεβού']), float(daily_payroll_by_monday.get(r['monday_date_str'], 0.0))), 2)
                        for _, r in tr_grp.iterrows()
                    ]
                    tr_grp['Πάγια'] = tr_grp['monday_date_str'].apply(
                        lambda m: round(monthly_fixed * (7.0 / 30.4375), 2) if wk_counts.get(m, 0) >= 25 else round(monthly_fixed * (3.0 / 30.4375), 2)
                    )
                    tr_grp['Καθαρό_Κέρδος'] = (
                        tr_grp['Καθαρός_Τζίρος'] - tr_grp['Προμήθειες'] - tr_grp['Υλικά'] - tr_grp['Μισθοδοσία_Βαρδιών'] - tr_grp['Πάγια']
                    ).round(2)
                    x_col_tr = 'monday_date_str'

                fig_tr = go.Figure()
                fig_tr.add_trace(go.Bar(x=tr_grp[x_col_tr], y=tr_grp['Καθαρός_Τζίρος'], name="Καθαρός Τζίρος (προ ΦΠΑ)", marker_color="#38bdf8"))
                fig_tr.add_trace(go.Bar(x=tr_grp[x_col_tr], y=tr_grp['Μισθοδοσία_Βαρδιών'], name="Μισθοδοσία Βαρδιών", marker_color="#f59e0b"))
                fig_tr.add_trace(go.Scatter(
                    x=tr_grp[x_col_tr], y=tr_grp['Καθαρό_Κέρδος'],
                    name="Καθαρό Αποτέλεσμα (€)",
                    mode="lines+markers+text",
                    text=[f"{v:+.0f}€" for v in tr_grp['Καθαρό_Κέρδος']],
                    textposition="top center",
                    line=dict(color="#10b981", width=3)
                ))
                fig_tr.update_layout(
                    barmode="group", height=360, margin=dict(t=20, b=20, l=10, r=10),
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
                )
                st.plotly_chart(fig_tr, width="stretch")

# =====================================================================
# TAB 2: ΣΥΓΚΡΙΣΗ ΠΕΡΙΟΔΩΝ (ΜΗΝΑΣ ΜΕ ΜΗΝΑ / ΕΤΟΣ ΜΕ ΕΤΟΣ / ΠΙΝΑΚΑΣ ΙΣΤΟΡΙΚΟΥ)
# =====================================================================
with tab_compare:
    if df_bookings_db.empty:
        st.info("Δεν υπάρχουν δεδομένα στη βάση για σύγκριση περιόδων.")
    else:
        comp_mode = st.segmented_control(
            "Επιλέξτε Τρόπο Σύγκρισης Περιόδων",
            options=[
                "⚖️ 1. Σύγκριση 2 Περιόδων Δίπλα-Δίπλα (Περίοδος Α vs Περίοδος Β)",
                "📊 2. Συγκριτικός Πίνακας Όλων των Ετών & Μηνών"
            ],
            default="⚖️ 1. Σύγκριση 2 Περιόδων Δίπλα-Δίπλα (Περίοδος Α vs Περίοδος Β)",
            key="comp_tab_mode"
        )

        st.space("small")

        if comp_mode.startswith("⚖️"):
            with st.container(border=True):
                st.subheader("⚖️ Σύγκριση 2 Περιόδων Δίπλα-Δίπλα (Μήνες, Έτη ή Εβδομάδες)", icon=":material/compare_arrows:")
                c_gran, c_pa, c_pb = st.columns([1.2, 1.9, 1.9], vertical_alignment="bottom")
                with c_gran:
                    gran_choice = st.radio(
                        "Επίπεδο Σύγκρισης:",
                        options=["📆 Ανά Μήνα", "📅 Ανά Έτος", "🗓️ Ανά Εβδομάδα"],
                        horizontal=True,
                        key="comp_gran_radio"
                    )

                if gran_choice == "📆 Ανά Μήνα":
                    active_map = month_options_map
                elif gran_choice == "📅 Ανά Έτος":
                    active_map = {k: v for k, v in year_options_map.items() if k.startswith("Έτος")}
                else:
                    active_map = week_options_map

                opt_keys = list(active_map.keys())
                if len(opt_keys) >= 2:
                    with c_pa:
                        sel_a_key = st.selectbox("🅰️ Περίοδος Α (Εστίαση):", options=opt_keys, index=0, key="comp_sel_a")
                    with c_pb:
                        sel_b_key = st.selectbox("🅱️ Περίοδος Β (Σύγκριση με):", options=opt_keys, index=min(1, len(opt_keys) - 1), key="comp_sel_b")

                    da_start, da_end, lbl_a = active_map[sel_a_key]
                    db_start, db_end, lbl_b = active_map[sel_b_key]

                    sum_a = compute_period_pnl_summary(da_start, da_end, lbl_a)
                    sum_b = compute_period_pnl_summary(db_start, db_end, lbl_b)

                    if sum_a and sum_b:
                        def fmt_delta(val_a, val_b, is_pct_point=False, suffix="€"):
                            diff = val_a - val_b
                            if is_pct_point:
                                return f"{diff:+.1f} π.μ. (έναντι {val_b:.1f}%)"
                            pct_ch = ((val_a - val_b) / abs(val_b) * 100.0) if abs(val_b) > 0.01 else 0.0
                            return f"{diff:+,.2f} {suffix} ({pct_ch:+.1f}%)"

                        m1, m2, m3, m4, m5 = st.columns(5)
                        m1.metric(
                            f"Μικτός Τζίρος ({lbl_a[:14]})",
                            f"{sum_a['Μικτός Τζίρος (€)']:,.2f} €",
                            fmt_delta(sum_a['Μικτός Τζίρος (€)'], sum_b['Μικτός Τζίρος (€)']),
                            border=True
                        )
                        m2.metric(
                            "Ραντεβού",
                            f"{sum_a['Ραντεβού']:,}",
                            f"{sum_a['Ραντεβού'] - sum_b['Ραντεβού']:+,} ραντ. ({((sum_a['Ραντεβού'] - sum_b['Ραντεβού']) / max(1, sum_b['Ραντεβού']) * 100):+.1f}%)",
                            border=True
                        )
                        m3.metric(
                            "Καθαρό Έσοδο / Ώρα",
                            f"{sum_a['Καθαρό Έσοδο / Παραγ. Ώρα (€/h)']:.2f} €/h",
                            fmt_delta(sum_a['Καθαρό Έσοδο / Παραγ. Ώρα (€/h)'], sum_b['Καθαρό Έσοδο / Παραγ. Ώρα (€/h)'], suffix="€/h"),
                            border=True
                        )
                        m4.metric(
                            "Μισθοδοσία & Πληρότητα",
                            f"{sum_a['Συνολική Μισθοδοσία (€)']:,.0f} € ({sum_a['Πληρότητα (%)']:.1f}%)",
                            fmt_delta(sum_a['Συνολική Μισθοδοσία (€)'], sum_b['Συνολική Μισθοδοσία (€)']),
                            delta_color="inverse",
                            border=True
                        )
                        m5.metric(
                            "Καθαρό Κέρδος",
                            f"{sum_a['Καθαρό Κέρδος (€)']:+,.2f} €",
                            fmt_delta(sum_a['Καθαρό Κέρδος (€)'], sum_b['Καθαρό Κέρδος (€)']),
                            border=True
                        )

                        col_cmp_ch, col_cmp_tbl = st.columns([1.15, 1.35], gap="medium")
                        with col_cmp_ch:
                            chart_metrics = [
                                "Μικτός Τζίρος (€)", "Καθαρός Τζίρος προ ΦΠΑ (€)",
                                "Συνολική Μισθοδοσία (€)", "Αναλώσιμα Υλικά (€)",
                                "Προμήθειες TW (€)", "Πάγια Έξοδα (€)", "Καθαρό Κέρδος (€)"
                            ]
                            fig_cmp = go.Figure()
                            fig_cmp.add_trace(go.Bar(
                                x=chart_metrics,
                                y=[sum_a[m] for m in chart_metrics],
                                name=f"🅰️ {lbl_a}",
                                marker_color="#3b82f6",
                                text=[f"{sum_a[m]:,.0f}€" for m in chart_metrics],
                                textposition="outside"
                            ))
                            fig_cmp.add_trace(go.Bar(
                                x=chart_metrics,
                                y=[sum_b[m] for m in chart_metrics],
                                name=f"🅱️ {lbl_b}",
                                marker_color="#94a3b8",
                                text=[f"{sum_b[m]:,.0f}€" for m in chart_metrics],
                                textposition="outside"
                            ))
                            fig_cmp.update_layout(
                                barmode="group", height=380, margin=dict(t=30, b=20, l=10, r=10),
                                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
                            )
                            st.plotly_chart(fig_cmp, width="stretch")

                        with col_cmp_tbl:
                            rows_cmp = []
                            ordered_keys = [
                                ("Ραντεβού", ""),
                                ("Μικτός Τζίρος (€)", "€"),
                                ("Καθαρός Τζίρος προ ΦΠΑ (€)", "€"),
                                ("Μέση Απόδειξη AOV (€)", "€"),
                                ("Μικτό Έσοδο / Παραγ. Ώρα (€/h)", "€/h"),
                                ("Καθαρό Έσοδο / Παραγ. Ώρα (€/h)", "€/h"),
                                ("Παραγωγικές Ώρες (h)", "h"),
                                ("Ώρες Βάρδιας (h)", "h"),
                                ("Πληρότητα (%)", "%"),
                                ("Εργασία σε Ραντεβού (€)", "€"),
                                ("Κόστος Νεκρού Χρόνου (€)", "€"),
                                ("Συνολική Μισθοδοσία (€)", "€"),
                                ("Αναλώσιμα Υλικά (€)", "€"),
                                ("Προμήθειες TW (€)", "€"),
                                ("Πάγια Έξοδα (€)", "€"),
                                ("Καθαρό Κέρδος (€)", "€"),
                                ("Περιθώριο Κέρδους (%)", "%")
                            ]
                            for k_name, unit_s in ordered_keys:
                                va = float(sum_a[k_name])
                                vb = float(sum_b[k_name])
                                diff_v = va - vb
                                pct_v = ((va - vb) / abs(vb) * 100.0) if abs(vb) > 0.01 else 0.0
                                rows_cmp.append({
                                    "Οικονομικός Δείκτης": k_name,
                                    f"🅰️ {lbl_a}": f"{va:,.2f} {unit_s}".strip() if unit_s != "" else f"{int(va):,}",
                                    f"🅱️ {lbl_b}": f"{vb:,.2f} {unit_s}".strip() if unit_s != "" else f"{int(vb):,}",
                                    "Διαφορά (Α - Β)": f"{diff_v:+,.2f} {unit_s}".strip() if unit_s != "" else f"{int(diff_v):+,}",
                                    "Μεταβολή (%)": f"{pct_v:+.1f}%"
                                })
                            st.dataframe(pd.DataFrame(rows_cmp), hide_index=True, width="stretch", height=380)

        else:
            with st.container(border=True):
                st.subheader("📊 Συγκριτικός Πίνακας Όλων των Ετών (2024 vs 2025 vs 2026)", icon=":material/calendar_today:")
                yr_summaries = []
                for y_k, (ys, ye, ylbl) in year_options_map.items():
                    if y_k.startswith("Έτος"):
                        s_y = compute_period_pnl_summary(ys, ye, ylbl)
                        if s_y:
                            yr_summaries.append(s_y)
                if yr_summaries:
                    df_yr_all = pd.DataFrame(yr_summaries)
                    st.dataframe(
                        df_yr_all[[
                            "Περίοδος", "Ραντεβού", "Μικτός Τζίρος (€)", "Καθαρός Τζίρος προ ΦΠΑ (€)",
                            "Καθαρό Έσοδο / Παραγ. Ώρα (€/h)", "Παραγωγικές Ώρες (h)", "Πληρότητα (%)",
                            "Συνολική Μισθοδοσία (€)", "Αναλώσιμα Υλικά (€)", "Προμήθειες TW (€)",
                            "Πάγια Έξοδα (€)", "Καθαρό Κέρδος (€)", "Περιθώριο Κέρδους (%)"
                        ]],
                        hide_index=True,
                        width="stretch"
                    )

            st.space("small")

            with st.container(border=True):
                st.subheader("📆 Συγκριτικός Πίνακας Όλων των Μηνών", icon=":material/date_range:")
                mo_summaries = []
                for m_k, (ms, me, mlbl) in month_options_map.items():
                    s_m = compute_period_pnl_summary(ms, me, mlbl)
                    if s_m:
                        mo_summaries.append(s_m)
                if mo_summaries:
                    df_mo_all = pd.DataFrame(mo_summaries)
                    st.dataframe(
                        df_mo_all[[
                            "Περίοδος", "Ραντεβού", "Μικτός Τζίρος (€)", "Καθαρός Τζίρος προ ΦΠΑ (€)",
                            "Καθαρό Έσοδο / Παραγ. Ώρα (€/h)", "Παραγωγικές Ώρες (h)", "Πληρότητα (%)",
                            "Συνολική Μισθοδοσία (€)", "Αναλώσιμα Υλικά (€)", "Προμήθειες TW (€)",
                            "Πάγια Έξοδα (€)", "Καθαρό Κέρδος (€)", "Περιθώριο Κέρδους (%)"
                        ]],
                        hide_index=True,
                        width="stretch"
                    )

# =====================================================================
# TAB 3: ΠΡΟΣΩΠΙΚΟ, IDLE TIME & ΒΕΛΤΙΣΤΟΠΟΙΗΤΗΣ ΒΑΡΔΙΩΝ
# =====================================================================
with tab_staff:
    if df_bookings_db.empty or df_tech_perf.empty:
        st.info("Δεν υπάρχουν δεδομένα προσωπικού για την επιλεγμένη περίοδο.")
    else:
        with st.container(border=True):
            st.subheader("👁️ Οπτική Ακτινογραφία Idle Time ανά Τεχνίτρια (Με μια Ματιά)", icon=":material/timelapse:")
            st.caption(
                "Το **Πράσινο** δείχνει πόσες ώρες η τεχνίτρια εξυπηρετούσε πελάτισσα. "
                "Το **Κόκκινο** δείχνει πόσες ώρες πληρωνόταν στη βάρδια χωρίς ραντεβού (Idle Time)."
            )

            df_plot_staff = df_tech_perf.sort_values("Ώρες Βάρδιας", ascending=True).copy()
            fig_idle_bar = go.Figure()
            fig_idle_bar.add_trace(go.Bar(
                y=df_plot_staff["Τεχνίτρια"],
                x=df_plot_staff["Παραγωγικές Ώρες"],
                name="🟢 Παραγωγικές Ώρες (Σε Ραντεβού)",
                orientation="h",
                marker_color="#10b981",
                text=[f"{p:,.1f}h ({u:.0f}%)" for p, u in zip(df_plot_staff["Παραγωγικές Ώρες"], df_plot_staff["Πληρότητα (%)"])],
                textposition="inside"
            ))
            fig_idle_bar.add_trace(go.Bar(
                y=df_plot_staff["Τεχνίτρια"],
                x=df_plot_staff["Νεκρές Ώρες (Idle)"],
                name="🔴 Νεκρός Χρόνος Αναμονής (Idle)",
                orientation="h",
                marker_color="#ef4444",
                text=[f"{i:,.1f}h κενές (-{c:,.0f}€)" if i > 0 else "" for i, c in zip(df_plot_staff["Νεκρές Ώρες (Idle)"], df_plot_staff["Κόστος Νεκρού Χρόνου (€)"])],
                textposition="inside"
            ))
            fig_idle_bar.update_layout(
                barmode="stack",
                height=300,
                margin=dict(t=15, b=15, l=10, r=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(title="Σύνολο Ωρών Βάρδιας στην Περίοδο", ticksuffix=" h"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
            )
            st.plotly_chart(fig_idle_bar, width="stretch")

        st.space("small")

        with st.container(border=True):
            c_st_h1, c_st_h2 = st.columns([2.5, 1.5], vertical_alignment="center")
            with c_st_h1:
                st.subheader("👩‍🔧 Ταμείο & Παραγωγικότητα ανά Τεχνίτρια", icon=":material/badge:")
            with c_st_h2:
                staff_tbl_mode = st.segmented_control(
                    "Προβολή Πίνακα",
                    options=["Απλή Προβολή (7 Στήλες)", "Πλήρης Λογιστική Προβολή"],
                    default="Απλή Προβολή (7 Στήλες)",
                    key="staff_tbl_mode_ctrl"
                )

            if staff_tbl_mode == "Απλή Προβολή (7 Στήλες)":
                cols_to_show = [
                    "Τεχνίτρια", "Ραντεβού", "Ώρες Βάρδιας", "Παραγωγικές Ώρες",
                    "Νεκρές Ώρες (Idle)", "Πληρότητα (%)", "Πραγματικό Ισοζύγιο (€)"
                ]
            else:
                cols_to_show = [
                    "Τεχνίτρια", "Ενεργές Εβδ.", "Ραντεβού", "Μικρά Ραντεβού (≤15')",
                    "Ώρες Βάρδιας", "Παραγωγικές Ώρες", "Νεκρές Ώρες (Idle)", "Πληρότητα (%)",
                    "Μικτός Τζίρος (€)", "Καθαρός Τζίρος (€)", "Προμήθειες TW (€)", "Υλικά (€)",
                    "Μισθοδοσία (€)", "Κόστος Νεκρού Χρόνου (€)", "Πραγματικό Ισοζύγιο (€)"
                ]

            st.dataframe(
                df_tech_perf[cols_to_show],
                hide_index=True,
                width="stretch",
                column_config={
                    "Πληρότητα (%)": st.column_config.ProgressColumn("Πληρότητα %", min_value=0, max_value=100, format="%.1f%%"),
                    "Μισθοδοσία (€)": st.column_config.NumberColumn("Μισθοδοσία (€)", format="%.2f €"),
                    "Κόστος Νεκρού Χρόνου (€)": st.column_config.NumberColumn("Κόστος Idle (€)", format="%.2f €"),
                    "Μικτός Τζίρος (€)": st.column_config.NumberColumn("Μικτός (€)", format="%.2f €"),
                    "Καθαρός Τζίρος (€)": st.column_config.NumberColumn("Καθαρός προ ΦΠΑ (€)", format="%.2f €"),
                    "Προμήθειες TW (€)": st.column_config.NumberColumn("TW Προμ. (€)", format="%.2f €"),
                    "Υλικά (€)": st.column_config.NumberColumn("Υλικά (€)", format="%.2f €"),
                    "Πραγματικό Ισοζύγιο (€)": st.column_config.NumberColumn("Καθαρό Ισοζύγιο (€)", format="%+.2f €")
                }
            )
            st.caption(
                "💡 **Πώς διαβάζεται το Καθαρό Ισοζύγιο (€):** Είναι ο Καθαρός Τζίρος (προ ΦΠΑ) που έφερε η τεχνίτρια, "
                "μείον τις προμήθειες Treatwell των ραντεβού της, μείον τα υλικά της, μείον ολόκληρη τη μισθοδοσία της βάρδιάς της."
            )

        st.space("small")

        with st.container(border=True):
            st.subheader("🗓️ Χάρτης Βαρδιών: Ποιες Ημέρες & Ώρες Κάθονται οι Τεχνίτριες;", icon=":material/grid_on:")
            st.caption(
                "**Κόκκινα κελιά (+):** Έχετε περισσότερες τεχνίτριες στη βάρδια απ' όσες χρειάζονται τα ραντεβού (Πλεόνασμα / Idle Time). "
                "**Μπλε κελιά (-):** Η ζήτηση ξεπερνά τις τεχνίτριες της βάρδιας."
            )

            safe_day_counts = np.maximum(1.0, period_weekday_counts)
            sched_cap_matrix = period_sched_total_matrix / safe_day_counts
            active_staff_grid = period_staff_per_hour_grid

            demand_total_matrix = np.zeros((6, 12))
            for _, r in df_comp.iterrows():
                dur_h = float(r.get('duration_minutes', 0)) / 60.0
                if dur_h <= 0:
                    continue
                dt_d = r['appointment_date_dt'].weekday()
                if dt_d > 5:
                    continue
                t_parts = str(r.get('appointment_time', '09:00:00')).split(':')
                st_h = int(t_parts[0]) + (int(t_parts[1]) / 60.0 if len(t_parts) > 1 else 0.0)
                en_h = st_h + dur_h
                for h_idx, h in enumerate(range(9, 21)):
                    ov = max(0.0, min(h + 1.0, en_h) - max(float(h), st_h))
                    demand_total_matrix[dt_d, h_idx] += ov

            demand_matrix = demand_total_matrix / safe_day_counts
            excess_matrix = np.round(sched_cap_matrix - demand_matrix, 1)
            hours_labels = [f"{h:02d}:00" for h in range(9, 21)]

            fig_opt = px.imshow(
                excess_matrix,
                x=hours_labels,
                y=DAYS_GR,
                color_continuous_scale="RdBu_r",
                color_continuous_midpoint=0.0,
                text_auto=True,
                aspect="auto",
                labels=dict(x="Ώρα Ημέρας", y="Ημέρα", color="Πλεόνασμα (+) / Έλλειψη (-)")
            )
            fig_opt.update_layout(height=330, margin=dict(t=15, b=15, l=10, r=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_opt, width="stretch")

            avg_h_cost = df_staff_db[df_staff_db['hourly_cost'] > 0]['hourly_cost'].mean() if not df_staff_db.empty else 7.5
            ov_blocks, un_blocks = generate_dynamic_shift_insights(sched_cap_matrix, demand_matrix, active_staff_grid, avg_h_cost)

            st.markdown("##### 🎯 Οι 3 Κυριότερες Προτάσεις Ανακατανομής Βαρδιών")
            c_o1, c_o2, c_o3 = st.columns(3, gap="medium")
            with c_o1:
                if len(ov_blocks) >= 1:
                    b1 = ov_blocks[0]
                    st.error(
                        f"**🔴 #1 Υπερστελέχωση: {b1['Ημέρα']} {b1['Ζώνη Ώρας']}**\n\n"
                        f"Έχετε **{b1['Μ.Ο. Βάρδιας']} τεχνίτριες** ({b1['Τεχνίτριες στη Βάρδια']}), "
                        f"ενώ η ζήτηση είναι **{b1['Μ.Ο. Ζήτησης']}** (+{b1['Πλεόνασμα (Τεχν.)']} κάθονται).\n\n"
                        f"💸 Κόστος: **-{b1['Κόστος/Εβδ. (€)']:,.2f} € / εβδομάδα**"
                    )
                else:
                    st.success("**🟢 Καμία Σοβαρή Υπερστελέχωση**")
            with c_o2:
                if len(ov_blocks) >= 2:
                    b2 = ov_blocks[1]
                    st.warning(
                        f"**🟠 #2 Υπερστελέχωση: {b2['Ημέρα']} {b2['Ζώνη Ώρας']}**\n\n"
                        f"Έχετε **{b2['Μ.Ο. Βάρδιας']} τεχνίτριες** ({b2['Τεχνίτριες στη Βάρδια']}), "
                        f"ενώ η ζήτηση είναι **{b2['Μ.Ο. Ζήτησης']}** (+{b2['Πλεόνασμα (Τεχν.)']} κάθονται).\n\n"
                        f"💸 Κόστος: **-{b2['Κόστος/Εβδ. (€)']:,.2f} € / εβδομάδα**"
                    )
                else:
                    st.success("**🟢 Ισορροπημένες Βάρδιες**")
            with c_o3:
                if len(un_blocks) >= 1:
                    u1 = un_blocks[0]
                    st.info(
                        f"**🔵 #1 Ζώνη Υψηλής Ζήτησης: {u1['Ημέρα']} {u1['Ζώνη Ώρας']}**\n\n"
                        f"Στη βάρδια βρίσκονται **{u1['Μ.Ο. Βάρδιας']} τεχνίτριες**, "
                        f"ενώ η ζήτηση φτάνει τις **{u1['Μ.Ο. Ζήτησης']}** (έλλειμμα **-{u1['Έλλειμμα (Τεχν.)']}**).\n\n"
                        f"💡 Μεταφέρετε εδώ βάρδια από τις κόκκινες ζώνες!"
                    )
                else:
                    st.success("**🟢 Πλήρης Κάλυψη Αιχμής**")

            with st.expander("📊 Προβολή Αναλυτικής Λίστας Όλων των Ζωνών Υπερστελέχωσης & Έλλειψης"):
                col_tb1, col_tb2 = st.columns(2)
                with col_tb1:
                    st.markdown("**Ζώνες Υπερστελέχωσης (Ταξινομημένες κατά Εβδομαδιαίο Κόστος)**")
                    if ov_blocks:
                        st.dataframe(pd.DataFrame(ov_blocks), hide_index=True, width="stretch")
                with col_tb2:
                    st.markdown("**Ζώνες Έλλειψης Προσωπικού**")
                    if un_blocks:
                        st.dataframe(pd.DataFrame(un_blocks), hide_index=True, width="stretch")

# =====================================================================
# TAB 3: ΥΠΗΡΕΣΙΕΣ, ΚΑΝΑΛΙΑ TREATWELL & ΠΩΛΗΣΕΙΣ ΛΙΑΝΙΚΗΣ
# =====================================================================
with tab_services:
    if df_bookings_db.empty or srv_matrix.empty:
        st.info("Δεν υπάρχουν δεδομένα υπηρεσιών για την επιλεγμένη περίοδο.")
    else:
        col_top_loss, col_top_win = st.columns(2, gap="medium")
        with col_top_loss:
            with st.container(border=True):
                st.subheader("⚠️ Top 3 Υπηρεσίες Χαμηλότερου Περιθωρίου", icon=":material/trending_down:")
                st.caption("Αξιολόγηση βάσει πραγματικού κόστους εκτέλεσης (ΦΠΑ, TW, Υλικά, Εργασία Τεχνίτριας & Πάγιο Καρέκλας):")
                worst_3 = srv_matrix.sort_values(by="Πλήρες_Κέρδος_Ραντεβού", ascending=True).head(3)
                for _, r_w in worst_3.iterrows():
                    st.error(
                        f"**{r_w['raw_service']}** ({int(r_w['Ραντεβού'])} ραντεβού • ~{int(r_w['Μέση_Διάρκεια_Λεπτά'])}')\n\n"
                        f"• Μικτός Τζίρος: **{r_w['Μικτός_Τζίρος']:,.0f} €** | Άμεσο Κέρδος: **{r_w['Άμεσο_Κέρδος_Ραντεβού']:+,.2f} € / ραντ.**\n"
                        f"• Μετά το Πάγιο Καρέκλας: **{r_w['Πλήρες_Κέρδος_Ραντεβού']:+,.2f} € / ραντεβού** (Σύνολο: **{r_w['Πλήρες_Καθαρό_Κέρδος']:+,.2f} €**)"
                    )

        with col_top_win:
            with st.container(border=True):
                st.subheader("🏆 Top 3 Πιο Κερδοφόρες Υπηρεσίες", icon=":material/workspace_premium:")
                st.caption("Οι υπηρεσίες που αφήνουν το μεγαλύτερο καθαρό κέρδος στην επιχείρηση:")
                best_3 = srv_matrix.sort_values(by="Πλήρες_Καθαρό_Κέρδος", ascending=False).head(3)
                for _, r_b in best_3.iterrows():
                    st.success(
                        f"**{r_b['raw_service']}** ({int(r_b['Ραντεβού'])} ραντεβού • ~{int(r_b['Μέση_Διάρκεια_Λεπτά'])}')\n\n"
                        f"• Μικτός Τζίρος: **{r_b['Μικτός_Τζίρος']:,.0f} €** | Άμεσο Κέρδος: **{r_b['Άμεσο_Κέρδος_Ραντεβού']:+,.2f} € / ραντ.**\n"
                        f"• Καθαρό μετά το Πάγιο Καρέκλας: **{r_b['Πλήρες_Κέρδος_Ραντεβού']:+,.2f} € / ραντεβού** (Σύνολο: **{r_b['Πλήρες_Καθαρό_Κέρδος']:+,.2f} €**)"
                    )

        st.space("small")

        with st.container(border=True):
            c_sv1, c_sv2 = st.columns([2.4, 1.6], vertical_alignment="center")
            with c_sv1:
                st.subheader("💅 Πίνακας Κερδοφορίας ανά Υπηρεσία", icon=":material/table_chart:")
            with c_sv2:
                srv_tbl_mode = st.segmented_control(
                    "Λεπτομέρεια Πίνακα",
                    options=["Απλή Προβολή (6 Στήλες)", "Πλήρης Κοστολόγηση (11 Στήλες)"],
                    default="Απλή Προβολή (6 Στήλες)",
                    key="srv_tbl_mode_ctrl"
                )

            if srv_tbl_mode == "Απλή Προβολή (6 Στήλες)":
                srv_cols = [
                    "raw_service", "Ραντεβού", "Μέση_Διάρκεια_Λεπτά",
                    "Μικτός_Τζίρος", "Άμεσο_Κέρδος_Ραντεβού", "Πλήρες_Κέρδος_Ραντεβού"
                ]
            else:
                srv_cols = [
                    "raw_service", "Ραντεβού", "Μέση_Διάρκεια_Λεπτά", "Μικτός_Τζίρος",
                    "Προμήθειες", "Υλικά", "Εργασία", "Άμεσο_Κέρδος",
                    "Αναλογία_Παγίων", "Πλήρες_Καθαρό_Κέρδος", "Πλήρες_Κέρδος_Ραντεβού"
                ]

            st.dataframe(
                srv_matrix[srv_cols],
                hide_index=True,
                width="stretch",
                column_config={
                    "raw_service": st.column_config.TextColumn("Υπηρεσία"),
                    "Μέση_Διάρκεια_Λεπτά": st.column_config.NumberColumn("Διάρκεια (λ.)", format="%d'"),
                    "Μικτός_Τζίρος": st.column_config.NumberColumn("Μικτός Τζίρος (€)", format="%.2f €"),
                    "Άμεσο_Κέρδος": st.column_config.NumberColumn("Άμεσο Κέρδος (€)", format="%+.2f €"),
                    "Άμεσο_Κέρδος_Ραντεβού": st.column_config.NumberColumn("Άμεσο / Ραντ. (€)", format="%+.2f €"),
                    "Αναλογία_Παγίων": st.column_config.NumberColumn("Αναλογία Παγίων (€)", format="-%.2f €"),
                    "Πλήρες_Καθαρό_Κέρδος": st.column_config.NumberColumn("Πλήρες Κέρδος (€)", format="%+.2f €"),
                    "Πλήρες_Κέρδος_Ραντεβού": st.column_config.NumberColumn("Πλήρες / Ραντ. (€)", format="%+.2f €")
                }
            )

        st.space("small")

        col_chan, col_ret = st.columns([1.2, 1.2], gap="medium")

        with col_chan:
            with st.container(border=True):
                st.subheader("🌐 Κερδοφορία ανά Κανάλι Κράτησης", icon=":material/hub:")
                chan_grp = df_comp.groupby('channel').agg(
                    Ραντεβού=('unique_id', 'count'),
                    Μικτός_Τζίρος=('gross_price', 'sum'),
                    Προμήθεια_TW=('commission_fee', 'sum'),
                    Άμεσο_Κέρδος=('net_profit', 'sum')
                ).reset_index()
                chan_grp['Κέρδος / Ραντεβού (€)'] = (chan_grp['Άμεσο_Κέρδος'] / chan_grp['Ραντεβού']).round(2)
                st.dataframe(
                    chan_grp,
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "channel": st.column_config.TextColumn("Κανάλι"),
                        "Μικτός_Τζίρος": st.column_config.NumberColumn("Μικτός (€)", format="%.2f €"),
                        "Προμήθεια_TW": st.column_config.NumberColumn("Προμήθεια (€)", format="%.2f €"),
                        "Άμεσο_Κέρδος": st.column_config.NumberColumn("Άμεσο Κέρδος (€)", format="%+.2f €")
                    }
                )

        with col_ret:
            with st.container(border=True):
                st.subheader("🛍️ Πωλήσεις Λιανικής (Retail POS)", icon=":material/shopping_bag:")
                df_products = df_services_db[df_services_db['category'] == 'Προϊόντα Λιανικής'].copy() if not df_services_db.empty else pd.DataFrame()
                df_retail_sales = df_comp[df_comp['clean_category'] == 'Προϊόντα Λιανικής'].copy() if not df_comp.empty else pd.DataFrame()

                ret_gross = float(df_retail_sales['gross_price'].sum()) if not df_retail_sales.empty else 0.0
                ret_cogs = float(df_retail_sales['material_cost'].sum()) if not df_retail_sales.empty else 0.0
                ret_net = float(df_retail_sales['net_revenue'].sum()) if not df_retail_sales.empty else 0.0
                ret_profit = ret_net - ret_cogs

                r_m1, r_m2, r_m3 = st.columns(3)
                r_m1.metric("Τζίρος Λιανικής", f"{ret_gross:,.2f} €", border=True)
                r_m2.metric("Κόστος (COGS)", f"{ret_cogs:,.2f} €", border=True)
                r_m3.metric("Καθαρό Κέρδος", f"{ret_profit:+,.2f} €", border=True)

                if not df_products.empty:
                    with st.expander("➕ Καταχώρηση Νέας Πώλησης Προϊόντος Λιανικής"):
                        c_pos1, c_pos2, c_pos3 = st.columns([2, 1, 1], vertical_alignment="bottom")
                        with c_pos1:
                            sel_prod_name = st.selectbox("Προϊόν", options=df_products['service_name'].tolist(), key="pos_prod_sel")
                        with c_pos2:
                            sel_prod_qty = st.number_input("Τεμάχια", min_value=1, max_value=20, value=1, key="pos_prod_qty")
                        with c_pos3:
                            if st.button("Καταχώρηση", type="primary", width="stretch", key="btn_pos_add"):
                                p_row = df_products[df_products['service_name'] == sel_prod_name].iloc[0]
                                u_price = float(p_row['gross_price']) * sel_prod_qty
                                u_cost = float(p_row['material_cost_estimate']) * sel_prod_qty
                                u_net = round(u_price / (1.0 + vat_rate_pct / 100.0), 2)
                                u_vat = round(u_price - u_net, 2)
                                now_dt = datetime.now()
                                uid = f"RET_{now_dt.strftime('%Y%m%d%H%M%S')}_{slugify(sel_prod_name)}"
                                new_rec = {
                                    "unique_id": uid, "order_id": uid, "channel": "Direct (Τηλέφωνο)",
                                    "order_date": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                                    "appointment_date": end_d.strftime("%Y-%m-%d"),
                                    "appointment_time": now_dt.strftime("%H:%M:%S"),
                                    "staff_name": "Δημήτρης", "customer_name": "ΠΕΛΑΤΗΣ ΛΙΑΝΙΚΗΣ",
                                    "raw_service": sel_prod_name, "clean_category": "Προϊόντα Λιανικής",
                                    "duration_minutes": 0, "gross_price": u_price, "vat_amount": u_vat,
                                    "net_revenue": u_net, "commission_pct": 0.0, "commission_fee": 0.0,
                                    "material_cost": u_cost, "labor_cost": 0.0, "net_profit": round(u_net - u_cost, 2),
                                    "status_raw": "Πώληση Λιανικής", "status_group": "COMPLETED", "is_first_visit": False
                                }
                                supabase.table("bookings_master").upsert([new_rec]).execute()
                                st.toast(f"Καταχωρήθηκε πώληση: {sel_prod_qty}x {sel_prod_name}!", icon=":material/check_circle:")
                                refresh_data()

# =====================================================================
# TAB 4: ΠΡΟΣΟΜΟΙΩΤΗΣ ΚΕΡΔΟΦΟΡΙΑΣ (@st.fragment) & AI CFO
# =====================================================================
@st.fragment
def render_profitability_simulator(curr_net_profit, curr_idle_cost, curr_net_rev, curr_comm_total, curr_appts_count, p_label):
    with st.container(border=True):
        st.subheader("🎛️ Διαδραστικός Προσομοιωτής Επιστροφής σε Κερδοφορία (Live What-If)", icon=":material/tune:")
        st.caption(f"Μετακινήστε τους 4 μοχλούς για να δείτε **άμεσα** πώς αλλάζει το Καθαρό Κέρδος στην περίοδο **{p_label}**:")

        col_sl1, col_sl2, col_sl3, col_sl4 = st.columns(4, gap="medium")
        with col_sl1:
            with st.container(border=True):
                st.markdown("**1. Συμμάζεμα Βαρδιών (Idle)**")
                sim_idle_cut_pct = st.slider("Μείωση νεκρών ωρών (%)", 0, 80, 35, 5, key="sim_sl_idle")
                gain_idle = curr_idle_cost * (sim_idle_cut_pct / 100.0)
                st.caption(f"Όφελος: **+{gain_idle:,.2f} €**")
        with col_sl2:
            with st.container(border=True):
                st.markdown("**2. Αναπροσαρμογή Τιμών**")
                sim_price_inc_pct = st.slider("Αύξηση τιμών καταλόγου (%)", 0, 30, 10, 2, key="sim_sl_price")
                gain_price = curr_net_rev * (sim_price_inc_pct / 100.0)
                st.caption(f"Όφελος (προ ΦΠΑ): **+{gain_price:,.2f} €**")
        with col_sl3:
            with st.container(border=True):
                st.markdown("**3. Re-booking (TW ➔ Direct)**")
                sim_tw_save_pct = st.slider("Μετατροπή TW σε Direct (%)", 0, 90, 50, 5, key="sim_sl_tw")
                gain_tw = curr_comm_total * (sim_tw_save_pct / 100.0)
                st.caption(f"Όφελος προμηθειών: **+{gain_tw:,.2f} €**")
        with col_sl4:
            with st.container(border=True):
                st.markdown("**4. Upsell / Λιανική / Αφαίρεση**")
                sim_extra_per_appt = st.slider("Επιπλέον καθαρό €/ραντεβού", 0.0, 5.0, 1.5, 0.5, key="sim_sl_upsell")
                gain_upsell = curr_appts_count * sim_extra_per_appt
                st.caption(f"Όφελος Upsell: **+{gain_upsell:,.2f} €**")

        total_sim_gain = gain_idle + gain_price + gain_tw + gain_upsell
        simulated_net_profit = curr_net_profit + total_sim_gain

        c_res1, c_res2, c_res3 = st.columns(3)
        c_res1.metric("Τρέχον Καθαρό Αποτέλεσμα", f"{curr_net_profit:+,.2f} €", delta_color="off", border=True)
        c_res2.metric("Συνολικό Όφελος Παρεμβάσεων", f"+{total_sim_gain:,.2f} €", "Από τους 4 μοχλούς", delta_color="normal", border=True)
        c_res3.metric(
            "Νέο Καθαρό Κέρδος στην Τσέπη",
            f"{simulated_net_profit:+,.2f} €",
            f"Βελτίωση +{total_sim_gain:,.0f} €",
            delta_color="normal" if simulated_net_profit >= 0 else "inverse",
            border=True
        )

        fig_sim = go.Figure()
        fig_sim.add_trace(go.Bar(
            x=["Τρέχον Καθαρό Αποτέλεσμα", "+ Συμμάζεμα Βαρδιών", "+ Αναπροσαρμογή Τιμών", "+ Re-booking Direct", "+ Upsell/Λιανική", "Νέο Καθαρό Κέρδος"],
            y=[curr_net_profit, gain_idle, gain_price, gain_tw, gain_upsell, simulated_net_profit],
            marker_color=["#ef4444" if curr_net_profit < 0 else "#3b82f6", "#10b981", "#10b981", "#10b981", "#10b981", "#10b981" if simulated_net_profit >= 0 else "#f59e0b"],
            text=[f"{v:+,.0f} €" for v in [curr_net_profit, gain_idle, gain_price, gain_tw, gain_upsell, simulated_net_profit]],
            textposition="outside"
        ))
        fig_sim.update_layout(height=280, margin=dict(t=25, b=10, l=10, r=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_sim, width="stretch")

with tab_sim:
    if not df_bookings_db.empty:
        render_profitability_simulator(true_net_profit, idle_labor_cost, net_rev, comm_total, completed_count, period_label)

        st.space("small")

        with st.container(border=True):
            st.subheader("🧠 Πλήρης Έκθεση Virtual CFO (Gemini Deep Audit — Μόνιμα Αποθηκευμένη στη Βάση)", icon=":material/psychology:")
            st.caption(
                "Η τελευταία έκθεση Deep Audit αποθηκεύεται αυτόματα στη βάση δεδομένων (`Supabase`), ώστε **να μη χάνεται ποτέ στο Refresh (F5)** "
                "και να μην ξαναχρεώνει κλήση στο Gemini μέχρι να πατήσετε ξανά το κουμπί!"
            )

            if "last_deep_audit" not in st.session_state:
                saved_audit = load_app_config_json("__LAST_DEEP_AUDIT__")
                st.session_state["last_deep_audit"] = saved_audit if isinstance(saved_audit, dict) else None

            if st.button("🚀 Εκτέλεση Νέου Βαθέως Οικονομικού Ελέγχου (AI CFO)", type="primary", icon=":material/auto_awesome:"):
                if not gemini_api_key:
                    st.error(f"Συμπληρώστε πρώτα το API Key ({ai_provider_choice}) στην Πλαϊνή Μπάρα (αριστερά)!")
                elif GENAI_AVAILABLE or ai_provider_choice != "Google Gemini":
                    with st.spinner(f"Ο Virtual CFO ({selected_gemini_model}) αναλύει τα οικονομικά δεδομένα της περιόδου και αποθηκεύει την έκθεση στη βάση..."):
                        try:
                            ai_payload = {
                                "period": period_label,
                                "weeks_covered": len(period_weeks_seen),
                                "exact_schedule_weeks": len(period_exact_weeks_seen),
                                "gross_revenue": round(float(gross_rev), 2),
                                "net_revenue": round(float(net_rev), 2),
                                "net_revenue_per_productive_hour": round(float(net_per_prod_h), 2),
                                "treatwell_commissions": round(float(comm_total), 2),
                                "materials": round(float(mat_total), 2),
                                "productive_labor": round(float(labor_prod_total), 2),
                                "idle_labor_cost": round(float(idle_labor_cost), 2),
                                "fixed_expenses": round(float(fixed_expenses), 2),
                                "true_net_profit": round(float(true_net_profit), 2),
                                "technician_occupancy_pct": round(float(overall_occ_pct), 1),
                                "staff_breakdown": df_tech_perf.to_dict(orient="records") if not df_tech_perf.empty else [],
                                "worst_services": srv_matrix.sort_values(by="Πλήρες_Καθαρό_Κέρδος", ascending=True).head(5)[
                                    ["raw_service", "Ραντεβού", "Μικτός_Τζίρος", "Καθαρό_Έσοδο_Ώρας", "Άμεσο_Κέρδος", "Πλήρες_Καθαρό_Κέρδος"]
                                ].to_dict(orient="records") if not srv_matrix.empty else []
                            }
                            prompt_cfo = f"""
                            Είσαι ο Οικονομικός Διευθυντής (CFO) του Nail Salon.
                            Ανάλυσε τα παρακάτω οικονομικά δεδομένα της περιόδου ({period_label}) και δώσε σε καθαρά Ελληνικά:
                            1. Ακριβή διάγνωση γιατί το κατάστημα έχει αυτό το καθαρό αποτέλεσμα ενώ κάνει υψηλό τζίρο (εστίασε ιδιαίτερα στο καθαρό έσοδο ανά παραγωγική ώρα στις βασικές υπηρεσίες Ημιμόνιμο/Gel vs κόστος ώρας).
                            2. Τις 4 πιο αποδοτικές κινήσεις (ανατιμήσεις συγκεκριμένων υπηρεσιών, μείωση διάρκειας, Treatwell re-booking, βάρδιες ανά τεχνίτρια) με ακριβή εκτίμηση ευρώ.
                            ΔΕΔΟΜΕΝΑ: {json.dumps(ai_payload, ensure_ascii=False)}
                            """
                            resp_txt, used_m = call_gemini_smart(
                                api_key=gemini_api_key,
                                contents=prompt_cfo,
                                expect_json=False,
                                preferred_model=selected_gemini_model,
                                provider=ai_provider_choice
                            )
                            audit_obj = {
                                "period": period_label,
                                "model": used_m,
                                "ts": datetime.now().strftime("%d/%m/%Y %H:%M"),
                                "report_md": resp_txt
                            }
                            st.session_state["last_deep_audit"] = audit_obj
                            save_app_config_json("__LAST_DEEP_AUDIT__", audit_obj)
                            st.toast("Η έκθεση Deep Audit αποθηκεύτηκε μόνιμα στη βάση!", icon=":material/check_circle:")
                        except Exception as e:
                            st.error(f"Σφάλμα AI API: {e}")

            active_audit = st.session_state.get("last_deep_audit")
            if isinstance(active_audit, dict) and active_audit.get("report_md"):
                st.divider()
                st.info(
                    f"📌 **Αποθηκευμένη Έκθεση Deep Audit** • Περίοδος Ανάλυσης: **`{active_audit.get('period', '-')}`** • "
                    f"Ημερομηνία: `{active_audit.get('ts', '-')}` • Μοντέλο: `{active_audit.get('model', '-')}`",
                    icon=":material/bookmark_added:"
                )
                st.markdown(active_audit["report_md"])

# =====================================================================
# TAB 5: ΔΕΔΟΜΕΝΑ, ΒΑΡΔΙΕΣ & ΡΥΘΜΙΣΕΙΣ (ΟΡΓΑΝΩΜΕΝΑ ΣΕ 3 ΥΠΟ-ΕΝΟΤΗΤΕΣ)
# =====================================================================
with tab_admin:
    admin_section = st.segmented_control(
        "Επιλέξτε Εργαλείο Διαχείρισης",
        options=[
            "📅 1. Εβδομαδιαίες Βάρδιες (Editor, JSON & AI Screenshots)",
            "📥 2. Εισαγωγή Ραντεβού (Multi-CSV Importer)",
            "💶 3. Κοστολόγηση (Υπηρεσίες, Προσωπικό & Πάγια)"
        ],
        default="📅 1. Εβδομαδιαίες Βάρδιες (Editor, JSON & AI Screenshots)",
        key="admin_sub_nav"
    )

    st.space("small")

    if admin_section == "📅 1. Εβδομαδιαίες Βάρδιες (Editor, JSON & AI Screenshots)":
        editor_week_map = {"📌 Προεπιλεγμένο Πρόγραμμα (WEEK_DEFAULT)": "WEEK_DEFAULT"}
        for m_d in sorted_mondays:
            m_str = m_d.strftime("%Y-%m-%d")
            sun_d = m_d + timedelta(days=6)
            iso_yr, iso_wk, _ = m_d.isocalendar()
            w_key = f"WEEK_{m_str}"
            if w_key in db_schedules:
                lbl = f"✅ Εβδ. {iso_wk}/{iso_yr} ({m_d.strftime('%d/%m/%Y')} – {sun_d.strftime('%d/%m/%Y')}) [Αποθηκευμένο]"
            else:
                lbl = f"🔄 Εβδ. {iso_wk}/{iso_yr} ({m_d.strftime('%d/%m/%Y')} – {sun_d.strftime('%d/%m/%Y')}) [Κληρονομεί προηγούμενο]"
            editor_week_map[lbl] = w_key

        col_sh_left, col_sh_right = st.columns([1.15, 1.85], gap="medium")

        with col_sh_left:
            with st.container(border=True):
                st.markdown("##### ⚡ Μαζική Εισαγωγή Ετήσιου Αρχείου JSON")
                json_sched_file = st.file_uploader("Επιλογή αρχείου .json", type=["json"], key="json_sched_up")
                set_json_def = st.checkbox("Ορισμός της πιο πρόσφατης εβδομάδας ως Προεπιλεγμένης (Default)", value=True, key="chk_json_def")

                if json_sched_file is not None:
                    try:
                        raw_bytes = json_sched_file.getvalue().decode("utf-8-sig")
                        loaded_sched_map = json.loads(raw_bytes)
                        valid_wks = {k: v for k, v in loaded_sched_map.items() if isinstance(v, list) and len(v) > 0}
                        if not valid_wks:
                            st.error("Το αρχείο JSON δεν περιέχει έγκυρες λίστες βαρδιών!")
                        else:
                            st.success(f"Βρέθηκαν **{len(valid_wks)} εβδομάδες** στο αρχείο.")
                            if st.button("🚀 Αποθήκευση Εγκύρων Εβδομάδων στη Βάση", type="primary", width="stretch", key="btn_save_json_sched"):
                                saved_n = save_multiple_week_schedules_to_db(valid_wks, update_default=set_json_def)
                                st.cache_data.clear()
                                st.success(f"✅ Αποθηκεύτηκαν {saved_n} καθαρισμένα προγράμματα στη Supabase!")
                                st.rerun()
                    except Exception as e:
                        st.error(f"Σφάλμα ανάγνωσης/αποθήκευσης JSON: {e}")

            with st.container(border=True):
                st.markdown("##### 📸 Μαζική Εισαγωγή Screenshots (Bulk AI Vision)")
                sched_img_files = st.file_uploader(
                    "Επιλέξτε ένα ή περισσότερα Screenshots Προγράμματος",
                    type=["png", "jpg", "jpeg", "webp"],
                    accept_multiple_files=True,
                    key="sched_img_bulk_up"
                )
                set_last_as_default = st.checkbox("Ορισμός του τελευταίου screenshot ως Default", value=False)

                if sched_img_files:
                    if st.button(f"🚀 Μαζική Ανάγνωση & Αποθήκευση ({len(sched_img_files)} Screenshots)", type="primary", icon=":material/auto_awesome:", width="stretch"):
                        if not gemini_api_key:
                            st.error(f"Συμπληρώστε πρώτα το API Key ({ai_provider_choice}) στην Πλαϊνή Μπάρα (Sidebar)!")
                        elif not GENAI_AVAILABLE and ai_provider_choice == "Google Gemini":
                            st.error("Δεν βρέθηκε η βιβλιοθήκη google-genai.")
                        else:
                            prog_bar = st.progress(0.0, text="Έναρξη ανάγνωσης screenshots...")
                            batch_schedules_to_save = {}
                            success_weeks = []
                            failed_files = []

                            prompt_vis = """
                            Διάβασε τον πίνακα εβδομαδιαίου προγράμματος βαρδιών από την εικόνα.
                            Αγνόησε τη γραμμή "Ώρες λειτουργίας καταστήματος".
                            ΠΡΟΣΟΧΗ: Αν μια ημέρα (π.χ. Δευτέρα) είναι κενή (λευκό κελί χωρίς ωράριο ή αν ο υπάλληλος έχει 64.5 ώρες επειδή δεν έχει προσληφθεί ακόμα), βάλε "" στην αντίστοιχη ημέρα και ΜΗΝ μετατοπίζεις το ωράριο της Τρίτης στη Δευτέρα!
                            Επέστρεψε ΑΠΟΚΛΕΙΣΤΙΚΑ έγκυρο JSON με την εξής δομή:
                            {
                              "monday_date": "YYYY-MM-DD",
                              "schedule": [
                                {
                                  "staff_name": "Νάντια",
                                  "Δευτέρα": "18:00 - 20:30",
                                  "Τρίτη": "",
                                  "Τετάρτη": "17:30 - 20:30",
                                  "Πέμπτη": "18:00 - 20:00",
                                  "Παρασκευή": "18:00 - 20:30",
                                  "Σάββατο": ""
                                }
                              ]
                            }
                            """

                            for idx_f, img_f in enumerate(sched_img_files):
                                prog_bar.progress(idx_f / len(sched_img_files), text=f"Ανάγνωση {idx_f+1}/{len(sched_img_files)}: {img_f.name}...")
                                try:
                                    img_bytes = img_f.getvalue()
                                    mime_t = img_f.type or "image/png"
                                    img_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_t) if (GENAI_AVAILABLE and ai_provider_choice == "Google Gemini") else {"data": img_bytes, "mime_type": mime_t}
                                    raw_txt, used_m = call_gemini_smart(
                                        api_key=gemini_api_key,
                                        contents=[img_part, prompt_vis],
                                        expect_json=True,
                                        preferred_model=selected_gemini_model,
                                        provider=ai_provider_choice
                                    )
                                    parsed_json = clean_json_response(raw_txt)
                                    raw_mon = parsed_json.get("monday_date", "2026-10-19")
                                    clean_mon = normalize_to_monday_str(raw_mon)
                                    extr_sched = parsed_json.get("schedule", [])
                                    if extr_sched:
                                        target_key = f"WEEK_{clean_mon}"
                                        batch_schedules_to_save[target_key] = extr_sched
                                        success_weeks.append(f"{clean_mon} ({img_f.name})")
                                except Exception as e:
                                    failed_files.append(f"{img_f.name}: {e}")

                            prog_bar.progress(1.0, text="Ολοκλήρωση αποθήκευσης στη βάση...")
                            if batch_schedules_to_save:
                                save_multiple_week_schedules_to_db(batch_schedules_to_save, update_default=set_last_as_default)
                                st.success(f"✅ Αποθηκεύτηκαν επιτυχώς **{len(batch_schedules_to_save)} εβδομαδιαία προγράμματα**!")
                                if not failed_files:
                                    refresh_data()
                            if failed_files:
                                for err_msg in failed_files:
                                    st.error(f"Σφάλμα στο αρχείο {err_msg}")

        with col_sh_right:
            with st.container(border=True):
                st.markdown("##### ✏️ Πίνακας Βαρδιών ανά Εβδομάδα & Επεξεργασία")
                sel_wk_label = st.selectbox("Επιλέξτε Εβδομάδα προς Προβολή / Επεξεργασία", options=list(editor_week_map.keys()))
                sel_wk_key = editor_week_map[sel_wk_label]
                mon_str_clean = sel_wk_key.replace("WEEK_", "")

                curr_sched_rows, is_exact_saved, source_used = resolve_week_schedule_smart(mon_str_clean, db_schedules, use_rolling_schedule)
                if sel_wk_key != "WEEK_DEFAULT":
                    if is_exact_saved:
                        st.caption(f"🟢 Προβάλλεται το **ακριβές αποθηκευμένο πρόγραμμα** της εβδομάδας `{mon_str_clean}`.")
                    else:
                        st.caption(f"🟠 Η εβδομάδα `{mon_str_clean}` κληρονομεί αυτόματα από `{source_used}`.")

                display_rows = []
                for r in curr_sched_rows:
                    row_copy = {"staff_name": r.get("staff_name", "")}
                    tot_h = 0.0
                    for d in DAYS_GR:
                        val = r.get(d, "")
                        row_copy[d] = val
                        tot_h += calc_shift_hours(val)
                    row_copy["Σύνολο Ωρών"] = round(tot_h, 1)
                    display_rows.append(row_copy)

                df_sched_ed = pd.DataFrame(display_rows)
                edited_sched_df = st.data_editor(
                    df_sched_ed,
                    hide_index=True,
                    num_rows="dynamic",
                    width="stretch",
                    key=f"ed_grid_{sel_wk_key}",
                    column_config={
                        "staff_name": st.column_config.TextColumn("Υπάλληλος"),
                        "Σύνολο Ωρών": st.column_config.NumberColumn("Ώρες/Εβδ.", format="%.1f h", disabled=True)
                    }
                )

                tech_hours_sum = sum(
                    sum(calc_shift_hours(r.get(d, "")) for d in DAYS_GR)
                    for _, r in edited_sched_df.iterrows()
                    if "δημ" not in str(r.get("staff_name", "")).lower()
                )
                st.caption(f"Σύνολο Εβδομαδιαίων Ωρών Τεχνιτριών: **{tech_hours_sum:.1f} ώρες/εβδ.** (Μισθοδοσία @ 7,50€/h: **{tech_hours_sum * 7.5:,.2f} €**)")

                c_btn1, c_btn2 = st.columns(2)
                with c_btn1:
                    if st.button("💾 Αποθήκευση για αυτή την Εβδομάδα", type="primary", width="stretch"):
                        clean_to_save = edited_sched_df[["staff_name"] + DAYS_GR].to_dict(orient="records")
                        save_multiple_week_schedules_to_db({sel_wk_key: clean_to_save}, update_default=False)
                        st.toast(f"Αποθηκεύτηκε το πρόγραμμα ({sel_wk_key})!", icon=":material/check_circle:")
                        refresh_data()
                with c_btn2:
                    if st.button("📌 Ορισμός και ως Προεπιλεγμένο (WEEK_DEFAULT)", width="stretch"):
                        clean_to_save = edited_sched_df[["staff_name"] + DAYS_GR].to_dict(orient="records")
                        save_multiple_week_schedules_to_db({"WEEK_DEFAULT": clean_to_save}, update_default=False)
                        st.toast("Ορίστηκε ως προεπιλεγμένο πρόγραμμα!", icon=":material/check_circle:")
                        refresh_data()

                with st.expander("📋 Μαζική Εφαρμογή αυτού του Προγράμματος σε Εύρος Εβδομάδων"):
                    c_rng1, c_rng2, c_rng3 = st.columns([1, 1, 1.2], vertical_alignment="bottom")
                    with c_rng1:
                        bulk_start_d = st.date_input("Από Ημερομηνία", value=full_cycle_start_d, key="bulk_sch_start")
                    with c_rng2:
                        bulk_end_d = st.date_input("Έως Ημερομηνία", value=full_cycle_end_d, key="bulk_sch_end")
                    with c_rng3:
                        if st.button("Εφαρμογή σε Όλες τις Εβδομάδες", width="stretch"):
                            clean_to_save = edited_sched_df[["staff_name"] + DAYS_GR].to_dict(orient="records")
                            curr_m = bulk_start_d - timedelta(days=bulk_start_d.weekday())
                            end_m = bulk_end_d - timedelta(days=bulk_end_d.weekday())
                            bulk_map = {}
                            while curr_m <= end_m:
                                bulk_map[f"WEEK_{curr_m.strftime('%Y-%m-%d')}"] = clean_to_save
                                curr_m += timedelta(days=7)
                            if bulk_map:
                                save_multiple_week_schedules_to_db(bulk_map, update_default=False)
                                st.toast(f"Το πρόγραμμα εφαρμόστηκε σε {len(bulk_map)} εβδομάδες!", icon=":material/check_circle:")
                                refresh_data()

    elif admin_section == "📥 2. Εισαγωγή Ραντεβού (Multi-CSV Importer)":
        with st.container(border=True):
            st.subheader("📁 Μαζική Εισαγωγή Ραντεβού από Treatwell CSV", icon=":material/upload_file:")
            st.caption(
                "Ανεβάστε είτε **1 ετήσιο CSV** είτε **πολλά μηνιαία CSVs ταυτόχρονα**. "
                "Το σύστημα τα ενώνει χρονολογικά, αφαιρεί τυχόν διπλοεγγραφές και εντοπίζει αυτόματα την 1η επίσκεψη κάθε πελάτισσας."
            )

            uploaded_files = st.file_uploader(
                "Σύρετε εδώ ένα ή περισσότερα αρχεία CSV από το Treatwell",
                type=["csv"],
                accept_multiple_files=True
            )
            upload_mode = st.radio(
                "Τρόπος Ενημέρωσης Βάσης:",
                options=[
                    "➕ Προσθήκη / Ενημέρωση πάνω στα Υπάρχοντα Ραντεβού (Upsert - Κρατάει τα παλιά και προσθέτει τα νέα)",
                    "🔄 Καθαρή Αντικατάσταση Όλων των Ραντεβού με τα Αρχεία που Ανέβασα Τώρα"
                ],
                index=0
            )

            if uploaded_files:
                dfs_list = []
                for idx_uf, uf in enumerate(uploaded_files):
                    try:
                        tmp_df = pd.read_csv(uf)
                        tmp_df["_src_file"] = str(idx_uf)
                        dfs_list.append(tmp_df)
                    except Exception as e:
                        st.error(f"Σφάλμα ανάγνωσης στο αρχείο {uf.name}: {e}")

                if dfs_list:
                    df_combined_raw = pd.concat(dfs_list, ignore_index=True)
                    st.success(f"Εντοπίστηκαν συνολικά **{len(df_combined_raw):,} εγγραφές** από **{len(dfs_list)} αρχεία CSV**.")
                    if st.button("🚀 Επεξεργασία & Αποθήκευση Όλων των Ραντεβού στη Βάση", type="primary", width="stretch"):
                        if df_services_db.empty:
                            seed_initial_catalog()
                            df_staff_db, df_services_db, df_expenses_db, df_bookings_db, db_schedules, db_app_settings = load_all_db_data()

                        with st.spinner("Χρονολογική ένωση, ταυτοποίηση πελατών και αποθήκευση..."):
                            existing_for_first_visit = df_bookings_db if upload_mode.startswith("➕") else pd.DataFrame()
                            records_to_save = process_and_calculate_bookings(
                                df_combined_raw, df_services_db, df_staff_db, existing_for_first_visit
                            )
                            if upload_mode.startswith("🔄"):
                                supabase.table("bookings_master").delete().neq("unique_id", "___KEEP_NONE___").execute()
                            for i in range(0, len(records_to_save), 200):
                                supabase.table("bookings_master").upsert(records_to_save[i:i+200]).execute()
                        st.toast(f"Αποθηκεύτηκαν επιτυχώς {len(records_to_save):,} μοναδικά ραντεβού!", icon=":material/check_circle:")
                        refresh_data()

    else:
        col_c1, col_c2 = st.columns(2, gap="medium")
        with col_c1:
            with st.container(border=True):
                st.markdown("##### 👩‍🔧 Ωρομίσθια & Προσωπικό (`config_staff`)")
                ed_staff = st.data_editor(
                    df_staff_db,
                    num_rows="dynamic",
                    hide_index=True,
                    width="stretch",
                    key="ed_staff",
                    column_config={
                        "staff_name": st.column_config.TextColumn("Όνομα"),
                        "hourly_cost": st.column_config.NumberColumn("Ωρομίσθιο (€/h)", format="%.2f €"),
                        "weekly_hours_declared": st.column_config.NumberColumn("Τυπικές Ώρες/Εβδ.", format="%.1f h")
                    }
                )
                if st.button("💾 Αποθήκευση Προσωπικού", type="primary", width="stretch"):
                    supabase.table("config_staff").upsert(ed_staff.to_dict(orient="records")).execute()
                    st.toast("Αποθηκεύτηκε το προσωπικό!", icon=":material/check_circle:")
                    refresh_data()

        with col_c2:
            with st.container(border=True):
                st.markdown("##### 🏢 Μηνιαία Πάγια Έξοδα (`expenses_log`)")
                ed_exp = st.data_editor(
                    df_expenses_db,
                    num_rows="dynamic",
                    hide_index=True,
                    width="stretch",
                    key="ed_exp",
                    column_config={
                        "category": st.column_config.TextColumn("Κατηγορία Εξόδου"),
                        "amount": st.column_config.NumberColumn("Μηνιαίο Ποσό (€)", format="%.2f €")
                    }
                )
                if st.button("💾 Αποθήκευση Παγίων", type="primary", width="stretch"):
                    supabase.table("expenses_log").delete().neq("expense_month", "0000").execute()
                    supabase.table("expenses_log").insert(ed_exp.to_dict(orient="records")).execute()
                    st.toast("Αποθηκεύτηκαν τα πάγια έξοδα!", icon=":material/check_circle:")
                    refresh_data()

        st.space("small")
        with st.container(border=True):
            st.markdown("##### 💅 Κοστολόγηση Υπηρεσιών & Προϊόντων Λιανικής (`config_services`)")
            st.caption("Επεξεργαστείτε τη διάρκεια (σε λεπτά), την τιμή καταλόγου και το εκτιμώμενο κόστος αναλωσίμων υλικών ανά υπηρεσία:")
            ed_srv = st.data_editor(
                df_services_db,
                num_rows="dynamic",
                hide_index=True,
                width="stretch",
                key="ed_services_cat",
                column_config={
                    "service_name": st.column_config.TextColumn("Υπηρεσία / Προϊόν"),
                    "category": st.column_config.TextColumn("Κατηγορία"),
                    "base_duration": st.column_config.NumberColumn("Διάρκεια (λεπτά)", format="%d'"),
                    "gross_price": st.column_config.NumberColumn("Τιμή Καταλόγου (€)", format="%.2f €"),
                    "material_cost_estimate": st.column_config.NumberColumn("Κόστος Υλικών (€)", format="%.2f €"),
                    "price_variants_json": st.column_config.TextColumn("Παραλλαγές (JSON)")
                }
            )
            if st.button("💾 Αποθήκευση Καταλόγου Υπηρεσιών & Υλικών", type="primary", width="stretch"):
                supabase.table("config_services").upsert(ed_srv.to_dict(orient="records")).execute()
                st.toast("Αποθηκεύτηκε ο κατάλογος υπηρεσιών!", icon=":material/check_circle:")
                refresh_data()