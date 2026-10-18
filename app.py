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
import io
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

DEFAULT_SCREENSHOT_SCHEDULE = []

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
    try:
        payroll = pd.DataFrame(supabase.table("payroll_ledger").select("*").order("id").execute().data)
    except Exception:
        payroll = pd.DataFrame()
    try:
        expenses_ledger = pd.DataFrame(supabase.table("expenses_ledger").select("*").order("expense_date", desc=True).execute().data)
    except Exception:
        expenses_ledger = pd.DataFrame()

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

    return staff, services, expenses, bookings, schedules_dict, app_settings_dict, payroll, expenses_ledger

df_staff_db, df_services_db, df_expenses_db, df_bookings_db, db_schedules, db_app_settings, df_payroll_db, df_expenses_ledger_db = load_all_db_data()

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
        "gemini-3.8-flash",
        "gemini-3.1-pro-preview",
        "gemini-4-argon",
        "gemini-pro-latest",
        "gemini-flash-latest",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-pro"
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

def call_gemini_smart(api_key, contents, expect_json=False, preferred_model="gemini-3.8-flash", provider="auto", thinking_level="HIGH"):
    """
    Κεντρικός δρομολογητής AI CFO:
    Υποστηρίζει Google Gemini (gemini-3.8-flash με thinking=HIGH, Pro & Flash), OpenAI ChatGPT και Anthropic Claude.
    Προεπιλογή: gemini-3.8-flash με thinking=HIGH για μέγιστη ευφυΐα και ελάχιστο κόστος.
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
                if m_name.startswith(("o1", "o3", "o4", "gpt-5")):
                    if thinking_level and str(thinking_level).upper() != "OFF":
                        payload["reasoning_effort"] = str(thinking_level).lower() if str(thinking_level).lower() in ("high", "medium", "low") else "high"

                if expect_json and not img_bytes and not m_name.startswith(("o1", "o3")):
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

    # 3. Google Gemini (gemini-3.8-flash, Thinking=HIGH, με έξυπνο fallback αν υπάρξει 404 ή Quota 429)
    client = genai.Client(api_key=api_key)
    candidate_models = [
        preferred_model,
        "gemini-3.8-flash",
        "gemini-3.1-pro-preview",
        "gemini-4-argon",
        "gemini-pro-latest",
        "gemini-flash-latest",
        "gemini-3-flash-preview",
        "gemini-2.5-flash",
        "gemini-2.5-pro"
    ]
    seen = set()
    ordered_models = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

    def _build_gemini_config(with_thinking=True):
        if not GENAI_AVAILABLE:
            return None
        kwargs = {}
        if expect_json:
            kwargs["response_mime_type"] = "application/json"
        if with_thinking and thinking_level and str(thinking_level).upper() != "OFF":
            t_str = str(thinking_level).upper()
            t_enum = getattr(types.ThinkingLevel, t_str, types.ThinkingLevel.HIGH)
            try:
                kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=t_enum)
            except Exception:
                try:
                    kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)
                except Exception:
                    pass
        return types.GenerateContentConfig(**kwargs) if kwargs else None

    last_err = None

    for m_name in ordered_models:
        try:
            cfg = _build_gemini_config(with_thinking=True)
            resp = client.models.generate_content(model=m_name, contents=contents, config=cfg)
            return resp.text, m_name
        except Exception as e:
            err_str = str(e)
            if "thinking" in err_str.lower() or "thought" in err_str.lower():
                try:
                    cfg_no_th = _build_gemini_config(with_thinking=False)
                    resp = client.models.generate_content(model=m_name, contents=contents, config=cfg_no_th)
                    return resp.text, m_name
                except Exception as e2:
                    e = e2
                    err_str = str(e2)
            last_err = e
            if any(tok in err_str for tok in ("404", "NOT_FOUND", "no longer available", "429", "RESOURCE_EXHAUSTED", "quota")):
                continue
            else:
                raise e

    try:
        cfg_fallback = _build_gemini_config(with_thinking=False)
        for m in client.models.list():
            m_id = getattr(m, "name", "").replace("models/", "")
            if "gemini" in m_id and ("flash" in m_id or "pro" in m_id) and "embedding" not in m_id:
                try:
                    resp = client.models.generate_content(model=m_id, contents=contents, config=cfg_fallback)
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

# --- 4Β. ΜΗΤΡΩΟ ΜΙΣΘΟΔΟΣΙΑΣ & ΚΟΣΤΟΛΟΓΗΣΗΣ ΠΡΟΣΩΠΙΚΟΥ (100% ΔΥΝΑΜΙΚΑ ΑΠΟ ΤΗ ΒΑΣΗ SUPABASE) ---
DEFAULT_PAYROLL_COLUMNS = [
    "staff_name", "payroll_code", "role_type", "is_active", "weekly_hours_declared",
    "nominal_gross", "monthly_net_pocket", "monthly_efka_tax", "monthly_employer_regular",
    "monthly_employer_full_14", "hourly_cost", "cost_2024", "net_2024",
    "cost_2025", "net_2025", "cost_2026_jan_aug", "net_2026_jan_aug",
    "cost_2026_sep", "net_2026_sep", "notes"
]

def get_payroll_profiles_df() -> pd.DataFrame:
    """Φορτώνει το πλήρες μητρώο μισθοδοσίας αποκλειστικά από τον πίνακα payroll_ledger της βάσης Supabase."""
    try:
        res = supabase.table("payroll_ledger").select("*").order("id").execute()
        if res.data and len(res.data) > 0:
            df_p = pd.DataFrame(res.data)
            for c in DEFAULT_PAYROLL_COLUMNS:
                if c not in df_p.columns:
                    df_p[c] = 0.0 if any(k in c for k in ["cost", "net", "nominal", "monthly", "hourly", "hours"]) else ""
            return df_p
    except Exception:
        pass

    # Δυναμικό fallback από τον πίνακα config_staff (χωρίς κανένα hardcoded όνομα στον κώδικα)
    try:
        if not df_staff_db.empty:
            rows = []
            for _, srow in df_staff_db.iterrows():
                sname = str(srow.get("staff_name", "")).strip()
                if not sname:
                    continue
                h_cost = float(srow.get("hourly_cost", 0.0) or 0.0)
                w_hrs = float(srow.get("weekly_hours_declared", 40.0) or 40.0)
                rows.append({
                    "staff_name": sname, "payroll_code": "", "role_type": "Τεχνίτρια",
                    "is_active": True, "weekly_hours_declared": w_hrs, "nominal_gross": 0.0,
                    "monthly_net_pocket": 0.0, "monthly_efka_tax": 0.0, "monthly_employer_regular": 0.0,
                    "monthly_employer_full_14": 0.0, "hourly_cost": h_cost, "cost_2024": 0.0,
                    "net_2024": 0.0, "cost_2025": 0.0, "net_2025": 0.0, "cost_2026_jan_aug": 0.0,
                    "net_2026_jan_aug": 0.0, "cost_2026_sep": 0.0, "net_2026_sep": 0.0, "notes": ""
                })
            if rows:
                return pd.DataFrame(rows)
    except Exception:
        pass

    return pd.DataFrame(columns=DEFAULT_PAYROLL_COLUMNS)

def save_payroll_profiles_df(df_ed: pd.DataFrame, auto_recalc_rates: bool = True):
    """Αποθηκεύει το μητρώο μισθοδοσίας αποκλειστικά στον πίνακα payroll_ledger της βάσης Supabase."""
    records = []
    staff_table_rows = []
    for _, r in df_ed.iterrows():
        s_name = str(r.get("staff_name", "")).strip()
        if not s_name:
            continue
        role_t = str(r.get("role_type", "Τεχνίτρια")).strip()
        wk_h = float(r.get("weekly_hours_declared", 0.0) or 0.0)
        net_p = round(float(r.get("monthly_net_pocket", 0.0) or 0.0), 2)
        efka_t = round(float(r.get("monthly_efka_tax", 0.0) or 0.0), 2)
        reg_emp = round(float(r.get("monthly_employer_regular", 0.0) or 0.0), 2)
        full_14 = round(float(r.get("monthly_employer_full_14", 0.0) or 0.0), 2)
        h_cost = round(float(r.get("hourly_cost", 0.0) or 0.0), 2)

        if auto_recalc_rates and role_t == "Τεχνίτρια":
            if net_p > 0 or efka_t > 0:
                reg_emp = round(net_p + efka_t, 2)
            if reg_emp > 0:
                full_14 = round(reg_emp * (14.0 / 12.0), 2)
                monthly_hours = wk_h * (52.0 / 12.0) if wk_h > 0 else 166.67
                h_cost = round(full_14 / monthly_hours, 2)
        elif auto_recalc_rates and role_t == "Άδεια / Υποστήριξη":
            if net_p > 0 or efka_t > 0:
                reg_emp = round(net_p + efka_t, 2)
            if reg_emp > 0:
                full_14 = round(reg_emp * (14.0 / 12.0), 2)

        rec = {
            "staff_name": s_name,
            "payroll_code": str(r.get("payroll_code", "") or ""),
            "role_type": role_t,
            "is_active": bool(r.get("is_active", True)),
            "weekly_hours_declared": wk_h,
            "nominal_gross": round(float(r.get("nominal_gross", 0.0) or 0.0), 2),
            "monthly_net_pocket": net_p,
            "monthly_efka_tax": efka_t,
            "monthly_employer_regular": reg_emp,
            "monthly_employer_full_14": full_14,
            "hourly_cost": h_cost,
            "cost_2024": round(float(r.get("cost_2024", 0.0) or 0.0), 2),
            "net_2024": round(float(r.get("net_2024", 0.0) or 0.0), 2),
            "cost_2025": round(float(r.get("cost_2025", 0.0) or 0.0), 2),
            "net_2025": round(float(r.get("net_2025", 0.0) or 0.0), 2),
            "cost_2026_jan_aug": round(float(r.get("cost_2026_jan_aug", 0.0) or 0.0), 2),
            "net_2026_jan_aug": round(float(r.get("net_2026_jan_aug", 0.0) or 0.0), 2),
            "cost_2026_sep": round(float(r.get("cost_2026_sep", 0.0) or 0.0), 2),
            "net_2026_sep": round(float(r.get("net_2026_sep", 0.0) or 0.0), 2),
            "notes": str(r.get("notes", "") or "")
        }
        records.append(rec)
        if role_t in ("Τεχνίτρια", "Ιδιοκτήτης / Διοίκηση"):
            staff_table_rows.append({
                "staff_name": s_name,
                "hourly_cost": h_cost if role_t == "Τεχνίτρια" else 0.0,
                "weekly_hours_declared": wk_h
            })

    try:
        supabase.table("payroll_ledger").upsert(records, on_conflict="staff_name").execute()
    except Exception as e:
        st.error(f"Σφάλμα αποθήκευσης payroll_ledger στο Supabase: {e}")

    if staff_table_rows:
        try:
            supabase.table("config_staff").upsert(staff_table_rows, on_conflict="staff_name").execute()
        except Exception:
            pass
    return records


# --- 4Γ. ΟΛΟΚΛΗΡΩΜΕΝΗ ΥΠΟΔΟΜΗ ΕΞΟΔΩΝ, ΑΓΟΡΩΝ & ΠΑΡΑΣΤΑΤΙΚΩΝ (100% ΔΥΝΑΜΙΚΑ ΑΠΟ ΤΗ ΒΑΣΗ SUPABASE) ---
EXPENSE_MAIN_CATEGORIES = [
    "Κόστος Υλικών & Εμπορευμάτων (COGS)",
    "Έξοδα Στέγασης & Λειτουργίας (Ενοίκιο, Κοινόχρηστα)",
    "Λογαριασμοί Κοινής Ωφελείας (Ρεύμα, Νερό, Internet)",
    "Marketing, Προβολή & Διαφήμιση",
    "Τεχνολογία, Λογισμικά & Προμήθειες POS",
    "Επαγγελματικές Αμοιβές & Άδειες (Λογιστής, Νομικά)",
    "Συντήρηση, Εξοπλισμός & Έκτακτα (CapEx)",
    "Λοιπά Λειτουργικά Έξοδα"
]

EXPENSE_NATURES = [
    "Πάγιο Επαναλαμβανόμενο",
    "Μεταβλητό Λειτουργικό",
    "Αγορά Υλικών/Stock (COGS)",
    "Έκτακτο / Επένδυση (CapEx)"
]

EXPENSE_PAYMENT_METHODS = [
    "Επαγγελματική Κάρτα",
    "Τραπεζική Μεταφορά (e-Banking)",
    "Μετρητά Ταμείου",
    "Παρακράτηση POS / Άλλο"
]

EXPENSE_VAT_RATES = [24.0, 13.0, 6.0, 0.0]

DEFAULT_EXPENSE_COLUMNS = [
    "id", "expense_date", "vendor_name", "main_category", "sub_category",
    "expense_nature", "net_amount", "vat_rate", "vat_amount", "gross_amount",
    "payment_method", "invoice_number", "notes"
]

def get_expenses_ledger_df() -> pd.DataFrame:
    """Φορτώνει το ημερολόγιο εξόδων απευθείας από τον πίνακα expenses_ledger του Supabase."""
    try:
        res = supabase.table("expenses_ledger").select("*").order("expense_date", desc=True).execute()
        if res.data and len(res.data) > 0:
            df = pd.DataFrame(res.data)
            df["expense_date"] = pd.to_datetime(df["expense_date"]).dt.date
            for c in ["net_amount", "vat_rate", "vat_amount", "gross_amount"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).round(2)
            return df
    except Exception:
        pass
    return pd.DataFrame(columns=DEFAULT_EXPENSE_COLUMNS)

def save_expense_entry(rec: dict):
    """Αποθηκεύει μία νέα ή ενημερωμένη εγγραφή εξόδου στο Supabase."""
    net = float(rec.get("net_amount", 0.0) or 0.0)
    vat_r = float(rec.get("vat_rate", 24.0) or 24.0)
    gross = float(rec.get("gross_amount", 0.0) or 0.0)
    if gross > 0 and net == 0:
        net = round(gross / (1.0 + vat_r / 100.0), 2)
        vat_amt = round(gross - net, 2)
    else:
        vat_amt = round(net * (vat_r / 100.0), 2)
        gross = round(net + vat_amt, 2)

    clean_rec = {
        "expense_date": str(rec.get("expense_date")),
        "vendor_name": str(rec.get("vendor_name", "")).strip(),
        "main_category": str(rec.get("main_category", EXPENSE_MAIN_CATEGORIES[0])),
        "sub_category": str(rec.get("sub_category", "") or ""),
        "expense_nature": str(rec.get("expense_nature", EXPENSE_NATURES[0])),
        "net_amount": round(net, 2),
        "vat_rate": round(vat_r, 1),
        "vat_amount": round(vat_amt, 2),
        "gross_amount": round(gross, 2),
        "payment_method": str(rec.get("payment_method", EXPENSE_PAYMENT_METHODS[0])),
        "invoice_number": str(rec.get("invoice_number", "") or ""),
        "notes": str(rec.get("notes", "") or "")
    }
    if rec.get("id") and not pd.isna(rec.get("id")):
        clean_rec["id"] = int(rec["id"])
    supabase.table("expenses_ledger").upsert([clean_rec]).execute()

def delete_expense_entry(expense_id: int):
    """Διαγράφει μία εγγραφή εξόδου από το Supabase."""
    try:
        supabase.table("expenses_ledger").delete().eq("id", expense_id).execute()
    except Exception:
        pass

def save_expenses_ledger_df(df_edited: pd.DataFrame) -> int:
    """
    Συγχρονίζει ολόκληρο το επεξεργασμένο DataFrame εξόδων με το Supabase:
    - Υπολογίζει αυτόματα ΦΠΑ και Μικτή Αξία αν λείπουν
    - Κάνει upsert στις νέες και τροποποιημένες γραμμές
    - Διαγράφει από τη βάση όσες γραμμές αφαιρέθηκαν από τον πίνακα
    """
    if df_edited is None or df_edited.empty:
        return 0
    try:
        curr_res = supabase.table("expenses_ledger").select("id").execute()
        existing_ids = set(r["id"] for r in (curr_res.data or []))
    except Exception:
        existing_ids = set()

    clean_records = []
    kept_ids = set()
    for _, row in df_edited.iterrows():
        exp_d = row.get("expense_date")
        if pd.isna(exp_d) or not exp_d:
            continue
        try:
            exp_d_str = pd.to_datetime(exp_d).strftime("%Y-%m-%d")
        except Exception:
            continue

        net = float(pd.to_numeric(row.get("net_amount", 0.0), errors="coerce") or 0.0)
        vat_r = float(pd.to_numeric(row.get("vat_rate", 24.0), errors="coerce") or 24.0)
        gross = float(pd.to_numeric(row.get("gross_amount", 0.0), errors="coerce") or 0.0)
        if gross > 0 and net == 0:
            net = round(gross / (1.0 + vat_r / 100.0), 2)
            vat_amt = round(gross - net, 2)
        else:
            vat_amt = round(net * (vat_r / 100.0), 2)
            gross = round(net + vat_amt, 2)

        main_cat = str(row.get("main_category", EXPENSE_MAIN_CATEGORIES[0]) or EXPENSE_MAIN_CATEGORIES[0]).strip()
        if main_cat not in EXPENSE_MAIN_CATEGORIES:
            main_cat = EXPENSE_MAIN_CATEGORIES[0]

        nature = str(row.get("expense_nature", EXPENSE_NATURES[0]) or EXPENSE_NATURES[0]).strip()
        if nature not in EXPENSE_NATURES:
            nature = EXPENSE_NATURES[0]

        pay_m = str(row.get("payment_method", EXPENSE_PAYMENT_METHODS[0]) or EXPENSE_PAYMENT_METHODS[0]).strip()
        if pay_m not in EXPENSE_PAYMENT_METHODS:
            pay_m = EXPENSE_PAYMENT_METHODS[0]

        rec = {
            "expense_date": exp_d_str,
            "vendor_name": str(row.get("vendor_name", "") or "").strip(),
            "main_category": main_cat,
            "sub_category": str(row.get("sub_category", "") or "").strip(),
            "expense_nature": nature,
            "net_amount": round(net, 2),
            "vat_rate": round(vat_r, 1),
            "vat_amount": round(vat_amt, 2),
            "gross_amount": round(gross, 2),
            "payment_method": pay_m,
            "invoice_number": str(row.get("invoice_number", "") or "").strip(),
            "notes": str(row.get("notes", "") or "").strip()
        }
        r_id = row.get("id")
        if pd.notna(r_id) and str(r_id).strip() != "" and int(pd.to_numeric(r_id, errors="coerce") or 0) > 0:
            rec_id = int(r_id)
            rec["id"] = rec_id
            kept_ids.add(rec_id)
        clean_records.append(rec)

    # Διαγραφή γραμμών που αφαιρέθηκαν
    deleted_ids = existing_ids - kept_ids
    for d_id in deleted_ids:
        try:
            supabase.table("expenses_ledger").delete().eq("id", d_id).execute()
        except Exception:
            pass

    saved_count = 0
    for i in range(0, len(clean_records), 100):
        chunk = clean_records[i:i+100]
        if chunk:
            supabase.table("expenses_ledger").upsert(chunk).execute()
            saved_count += len(chunk)
    return saved_count

def parse_expenses_import_file(uploaded_file) -> pd.DataFrame:
    """
    Διαβάζει αρχείο CSV ή Excel με έξοδα/τιμολόγια, αναγνωρίζει αυτόματα τις στήλες
    (σε Ελληνικά & Αγγλικά) και επιστρέφει ένα καθαρό DataFrame έτοιμο για εισαγωγή.
    """
    fname = uploaded_file.name.lower()
    if fname.endswith(".csv"):
        content = uploaded_file.getvalue()
        df_raw = None
        for enc in ["utf-8-sig", "utf-8", "windows-1253", "iso-8859-7", "cp1252"]:
            try:
                df_raw = pd.read_csv(io.BytesIO(content), encoding=enc)
                break
            except Exception:
                continue
        if df_raw is None:
            raise ValueError("Αδυναμία ανάγνωσης του CSV. Ελέγξτε τη μορφή ή την κωδικοποίηση.")
    else:
        df_raw = pd.read_excel(uploaded_file)

    if df_raw is None or df_raw.empty:
        return pd.DataFrame(columns=DEFAULT_EXPENSE_COLUMNS)

    col_map = {}
    for col in df_raw.columns:
        c_clean = str(col).strip().lower()
        if any(k in c_clean for k in ["ημερομην", "date", "ημ/νια", "ημερα", "expense_date"]):
            col_map[col] = "expense_date"
        elif any(k in c_clean for k in ["προμηθευτ", "vendor", "supplier", "εκδοτ", "επωνυμ"]):
            col_map[col] = "vendor_name"
        elif any(k in c_clean for k in ["βασικη κατηγορια", "main_cat", "κατηγορια", "category"]):
            col_map[col] = "main_category"
        elif any(k in c_clean for k in ["υποκατηγορ", "sub_cat", "υπο-κατηγορ", "ειδος", "περιγραφ"]):
            col_map[col] = "sub_category"
        elif any(k in c_clean for k in ["φυση", "nature"]):
            col_map[col] = "expense_nature"
        elif any(k in c_clean for k in ["καθαρη αξια", "καθαρο", "net_amount", "net", "καθαρα"]):
            col_map[col] = "net_amount"
        elif any(k in c_clean for k in ["συντελεστης φπα", "φπα %", "vat_rate", "vat %"]):
            col_map[col] = "vat_rate"
        elif any(k in c_clean for k in ["ποσο φπα", "αξια φπα", "vat_amount", "φπα", "vat"]):
            col_map[col] = "vat_amount"
        elif any(k in c_clean for k in ["συνολο", "μικτο", "gross_amount", "gross", "πληρωτεο", "τελικη αξια"]):
            col_map[col] = "gross_amount"
        elif any(k in c_clean for k in ["τροπος πληρωμης", "payment_method", "πληρωμη", "μεθοδος"]):
            col_map[col] = "payment_method"
        elif any(k in c_clean for k in ["παραστατικ", "τιμολογι", "αρ.", "invoice", "invoice_number"]):
            col_map[col] = "invoice_number"
        elif any(k in c_clean for k in ["σημειωσ", "αιτιολογ", "notes", "σχολια"]):
            col_map[col] = "notes"

    df_norm = df_raw.rename(columns=col_map)
    out_rows = []

    for _, r in df_norm.iterrows():
        raw_d = r.get("expense_date")
        if pd.isna(raw_d) or not raw_d:
            continue
        try:
            d_val = pd.to_datetime(raw_d, dayfirst=True).date()
        except Exception:
            continue

        net = float(pd.to_numeric(r.get("net_amount", 0.0), errors="coerce") or 0.0)
        gross = float(pd.to_numeric(r.get("gross_amount", 0.0), errors="coerce") or 0.0)
        vat_r = float(pd.to_numeric(r.get("vat_rate", 24.0), errors="coerce") or 24.0)
        vat_amt = float(pd.to_numeric(r.get("vat_amount", 0.0), errors="coerce") or 0.0)

        if gross > 0 and net == 0:
            net = round(gross / (1.0 + vat_r / 100.0), 2)
            vat_amt = round(gross - net, 2)
        elif net > 0 and gross == 0:
            if vat_amt == 0:
                vat_amt = round(net * (vat_r / 100.0), 2)
            gross = round(net + vat_amt, 2)
        elif gross == 0 and net == 0 and vat_amt > 0:
            gross = vat_amt
            net = round(gross / (1.0 + vat_r / 100.0), 2)

        main_cat = str(r.get("main_category", "") or "").strip()
        if main_cat not in EXPENSE_MAIN_CATEGORIES:
            mc_lower = main_cat.lower()
            if any(k in mc_lower for k in ["υλικ", "αναλωσιμ", "προιον", "βερνικ", "τζελ", "nail", "cogs"]):
                main_cat = "Κόστος Υλικών & Εμπορευμάτων (COGS)"
            elif any(k in mc_lower for k in ["ενοικι", "δεη", "ρευμα", "νερο", "ευδαπ", "τηλεφων", "ιντερνετ", "κοινοχρηστ", "καθαρισμ"]):
                main_cat = "Έξοδα Στέγασης & Λειτουργίας Καταστήματος"
            elif any(k in mc_lower for k in ["λογιστ", "δικηγορ", "τεχνικ", "συντηρησ", "pos", "τραπεζ"]):
                main_cat = "Επαγγελματικές Υπηρεσίες & Συντηρήσεις"
            elif any(k in mc_lower for k in ["διαφημισ", "marketing", "facebook", "instagram", "google", "treatwell"]):
                main_cat = "Marketing, Διαφήμιση & Πλατφόρμες"
            elif any(k in mc_lower for k in ["φορ", "τελη", "δημοτικ", "επιμελητηρ"]):
                main_cat = "Φόροι, Τέλη & Χρηματοοικονομικά"
            else:
                main_cat = "Λοιπά Γενικά Έξοδα"

        nature = str(r.get("expense_nature", "Σταθερό") or "Σταθερό").strip()
        if nature not in EXPENSE_NATURES:
            nature = "Μεταβλητό" if main_cat == "Κόστος Υλικών & Εμπορευμάτων (COGS)" else "Σταθερό"

        pay_m = str(r.get("payment_method", "Τράπεζα / Έμβασμα") or "Τράπεζα / Έμβασμα").strip()
        if pay_m not in EXPENSE_PAYMENT_METHODS:
            pay_m = "Τράπεζα / Έμβασμα"

        out_rows.append({
            "expense_date": d_val,
            "vendor_name": str(r.get("vendor_name", "") or "").strip(),
            "main_category": main_cat,
            "sub_category": str(r.get("sub_category", "") or "").strip(),
            "expense_nature": nature,
            "net_amount": round(net, 2),
            "vat_rate": round(vat_r, 1),
            "vat_amount": round(vat_amt, 2),
            "gross_amount": round(gross, 2),
            "payment_method": pay_m,
            "invoice_number": str(r.get("invoice_number", "") or "").strip(),
            "notes": str(r.get("notes", "") or "").strip()
        })

    return pd.DataFrame(out_rows)


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

    df_payroll_profiles = get_payroll_profiles_df()

    with st.container(border=True):
        st.markdown("**Μισθοδοσία & Κανόνες Βαρδιών**")
        payroll_calc_mode = st.radio(
            "Μέθοδος Υπολογισμού Μισθοδοσίας στο P&L",
            options=[
                "🏛️ Πραγματική Λογιστική Μισθοδοσία (Εκκαθαριστικά ΕΦΚΑ/ΦΜΥ/Δώρα)",
                "⏱️ Ωρομίσθιο Βάρδιας (Ώρες Βάρδιας × Ωριαίο Κόστος)"
            ],
            index=0,
            help="Η 'Πραγματική Λογιστική Μισθοδοσία' χρησιμοποιεί τα ακριβή ποσά από τις μισθοδοτικές καταστάσεις του λογιστή (2024, 2025, 2026) και τα τρέχοντα μηνιαία κόστη εργοδότη."
        )
        use_accounting_payroll = payroll_calc_mode.startswith("🏛️")
        include_payroll_gifts = st.checkbox(
            "Συμπερίληψη Δώρων, Επιδομάτων (14 μισθοί) & Εκκαθαρίσεων",
            value=True,
            help="Όταν είναι ενεργό, περιλαμβάνει Δώρο Χριστουγέννων, Δώρο Πάσχα, Επίδομα Αδείας και τυχόν εκκαθαρίσεις αποχωρήσεων."
        )
        include_support_staff = st.checkbox(
            "Συμπερίληψη Προσωπικού Άδειας / Καθαριότητας / Υποδοχής / ΕΠΑΣ",
            value=True,
            help="Συμπεριλαμβάνει στη συνολική μισθοδοσία το προσωπικό υπεύθυνης άδειας λειτουργίας, καθαριότητας, υποδοχής και πρακτικής άσκησης."
        )
        use_rolling_schedule = st.checkbox(
            "Κληρονόμηση προηγούμενου προγράμματος (Rolling Schedule)",
            value=True,
            help="Αν μια εβδομάδα δεν έχει δικό της αποθηκευμένο πρόγραμμα, χρησιμοποιεί το πιο πρόσφατο προηγούμενο."
        )
        auto_skip_zero_weeks = st.checkbox(
            "Εξαίρεση εβδομάδων με 0 ραντεβού ανά τεχνίτρια (Άδεια / Πριν την πρόσληψη)",
            value=True,
            help="Αν μια τεχνίτρια έχει 0 ραντεβού σε ολόκληρη την εβδομάδα (π.χ. πριν προσληφθεί ή σε ετήσια άδεια), δεν χρεώνεται βάρδια γι' αυτή την εβδομάδα."
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

        base_models = DEFAULT_MODELS_BY_PROVIDER.get(ai_provider_choice, ["gemini-3.8-flash"])
        live_models = st.session_state["live_models_by_prov"].get(ai_provider_choice, [])
        merged_models = []
        for m_item in (base_models + live_models):
            if m_item not in merged_models:
                merged_models.append(m_item)
        CUSTOM_MODEL_OPT = "✍️ Άλλο / Χειροκίνητο Όνομα Μοντέλου..."
        merged_models.append(CUSTOM_MODEL_OPT)

        chosen_model_opt = st.selectbox(
            "Μοντέλο AI (Προεπιλογή: Gemini 3.8 Flash • High Thinking)",
            options=merged_models,
            index=0,
            help="Προεπιλεγμένο μοντέλο: gemini-3.8-flash (ταχύτατο, οικονομικό και κορυφαίο σε συλλογισμό). Μπορείτε να επιλέξετε Pro/Argon ή οποιοδήποτε άλλο μοντέλο."
        )
        if chosen_model_opt == CUSTOM_MODEL_OPT:
            custom_m_typed = st.text_input(
                "Πληκτρολογήστε το ακριβές ID μοντέλου",
                value=base_models[0],
                placeholder="π.χ. gemini-3.8-flash, o3, gpt-5, claude-sonnet-4-6"
            )
            selected_gemini_model = custom_m_typed.strip() or base_models[0]
        else:
            selected_gemini_model = chosen_model_opt

        sel_thinking_opt = st.selectbox(
            "🧠 Επίπεδο Συλλογισμού AI (Thinking)",
            options=["HIGH (Υψηλό - Μέγιστη Εμβάθυνση)", "MEDIUM (Ισορροπημένο)", "LOW (Γρήγορο)", "OFF (Ανενεργό)"],
            index=0,
            help="Ελέγχει το βάθος συλλογισμού (extended thinking / reasoning tokens) του Gemini 3.8 Flash. Προεπιλογή: HIGH."
        )
        selected_thinking_level = "HIGH"
        if "MEDIUM" in sel_thinking_opt:
            selected_thinking_level = "MEDIUM"
        elif "LOW" in sel_thinking_opt:
            selected_thinking_level = "LOW"
        elif "OFF" in sel_thinking_opt:
            selected_thinking_level = "OFF"

    if st.button("Ανανέωση Δεδομένων Βάσης", icon=":material/refresh:", width="stretch"):
        refresh_data()

    if app_password_secret:
        if st.button("Κλείδωμα Εφαρμογής (Logout)", icon=":material/lock:", width="stretch"):
            st.session_state["app_authenticated"] = False
            st.rerun()

# --- 7. ΚΑΤΑΛΟΓΟΣ & ΕΠΕΞΕΡΓΑΣΙΑ ΠΟΛΛΑΠΛΩΝ CSV ---
def seed_initial_catalog():
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
        {"service_name": "WEEK_DEFAULT", "category": "__SHIFT_SCHEDULE__", "base_duration": 0, "gross_price": 0.0, "material_cost_estimate": 0.0, "price_variants_json": "[]"}
    ]
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

# --- 9. ΚΕΝΤΡΙΚΗ ΜΗΧΑΝΗ ΥΠΟΛΟΓΙΣΜΟΥ P&L, ΜΙΣΘΟΔΟΣΙΑΣ, ΒΑΡΔΙΩΝ & ABC ΚΟΣΤΟΛΟΓΗΣΗΣ ---
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

    # Ενοποιημένο Μητρώο Προσωπικού (Συνδυασμός df_payroll_profiles + df_staff_db)
    payroll_prof_map = {str(r["staff_name"]).strip().lower(): r.to_dict() for _, r in df_payroll_profiles.iterrows()}
    roster_rows = []
    seen_roster = set()
    for _, r in df_payroll_profiles.iterrows():
        s_n = str(r["staff_name"]).strip()
        if r["role_type"] in ("Τεχνίτρια", "Ιδιοκτήτης / Διοίκηση"):
            seen_roster.add(s_n.lower())
            base_hc = float(r.get("hourly_cost", 0.0) or 0.0)
            eff_hc = base_hc if include_payroll_gifts else round(base_hc * (12.0 / 14.0), 2)
            roster_rows.append({
                "staff_name": s_n,
                "hourly_cost": eff_hc if r["role_type"] == "Τεχνίτρια" else 0.0,
                "weekly_hours_declared": float(r.get("weekly_hours_declared", 0.0) or 0.0),
                "role_type": r["role_type"]
            })
    for _, r in df_staff_db.iterrows():
        s_n = str(r["staff_name"]).strip()
        if s_n.lower() not in seen_roster:
            seen_roster.add(s_n.lower())
            roster_rows.append({
                "staff_name": s_n,
                "hourly_cost": float(r.get("hourly_cost", 7.5) or 0.0),
                "weekly_hours_declared": float(r.get("weekly_hours_declared", 30.0) or 0.0),
                "role_type": "Τεχνίτρια" if float(r.get("hourly_cost", 7.5) or 0.0) > 0 else "Ιδιοκτήτης / Διοίκηση"
            })
    df_all_staff_roster = pd.DataFrame(roster_rows)
    staff_hourly_lookup = {str(r["staff_name"]).strip().lower(): float(r["hourly_cost"]) for _, r in df_all_staff_roster.iterrows()}

    # Προ-υπολογισμός ενεργών ημερών ραντεβού ανά τεχνίτρια και λογιστική περίοδο (για ακριβή επιμερισμό σε μήνες/εβδομάδες)
    ACCOUNTING_EPOCHS = [
        ("2024", date(2024, 7, 1), date(2024, 12, 31)),
        ("2025", date(2025, 1, 1), date(2025, 12, 31)),
        ("2026_jan_aug", date(2026, 1, 1), date(2026, 8, 31)),
        ("2026_sep", date(2026, 9, 1), date(2026, 9, 30)),
    ]
    tech_epoch_appt_dates = {}
    for s_low_k, grp_s in active_comp_all.groupby(active_comp_all['staff_name'].str.strip().str.lower()):
        s_dates = sorted(grp_s['appointment_date_dt'].dt.date.unique())
        for ep_key, ep_s, ep_e in ACCOUNTING_EPOCHS:
            ep_d_list = [d for d in s_dates if ep_s <= d <= ep_e]
            tech_epoch_appt_dates[(s_low_k, ep_key)] = ep_d_list

    def compute_accounting_payroll_for_period(p_start_d: date, p_end_d: date):
        """
        Υπολογίζει για οποιοδήποτε εύρος ημερομηνιών [p_start_d, p_end_d]:
        - Το ακριβές εργοδοτικό κόστος & καθαρές αποδοχές τσέπης ανά εργαζόμενο βάσει των εκκαθαριστικών μισθοδοσίας (2024, 2025, Ιαν-Αυγ 2026, Σεπτ 2026)
        - Και για περιόδους από 01/10/2026 και μετά, βάσει των τρεχόντων μηνιαίων μισθών του πίνακα.
        """
        emp_cost = {}
        emp_net = {}
        support_details = []
        support_cost_sum = 0.0
        support_net_sum = 0.0

        for _, prof in df_payroll_profiles.iterrows():
            s_name = str(prof["staff_name"]).strip()
            s_low = s_name.lower()
            role_t = str(prof.get("role_type", "Τεχνίτρια")).strip()
            c_tot = 0.0
            n_tot = 0.0

            for ep_key, ep_s, ep_e in ACCOUNTING_EPOCHS:
                ov_s = max(p_start_d, ep_s)
                ov_e = min(p_end_d, ep_e)
                if ov_s > ov_e:
                    continue
                base_c = float(prof.get(f"cost_{ep_key}", 0.0) or 0.0)
                base_n = float(prof.get(f"net_{ep_key}", 0.0) or 0.0)
                if base_c <= 0 and base_n <= 0:
                    continue

                if not include_payroll_gifts:
                    if ep_key == "2026_sep" and s_low == "νάνσυ":
                        # Αφαίρεση εκκαθάρισης αποχώρησης (Δώρο Χριστουγέννων 796,27€ + Αποζ. Αδείας 476,98€) αν ο χρήστης θέλει μόνο τακτική μισθοδοσία
                        base_c = min(base_c, 592.08)
                        base_n = min(base_n, 416.14)
                    elif ep_key in ("2024", "2025", "2026_jan_aug"):
                        base_c = base_c * (12.0 / 14.0)
                        base_n = base_n * (12.0 / 14.0)

                # Έλεγχος αν καλύπτεται ολόκληρη η λογιστική περίοδος
                if ov_s <= (ep_s + timedelta(days=5)) and ov_e >= (ep_e - timedelta(days=5)):
                    ratio = 1.0
                else:
                    if role_t == "Τεχνίτρια":
                        d_list = tech_epoch_appt_dates.get((s_low, ep_key), [])
                        if len(d_list) > 0:
                            ov_cnt = sum(1 for d in d_list if ov_s <= d <= ov_e)
                            ratio = ov_cnt / float(len(d_list))
                        else:
                            ratio = 0.0
                    else:
                        ep_days = float((ep_e - ep_s).days + 1)
                        ov_days = float((ov_e - ov_s).days + 1)
                        ratio = ov_days / ep_days

                c_tot += base_c * ratio
                n_tot += base_n * ratio

            # Περίοδος από 01/10/2026 και μετά (Τρέχον Μηνιαίο Μισθολόγιο)
            if p_end_d >= date(2026, 10, 1) and bool(prof.get("is_active", True)):
                oct_s = max(p_start_d, date(2026, 10, 1))
                oct_e = p_end_d
                if oct_s <= oct_e:
                    oct_workdays = 0
                    cd_tmp = oct_s
                    while cd_tmp <= oct_e:
                        wd_t = cd_tmp.weekday()
                        if wd_t <= 5:
                            m_s_t = (cd_tmp - timedelta(days=wd_t)).strftime("%Y-%m-%d")
                            valid_day = True
                            if auto_skip_zero_weeks and m_s_t not in week_active_date_bounds:
                                valid_day = False
                            elif m_s_t in week_active_date_bounds:
                                w_min_t, w_max_t, w_cnt_t = week_active_date_bounds[m_s_t]
                                if w_cnt_t < 25 and (cd_tmp < w_min_t or cd_tmp > w_max_t):
                                    valid_day = False
                            if valid_day:
                                if role_t == "Τεχνίτρια" and auto_skip_zero_weeks:
                                    if (s_low, m_s_t) in active_staff_weeks_set:
                                        oct_workdays += 1
                                else:
                                    oct_workdays += 1
                        cd_tmp += timedelta(days=1)

                    mo_frac = (oct_workdays * (7.0 / 6.0)) / 30.4375
                    mo_c = float(prof.get("monthly_employer_full_14", 0.0) if include_payroll_gifts else prof.get("monthly_employer_regular", 0.0))
                    mo_n = float(prof.get("monthly_net_pocket", 0.0)) * ((14.0 / 12.0) if include_payroll_gifts else 1.0)
                    c_tot += mo_c * mo_frac
                    n_tot += mo_n * mo_frac

            c_tot = round(c_tot, 2)
            n_tot = round(n_tot, 2)
            emp_cost[s_name] = c_tot
            emp_net[s_name] = n_tot
            if role_t == "Άδεια / Υποστήριξη" and (c_tot > 0 or n_tot > 0):
                support_cost_sum += c_tot
                support_net_sum += n_tot
                support_details.append({
                    "Εργαζόμενος": s_name,
                    "Κωδικός / Ιδιότητα": str(prof.get("payroll_code", "")),
                    "Καθαρά στην Τσέπη (€)": n_tot,
                    "ΕΦΚΑ & ΦΜΥ (€)": round(max(0.0, c_tot - n_tot), 2),
                    "Συνολικό Κόστος Εργοδότη (€)": c_tot
                })

        return emp_cost, emp_net, round(support_cost_sum, 2), round(support_net_sum, 2), support_details

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

        # Δυναμικός υπολογισμός labor_cost ανά ραντεβού με το πραγματικό ωριαίο κόστος κάθε τεχνίτριας
        comp_staff_rates = df_filtered.loc[comp_mask, 'staff_name'].astype(str).str.strip().str.lower().map(lambda x: staff_hourly_lookup.get(x, 7.80))
        df_filtered.loc[comp_mask, 'labor_cost'] = ((df_filtered.loc[comp_mask, 'duration_minutes'] / 60.0) * comp_staff_rates).round(2)

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

    # --- ΗΜΕΡΑ-ΠΡΟΣ-ΗΜΕΡΑ & ΩΡΑ-ΠΡΟΣ-ΩΡΑ ΜΗΧΑΝΗ ΒΑΡΔΙΩΝ ---
    staff_scheduled_hours_map = {}
    staff_active_weeks_count = {}
    staff_cost_map = {}
    for _, s_row in df_all_staff_roster.iterrows():
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

            for _, s_row in df_all_staff_roster.iterrows():
                s_name = str(s_row['staff_name']).strip()
                s_low = s_name.lower()
                h_cost = float(s_row.get('hourly_cost', 7.5))
                role_t = str(s_row.get('role_type', 'Τεχνίτρια'))

                if auto_skip_zero_weeks and role_t == "Τεχνίτρια":
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

                if role_t == "Τεχνίτρια" and s_low != "νάντια":
                    for st_h, en_h in iv_list:
                        for h_idx, h in enumerate(range(9, 21)):
                            ov = max(0.0, min(h + 1.0, en_h) - max(float(h), st_h))
                            if ov > 0:
                                period_sched_total_matrix[wd, h_idx] += ov
                            if ov >= 0.5:
                                period_staff_per_hour_grid[wd][h_idx].add(s_name)
        curr_d += timedelta(days=1)

    emp_acct_cost_map, emp_acct_net_map, support_payroll_raw, support_net_raw, support_staff_details = compute_accounting_payroll_for_period(start_d, end_d)

    staff_perf_list = []
    total_tech_prod_h = 0.0
    total_tech_idle_h = 0.0
    total_tech_decl_h = 0.0
    tech_net_pocket_sum = 0.0

    for _, s_row in df_all_staff_roster.iterrows():
        s_name = str(s_row['staff_name']).strip()
        h_cost = float(s_row.get('hourly_cost', 7.5))
        role_t = str(s_row.get('role_type', 'Τεχνίτρια'))
        raw_decl_hours = round(staff_scheduled_hours_map.get(s_name, 0.0), 1)

        s_df = df_comp[df_comp['staff_name'].str.lower() == s_name.lower()] if not df_comp.empty else pd.DataFrame()
        prod_hours = (s_df['duration_minutes'].sum() / 60.0) if not s_df.empty else 0.0
        s_prod_labor = float(s_df['labor_cost'].sum()) if not s_df.empty else 0.0
        acct_c = float(emp_acct_cost_map.get(s_name, 0.0))
        acct_n = float(emp_acct_net_map.get(s_name, 0.0))

        # Παραλείπουμε πρώην τεχνίτριες που δεν είχαν ούτε βάρδια, ούτε ραντεβού, ούτε μισθοδοσία στην επιλεγμένη περίοδο
        if role_t == "Τεχνίτρια" and raw_decl_hours <= 0 and len(s_df) == 0 and acct_c <= 0:
            continue

        if role_t == "Τεχνίτρια":
            decl_hours = max(raw_decl_hours, round(prod_hours, 1))
            idle_hours = max(0.0, decl_hours - prod_hours)
            util_pct = (prod_hours / decl_hours * 100.0) if decl_hours > 0 else 0.0

            if use_accounting_payroll and (acct_c > 0 or h_cost == 0):
                decl_cost = round(acct_c, 2)
                s_net_pocket = round(acct_n, 2)
                if decl_hours > 0:
                    s_prod_labor = round(decl_cost * (prod_hours / decl_hours), 2)
                    idle_cost_s = round(max(0.0, decl_cost - s_prod_labor), 2)
                else:
                    s_prod_labor = 0.0
                    idle_cost_s = decl_cost
            else:
                idle_cost_s = round(idle_hours * h_cost, 2)
                decl_cost = round(s_prod_labor + idle_cost_s, 2)
                s_net_pocket = round(decl_cost * 0.745, 2)

            if h_cost > 0 or acct_c > 0:
                total_tech_prod_h += prod_hours
                total_tech_idle_h += idle_hours
                total_tech_decl_h += decl_hours
            tech_net_pocket_sum += s_net_pocket
        else:
            decl_hours = raw_decl_hours
            idle_hours = 0.0
            idle_cost_s = 0.0
            decl_cost = 0.0
            s_prod_labor = 0.0
            s_net_pocket = 0.0
            util_pct = 0.0

        s_gross = float(s_df['gross_price'].sum()) if not s_df.empty else 0.0
        s_net_rev = float(s_df['net_revenue'].sum()) if not s_df.empty else 0.0
        s_comm = float(s_df['commission_fee'].sum()) if not s_df.empty else 0.0
        s_mat = float(s_df['material_cost'].sum()) if not s_df.empty else 0.0
        s_true_net_contrib = s_net_rev - s_comm - s_mat - decl_cost
        short_appts = len(s_df[s_df['duration_minutes'] <= 15]) if not s_df.empty else 0
        s_efka_tax = round(max(0.0, decl_cost - s_net_pocket), 2)

        staff_perf_list.append({
            "Τεχνίτρια": s_name,
            "Ρόλος": "Τεχνίτρια" if role_t == "Τεχνίτρια" else "Διοίκηση/Υποδοχή",
            "Ενεργές Εβδ.": len(staff_active_weeks_count.get(s_name, set())),
            "Ώρες Βάρδιας": round(decl_hours, 1),
            "Παραγωγικές Ώρες": round(prod_hours, 1),
            "Νεκρές Ώρες (Idle)": round(idle_hours, 1),
            "Πληρότητα (%)": round(util_pct, 1),
            "Καθαρά στην Τσέπη (€)": round(s_net_pocket, 2),
            "ΕΦΚΑ & ΦΜΥ (€)": s_efka_tax,
            "Εργασία Ραντεβού (€)": round(s_prod_labor, 2),
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
    labor_prod_total = round(float(df_tech_perf["Εργασία Ραντεβού (€)"].sum()), 2) if not df_tech_perf.empty else 0.0
    tech_idle_only_cost = round(float(df_tech_perf["Κόστος Νεκρού Χρόνου (€)"].sum()), 2) if not df_tech_perf.empty else 0.0
    support_payroll_cost = round(support_payroll_raw, 2) if (use_accounting_payroll and include_support_staff) else 0.0
    support_net_pocket = round(support_net_raw, 2) if (use_accounting_payroll and include_support_staff) else 0.0

    # Το idle_labor_cost περιλαμβάνει τον μη-παραγωγικό μισθολογικό χρόνο (νεκρός χρόνος βαρδιών + προσωπικό υποστήριξης/άδειας)
    idle_labor_cost = round(tech_idle_only_cost + support_payroll_cost, 2)
    total_declared_payroll = round(labor_prod_total + idle_labor_cost, 2)
    total_net_pocket_payroll = round(tech_net_pocket_sum + support_net_pocket, 2)
    total_efka_tax_payroll = round(max(0.0, total_declared_payroll - total_net_pocket_payroll), 2)

    # Ανάκτηση πραγματικών εξόδων από το expenses_ledger για την ενεργή περίοδο
    actual_period_exp_df = pd.DataFrame()
    if not df_expenses_ledger_db.empty and "expense_date" in df_expenses_ledger_db.columns:
        try:
            exp_dates = pd.to_datetime(df_expenses_ledger_db["expense_date"]).dt.date
            exp_mask = (exp_dates >= start_d) & (exp_dates <= end_d)
            actual_period_exp_df = df_expenses_ledger_db[exp_mask].copy()
        except Exception:
            actual_period_exp_df = pd.DataFrame()

    has_actual_expenses = not actual_period_exp_df.empty
    if has_actual_expenses:
        period_net_expenses = round(float(actual_period_exp_df["net_amount"].sum()), 2)
        period_vat_expenses = round(float(actual_period_exp_df["vat_amount"].sum()), 2)
        period_gross_expenses = round(float(actual_period_exp_df["gross_amount"].sum()), 2)
        exp_by_cat = actual_period_exp_df.groupby("main_category")["net_amount"].sum().to_dict()
        actual_cogs_materials = round(float(exp_by_cat.get("Κόστος Υλικών & Εμπορευμάτων (COGS)", 0.0)), 2)
        # Λειτουργικά έξοδα χωρίς τα υλικά COGS (που μετρώνται ήδη στα υλικά)
        fixed_expenses = round(period_net_expenses - actual_cogs_materials, 2)
    else:
        monthly_fixed = float(df_expenses_db['amount'].sum()) if not df_expenses_db.empty else 1050.0
        effective_calendar_days = max(1.0, active_workdays_count * (7.0 / 6.0))
        fixed_expenses = round(monthly_fixed * (effective_calendar_days / 30.4375), 2)
        period_net_expenses = fixed_expenses
        period_vat_expenses = round(fixed_expenses * 0.24, 2)
        period_gross_expenses = round(fixed_expenses + period_vat_expenses, 2)
        actual_cogs_materials = 0.0
        exp_by_cat = {"Έξοδα Στέγασης & Λειτουργίας": fixed_expenses}

    profit_before_idle_and_fixed = round(net_rev - comm_total - mat_total - labor_prod_total, 2)
    total_all_costs = round(comm_total + mat_total + total_declared_payroll + fixed_expenses, 2)
    true_net_profit = round(net_rev - total_all_costs, 2)
    margin_pct = (true_net_profit / gross_rev * 100.0) if gross_rev > 0 else 0.0
    payroll_ratio_pct = (total_declared_payroll / net_rev * 100.0) if net_rev > 0 else 0.0
    completed_count = len(df_comp)
    aov = (gross_rev / completed_count) if completed_count > 0 else 0.0
    gross_per_prod_h = (gross_rev / total_tech_prod_h) if total_tech_prod_h > 0 else 0.0
    net_per_prod_h = (net_rev / total_tech_prod_h) if total_tech_prod_h > 0 else 0.0

    # CFO Prime Cost & Break-Even Metrics
    prime_cost = round(total_declared_payroll + mat_total, 2)
    prime_cost_pct = (prime_cost / net_rev * 100.0) if net_rev > 0 else 0.0
    break_even_fixed = round(total_declared_payroll + fixed_expenses, 2)
    variable_cost = round(comm_total + mat_total, 2)
    contribution_margin = round(net_rev - variable_cost, 2)
    cm_ratio = (contribution_margin / net_rev) if net_rev > 0 else 0.0
    break_even_rev = round(break_even_fixed / cm_ratio, 2) if cm_ratio > 0 else 0.0
    break_even_appts = int(np.ceil(break_even_rev / aov)) if aov > 0 else 0

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
            f"📍 **Ενεργή Περίοδος:** `{period_label}` • **{completed_count:,} ραντεβού** • "
            f"Έσοδο Παραγ. Ώρας: **{gross_per_prod_h:.2f} €/h** (**{net_per_prod_h:.2f} €/h** προ ΦΠΑ) • "
            f"Συνολική Μισθοδοσία Εργοδότη: **{total_declared_payroll:,.2f} €** (Καθαρά Τσέπης: **{total_net_pocket_payroll:,.2f} €** | ΕΦΚΑ+ΦΜΥ: **{total_efka_tax_payroll:,.2f} €**)"
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

    p_staff_rates = p_comp['staff_name'].astype(str).str.strip().str.lower().map(lambda x: staff_hourly_lookup.get(x, 7.80))
    p_comp['labor_cost'] = ((p_comp['duration_minutes'] / 60.0) * p_staff_rates).round(2)

    p_gross = round(float(p_comp['gross_price'].sum()), 2)
    p_vat = round(float(p_comp['vat_amount'].sum()), 2)
    p_net = round(p_gross - p_vat, 2)
    p_comm = round(float(p_comp['commission_fee'].sum()), 2)
    p_mat = round(float(p_comp['material_cost'].sum()), 2)
    p_prod_lab = round(float(p_comp['labor_cost'].sum()), 2)
    p_prod_h = float(p_comp['duration_minutes'].sum()) / 60.0

    p_sched_map = {str(r['staff_name']).strip(): 0.0 for _, r in df_all_staff_roster.iterrows()}
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
            for _, s_row in df_all_staff_roster.iterrows():
                s_n = str(s_row['staff_name']).strip()
                s_l = s_n.lower()
                role_t = str(s_row.get('role_type', 'Τεχνίτρια'))
                if auto_skip_zero_weeks and role_t == "Τεχνίτρια" and (s_l, m_s) not in active_staff_weeks_set:
                    continue
                if s_l in wk_m:
                    d_h = calc_shift_hours(wk_m[s_l].get(d_col, ""))
                else:
                    d_h = float(s_row.get('weekly_hours_declared', 30)) / 6.0
                if d_h > 0:
                    p_sched_map[s_n] = p_sched_map.get(s_n, 0.0) + d_h
        c_d += timedelta(days=1)

    p_emp_cost, p_emp_net, p_sup_cost, p_sup_net, _ = compute_accounting_payroll_for_period(p_start_d, p_end_d)

    p_decl_h_tot = 0.0
    p_idle_h_tot = 0.0
    p_prod_lab_tot = 0.0
    p_idle_cost_tot = 0.0
    for _, s_row in df_all_staff_roster.iterrows():
        s_n = str(s_row['staff_name']).strip()
        h_c = float(s_row.get('hourly_cost', 7.5))
        role_t = str(s_row.get('role_type', 'Τεχνίτρια'))
        if role_t != "Τεχνίτρια":
            continue
        raw_dh = round(p_sched_map.get(s_n, 0.0), 1)
        s_sub = p_comp[p_comp['staff_name'].str.lower() == s_n.lower()]
        pr_h = (s_sub['duration_minutes'].sum() / 60.0) if not s_sub.empty else 0.0
        acct_c = float(p_emp_cost.get(s_n, 0.0))
        if raw_dh <= 0 and len(s_sub) == 0 and acct_c <= 0:
            continue
        dh = max(raw_dh, round(pr_h, 1))
        ih = max(0.0, dh - pr_h)
        if h_c > 0 or acct_c > 0:
            p_decl_h_tot += dh
            p_idle_h_tot += ih
        if use_accounting_payroll and (acct_c > 0 or h_c == 0):
            s_pl = round(acct_c * (pr_h / dh), 2) if dh > 0 else 0.0
            s_ic = round(max(0.0, acct_c - s_pl), 2)
        else:
            s_pl = float(s_sub['labor_cost'].sum()) if not s_sub.empty else 0.0
            s_ic = round(ih * h_c, 2)
        p_prod_lab_tot += s_pl
        p_idle_cost_tot += s_ic

    p_sup_effective = round(p_sup_cost, 2) if (use_accounting_payroll and include_support_staff) else 0.0
    p_prod_lab = round(p_prod_lab_tot, 2)
    p_idle_cost_tot = round(p_idle_cost_tot + p_sup_effective, 2)
    p_payroll_tot = round(p_prod_lab + p_idle_cost_tot, 2)
    p_occ = (p_prod_h / p_decl_h_tot * 100.0) if p_decl_h_tot > 0 else 0.0
    p_eff_days = max(1.0, p_workdays * (7.0 / 6.0))

    # Ανάκτηση πραγματικών εξόδων από το expenses_ledger για την υπό σύγκριση περίοδο
    p_act_exp_df = pd.DataFrame()
    if not df_expenses_ledger_db.empty and "expense_date" in df_expenses_ledger_db.columns:
        try:
            p_exp_dates = pd.to_datetime(df_expenses_ledger_db["expense_date"]).dt.date
            p_exp_mask = (p_exp_dates >= p_start_d) & (p_exp_dates <= p_end_d)
            p_act_exp_df = df_expenses_ledger_db[p_exp_mask].copy()
        except Exception:
            p_act_exp_df = pd.DataFrame()

    p_has_actual = not p_act_exp_df.empty
    if p_has_actual:
        p_net_exp = round(float(pd.to_numeric(p_act_exp_df["net_amount"], errors="coerce").sum()), 2)
        p_vat_exp = round(float(pd.to_numeric(p_act_exp_df["vat_amount"], errors="coerce").sum()), 2)
        p_gross_exp = round(float(pd.to_numeric(p_act_exp_df["gross_amount"], errors="coerce").sum()), 2)
        p_exp_by_cat = p_act_exp_df.groupby("main_category")["net_amount"].sum().to_dict()
        p_cogs_mat = round(float(p_exp_by_cat.get("Κόστος Υλικών & Εμπορευμάτων (COGS)", 0.0)), 2)
        p_fixed = round(p_net_exp - p_cogs_mat, 2)
        p_exp_source = "Ledger (Πραγματικά)"
    else:
        p_fixed = round(monthly_fixed * (p_eff_days / 30.4375), 2)
        p_net_exp = p_fixed
        p_vat_exp = round(p_fixed * 0.24, 2)
        p_gross_exp = round(p_fixed + p_vat_exp, 2)
        p_cogs_mat = 0.0
        p_exp_source = "Budget (Εκτίμηση)"

    p_true_net = round(p_net - p_comm - p_mat - p_payroll_tot - p_fixed, 2)
    p_margin = (p_true_net / p_gross * 100.0) if p_gross > 0 else 0.0
    p_cnt = len(p_comp)
    p_aov = (p_gross / p_cnt) if p_cnt > 0 else 0.0
    p_net_per_h = (p_net / p_prod_h) if p_prod_h > 0 else 0.0
    p_gross_per_h = (p_gross / p_prod_h) if p_prod_h > 0 else 0.0

    p_prime_cost = round(p_payroll_tot + p_mat, 2)
    p_prime_cost_pct = (p_prime_cost / p_net * 100.0) if p_net > 0 else 0.0
    p_be_fixed = round(p_payroll_tot + p_fixed, 2)
    p_var_cost = round(p_comm + p_mat, 2)
    p_cm = round(p_net - p_var_cost, 2)
    p_cm_ratio = (p_cm / p_net) if p_net > 0 else 0.0
    p_be_rev = round(p_be_fixed / p_cm_ratio, 2) if p_cm_ratio > 0 else 0.0
    p_be_appts = int(np.ceil(p_be_rev / p_aov)) if p_aov > 0 else 0

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
        "Prime Cost (€)": p_prime_cost,
        "Prime Cost (%)": round(p_prime_cost_pct, 1),
        "Πάγια Έξοδα (€)": p_fixed,
        "Καθαρά Έξοδα Τιμολογίων (€)": p_net_exp,
        "ΦΠΑ Εξόδων (€)": p_vat_exp,
        "Συνολική Εκροή Εξόδων (€)": p_gross_exp,
        "Νεκρό Σημείο Τζίρου (€)": p_be_rev,
        "Νεκρό Σημείο Ραντεβού": p_be_appts,
        "Πηγή Εξόδων": p_exp_source,
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

        # --- ΕΙΔΙΚΗ ΕΝΟΤΗΤΑ CFO: PRIME COST, BREAK-EVEN & ΔΟΜΗ ΕΞΟΔΩΝ ---
        with st.container(border=True):
            st.markdown("##### 🎯 Στρατηγικοί Δείκτες CFO: Prime Cost, Νεκρό Σημείο & Ταμειακή Ροή Εξόδων")
            col_strat1, col_strat2, col_strat3 = st.columns(3, gap="medium")
            with col_strat1:
                prime_status = "🟢 Υγιές (<55%)" if prime_cost_pct <= 55.0 else ("🟠 Οριακό (55%-62%)" if prime_cost_pct <= 62.0 else "🔴 Υψηλό (>62%)")
                st.metric(
                    "Prime Cost (Μισθοδοσία + Υλικά)",
                    f"{prime_cost:,.2f} €",
                    f"{prime_cost_pct:.1f}% του καθαρού ({prime_status})",
                    delta_color="off",
                    border=True,
                    help="Το Prime Cost είναι το άθροισμα μισθοδοσίας και αναλωσίμων. Στον κλάδο της ομορφιάς/νυχιών, το βέλτιστο όριο είναι 45%–55% του καθαρού τζίρου."
                )
            with col_strat2:
                be_margin_eur = round(net_rev - break_even_rev, 2)
                st.metric(
                    "Νεκρό Σημείο Τζίρου (Break-Even)",
                    f"{break_even_rev:,.2f} €",
                    f"{be_margin_eur:+,.2f} € περιθώριο ασφαλείας" if net_rev > 0 else "0.00 €",
                    delta_color="normal" if be_margin_eur >= 0 else "inverse",
                    border=True,
                    help="Ο καθαρός τζίρος που απαιτείται ώστε τα έσοδα να καλύψουν ακριβώς τη μισθοδοσία και τα πάγια λειτουργικά έξοδα."
                )
            with col_strat3:
                be_appts_diff = completed_count - break_even_appts
                st.metric(
                    "Ελάχιστα Ραντεβού Νεκρού Σημείου",
                    f"{break_even_appts:,} ραντεβού",
                    f"{be_appts_diff:+,} έναντι πραγματικών ({completed_count:,})" if completed_count > 0 else "0 ραντ.",
                    delta_color="normal" if be_appts_diff >= 0 else "inverse",
                    border=True,
                    help="Ο αριθμός ολοκληρωμένων ραντεβού που χρειάζονται για να μηδενιστεί η ζημιά. Κάθε επιπλέον ραντεβού παράγει καθαρό κέρδος."
                )

            # Υπο-ενότητα Δομής Εξόδων & Ταμειακής Ροής
            col_exp_viz, col_exp_cf = st.columns([1.25, 1.15], gap="medium")
            with col_exp_viz:
                if has_actual_expenses:
                    st.caption("📊 **Πραγματικά Έξοδα Περιόδου ανά Κατηγορία (expenses_ledger)**")
                    cat_items = sorted(exp_by_cat.items(), key=lambda x: x[1], reverse=True)
                    fig_exp_cat = go.Figure(go.Pie(
                        labels=[k for k, _ in cat_items],
                        values=[v for _, v in cat_items],
                        hole=0.45,
                        textinfo="percent+label",
                        marker=dict(colors=["#3b82f6", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#64748b"])
                    ))
                    fig_exp_cat.update_layout(height=240, margin=dict(t=10, b=10, l=10, r=10), showlegend=False, paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_exp_cat, width="stretch")
                else:
                    st.caption("🏢 **Προϋπολογιστικά Πάγια Περιόδου (expenses_log)**")
                    st.info(
                        f"💡 Για την περίοδο `{period_label}` υπολογίζονται προϋπολογιστικά πάγια **{fixed_expenses:,.2f} €** "
                        f"(βάσει του μηνιαίου προϋπολογισμού {monthly_fixed:,.2f} €). "
                        f"Καταχωρήστε τα πραγματικά τιμολόγια στην **Καρτέλα 5 (Διαχείριση)** για πλήρη λογιστική ακρίβεια!",
                        icon=":material/info:"
                    )
            with col_exp_cf:
                st.caption("💶 **Ταμειακή Ροή & Εκκαθάριση ΦΠΑ Περιόδου**")
                net_vat_due = round(vat_total - period_vat_expenses, 2)
                st.html(f"""
                <div style="background: rgba(255,255,255,0.03); border: 1px solid rgba(148,163,184,0.2); border-radius: 8px; padding: 12px 16px; font-size: 0.90rem; line-height: 1.6;">
                    <div>• Καθαρά Έξοδα Περιόδου: <b>{period_net_expenses:,.2f} €</b></div>
                    <div>• ΦΠΑ Εισροών Εξόδων: <b style="color:#10b981;">+{period_vat_expenses:,.2f} €</b> (προς έκπτωση)</div>
                    <div>• Συνολική Ταμειακή Εκροή (με ΦΠΑ): <b style="color:#ef4444;">-{period_gross_expenses:,.2f} €</b></div>
                    <hr style="margin: 8px 0; border: none; border-top: 1px solid rgba(148,163,184,0.2);">
                    <div>• ΦΠΑ Εσόδων (Εκροών): <b>{vat_total:,.2f} €</b></div>
                    <div>• <b>Εκτιμώμενος ΦΠΑ προς Απόδοση:</b> <b style="color:{'#f59e0b' if net_vat_due > 0 else '#10b981'}; font-size: 1.0rem;">{net_vat_due:,.2f} €</b></div>
                    <div style="font-size: 0.78rem; opacity: 0.8; margin-top: 4px;">Πηγή Εξόδων: <b>{'🟢 Πραγματικά Τιμολόγια (Ledger)' if has_actual_expenses else '🟠 Προϋπολογισμός (Budget)'}</b></div>
                </div>
                """)

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
                            preferred_model=selected_gemini_model,
                            provider=ai_provider_choice,
                            thinking_level=selected_thinking_level
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
                    mo_pay_list = []
                    for _, r in tr_grp.iterrows():
                        ym_s = str(r['ym_str'])
                        if use_accounting_payroll:
                            yr_i, mo_i = int(ym_s[:4]), int(ym_s[5:7])
                            ms_d = max(start_d, date(yr_i, mo_i, 1))
                            me_d = min(end_d, date(yr_i, 12, 31) if mo_i == 12 else (date(yr_i, mo_i + 1, 1) - timedelta(days=1)))
                            ec_m, _, sc_m, _, _ = compute_accounting_payroll_for_period(ms_d, me_d)
                            tot_m_pay = sum(ec_m.values()) - (0.0 if include_support_staff else sc_m)
                            mo_pay_list.append(round(max(float(r['Εργασία_Ραντεβού']), tot_m_pay), 2))
                        else:
                            mo_pay_list.append(round(max(float(r['Εργασία_Ραντεβού']), float(mo_pay.get(ym_s, 0.0))), 2))
                    tr_grp['Μισθοδοσία_Βαρδιών'] = mo_pay_list
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
                    wk_pay_list = []
                    for _, r in tr_grp.iterrows():
                        m_s_w = str(r['monday_date_str'])
                        if use_accounting_payroll:
                            ws_d = max(start_d, datetime.strptime(m_s_w, "%Y-%m-%d").date())
                            we_d = min(end_d, ws_d + timedelta(days=6))
                            ec_w, _, sc_w, _, _ = compute_accounting_payroll_for_period(ws_d, we_d)
                            tot_w_pay = sum(ec_w.values()) - (0.0 if include_support_staff else sc_w)
                            wk_pay_list.append(round(max(float(r['Εργασία_Ραντεβού']), tot_w_pay), 2))
                        else:
                            wk_pay_list.append(round(max(float(r['Εργασία_Ραντεβού']), float(daily_payroll_by_monday.get(m_s_w, 0.0))), 2))
                    tr_grp['Μισθοδοσία_Βαρδιών'] = wk_pay_list
                    tr_grp['Πάγια'] = tr_grp['monday_date_str'].apply(
                        lambda m: round(monthly_fixed * (7.0 / 30.4375), 2) if wk_counts.get(m, 0) >= 25 else round(monthly_fixed * (3.0 / 30.4375), 2)
                    )
                    tr_grp['Καθαρό_Κέρδος'] = (
                        tr_grp['Καθαρός_Τζίρος'] - tr_grp['Προμήθειες'] - tr_grp['Υλικά'] - tr_grp['Μισθοδοσία_Βαρδιών'] - tr_grp['Πάγια']
                    ).round(2)
                    x_col_tr = 'monday_date_str'

                fig_tr = go.Figure()
                fig_tr.add_trace(go.Bar(x=tr_grp[x_col_tr], y=tr_grp['Καθαρός_Τζίρος'], name="Καθαρός Τζίρος (προ ΦΠΑ)", marker_color="#38bdf8"))
                fig_tr.add_trace(go.Bar(x=tr_grp[x_col_tr], y=tr_grp['Μισθοδοσία_Βαρδιών'], name="Συνολική Μισθοδοσία Εργοδότη", marker_color="#f59e0b"))
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
                                ("Prime Cost (€)", "€"),
                                ("Prime Cost (%)", "%"),
                                ("Προμήθειες TW (€)", "€"),
                                ("Πάγια Έξοδα (€)", "€"),
                                ("Καθαρά Έξοδα Τιμολογίων (€)", "€"),
                                ("Συνολική Εκροή Εξόδων (€)", "€"),
                                ("Νεκρό Σημείο Τζίρου (€)", "€"),
                                ("Νεκρό Σημείο Ραντεβού", ""),
                                ("Πηγή Εξόδων", ""),
                                ("Καθαρό Κέρδος (€)", "€"),
                                ("Περιθώριο Κέρδους (%)", "%")
                            ]
                            for k_name, unit_s in ordered_keys:
                                raw_a = sum_a.get(k_name, 0)
                                raw_b = sum_b.get(k_name, 0)
                                if isinstance(raw_a, str) or isinstance(raw_b, str):
                                    rows_cmp.append({
                                        "Οικονομικός Δείκτης": k_name,
                                        f"🅰️ {lbl_a}": str(raw_a),
                                        f"🅱️ {lbl_b}": str(raw_b),
                                        "Διαφορά (Α - Β)": "-",
                                        "Μεταβολή (%)": "-"
                                    })
                                    continue
                                va = float(raw_a)
                                vb = float(raw_b)
                                diff_v = va - vb
                                pct_v = ((va - vb) / abs(vb) * 100.0) if abs(vb) > 0.01 else 0.0
                                if unit_s == "":
                                    disp_a = f"{int(va):,}"
                                    disp_b = f"{int(vb):,}"
                                    disp_d = f"{int(diff_v):+,}"
                                else:
                                    disp_a = f"{va:,.2f} {unit_s}"
                                    disp_b = f"{vb:,.2f} {unit_s}"
                                    disp_d = f"{diff_v:+,.2f} {unit_s}"
                                rows_cmp.append({
                                    "Οικονομικός Δείκτης": k_name,
                                    f"🅰️ {lbl_a}": disp_a,
                                    f"🅱️ {lbl_b}": disp_b,
                                    "Διαφορά (Α - Β)": disp_d,
                                    "Μεταβολή (%)": f"{pct_v:+.1f}%"
                                })
                            st.dataframe(pd.DataFrame(rows_cmp), hide_index=True, width="stretch", height=420)

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
                    cols_to_show_yr = [
                        "Περίοδος", "Ραντεβού", "Μικτός Τζίρος (€)", "Καθαρός Τζίρος προ ΦΠΑ (€)",
                        "Καθαρό Έσοδο / Παραγ. Ώρα (€/h)", "Παραγωγικές Ώρες (h)", "Πληρότητα (%)",
                        "Συνολική Μισθοδοσία (€)", "Αναλώσιμα Υλικά (€)", "Prime Cost (€)", "Prime Cost (%)",
                        "Προμήθειες TW (€)", "Πάγια Έξοδα (€)", "Νεκρό Σημείο Τζίρου (€)", "Καθαρό Κέρδος (€)", "Περιθώριο Κέρδους (%)", "Πηγή Εξόδων"
                    ]
                    avail_cols_yr = [c for c in cols_to_show_yr if c in df_yr_all.columns]
                    st.dataframe(df_yr_all[avail_cols_yr], hide_index=True, width="stretch")

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
                    cols_to_show_mo = [
                        "Περίοδος", "Ραντεβού", "Μικτός Τζίρος (€)", "Καθαρός Τζίρος προ ΦΠΑ (€)",
                        "Καθαρό Έσοδο / Παραγ. Ώρα (€/h)", "Παραγωγικές Ώρες (h)", "Πληρότητα (%)",
                        "Συνολική Μισθοδοσία (€)", "Αναλώσιμα Υλικά (€)", "Prime Cost (€)", "Prime Cost (%)",
                        "Προμήθειες TW (€)", "Πάγια Έξοδα (€)", "Νεκρό Σημείο Τζίρου (€)", "Καθαρό Κέρδος (€)", "Περιθώριο Κέρδους (%)", "Πηγή Εξόδων"
                    ]
                    avail_cols_mo = [c for c in cols_to_show_mo if c in df_mo_all.columns]
                    st.dataframe(df_mo_all[avail_cols_mo], hide_index=True, width="stretch")


# --- ΒΕΛΤΙΣΤΟΠΟΙΗΤΗΣ ΒΑΡΔΙΩΝ & ΟΠΤΙΚΟΣ ΕΝΤΟΠΙΣΜΟΣ IDLE TIME ---
def render_daily_gantt_idle_detector(df_comp, db_schedules, staff_hourly_lookup, use_rolling_schedule):
    with st.container(border=True):
        st.subheader("🔬 Οπτικό Ημερήσιο Timeline Ραντεβού vs Βάρδιας (Daily Gantt & Ανίχνευση Κενών)", icon=":material/view_timeline:")
        st.caption(
            "Επιλέξτε οποιαδήποτε ημέρα της περιόδου για να δείτε ακριβώς πότε ξεκίνησε η βάρδια κάθε τεχνίτριας, "
            "ποια ραντεβού εκτέλεσε, και **πού ακριβώς δημιουργήθηκαν τα κόκκινα κενά (νεκρές ώρες)** που κοστίζουν μισθοδοσία."
        )

        if df_comp.empty or "appointment_date" not in df_comp.columns:
            st.info("Δεν υπάρχουν ραντεβού στην επιλεγμένη περίοδο.")
            return

        unique_dates = sorted(df_comp["appointment_date"].dropna().unique().tolist())
        if not unique_dates:
            st.info("Δεν βρέθηκαν διαθέσιμες ημερομηνίες.")
            return

        date_idle_stats = []
        for d_str in unique_dates:
            dt_obj = pd.to_datetime(d_str, errors="coerce")
            if pd.isna(dt_obj):
                continue
            wd = dt_obj.weekday()
            if wd > 5:
                continue
            day_name = DAYS_GR[wd]
            mon_str = (dt_obj - timedelta(days=wd)).strftime("%Y-%m-%d")
            wk_rows, _, _ = resolve_week_schedule_smart(mon_str, db_schedules, use_rolling_schedule)
            day_sched_h = sum(
                calc_shift_hours(r.get(day_name, ""))
                for r in wk_rows
                if str(r.get("staff_name", "")).strip().lower() != "νάντια"
            )
            day_booked_m = df_comp[df_comp["appointment_date"] == d_str]["duration_minutes"].sum()
            day_booked_h = day_booked_m / 60.0
            idle_h = max(0.0, day_sched_h - day_booked_h)
            date_idle_stats.append({
                "date_str": d_str,
                "dt_obj": dt_obj,
                "day_name": day_name,
                "sched_h": day_sched_h,
                "booked_h": day_booked_h,
                "idle_h": idle_h,
                "idle_cost": idle_h * 7.80
            })

        if not date_idle_stats:
            st.info("Δεν βρέθηκαν εργάσιμες ημέρες.")
            return

        date_idle_stats.sort(key=lambda x: x["idle_h"], reverse=True)

        date_options = [
            f"{x['date_str']} ({x['day_name']}) — {x['idle_h']:.1f}h Idle (-{x['idle_cost']:.0f} €) 🔴"
            if x["idle_h"] >= 4.0 else
            f"{x['date_str']} ({x['day_name']}) — {x['idle_h']:.1f}h Idle (-{x['idle_cost']:.0f} €)"
            for x in date_idle_stats
        ]

        c_sel1, c_sel2 = st.columns([2.5, 1.5], vertical_alignment="center")
        with c_sel1:
            chosen_label = st.selectbox(
                "📅 Επιλέξτε Ημερομηνία για Ακτινογραφία Ραντεβού (Ταξινομημένες κατά Νεκρό Χρόνο)",
                options=date_options,
                index=0,
                key="gantt_day_picker"
            )
        with c_sel2:
            st.caption(
                "💡 Προεπιλογή: **Η #1 ημέρα με το μεγαλύτερο Idle Time** στην περίοδο, ώστε να εντοπίζετε αμέσως τη μεγαλύτερη σπατάλη."
            )

        selected_idx = date_options.index(chosen_label)
        day_info = date_idle_stats[selected_idx]
        sel_date_str = day_info["date_str"]
        sel_dt = day_info["dt_obj"]
        sel_day_name = day_info["day_name"]
        mon_str = (sel_dt - timedelta(days=sel_dt.weekday())).strftime("%Y-%m-%d")

        wk_rows, is_exact, src_key = resolve_week_schedule_smart(mon_str, db_schedules, use_rolling_schedule)
        wk_map = {str(r.get("staff_name", "")).strip().lower(): r for r in wk_rows}

        df_day_appts = df_comp[df_comp["appointment_date"] == sel_date_str].copy()

        active_techs_set = set()
        for r in wk_rows:
            t_name = str(r.get("staff_name", "")).strip()
            if t_name and t_name.lower() != "νάντια" and calc_shift_hours(r.get(sel_day_name, "")) > 0:
                active_techs_set.add(t_name)
        for _, b_row in df_day_appts.iterrows():
            stf = str(b_row.get("staff_name", "")).strip()
            if stf and stf.lower() != "νάντια":
                active_techs_set.add(stf)

        if not active_techs_set:
            st.warning(f"Δεν βρέθηκαν τεχνίτριες με βάρδια ή ραντεβού για την ημέρα {sel_date_str}.")
            return

        sorted_techs = sorted(list(active_techs_set))

        timeline_data = []
        total_day_shift_h = 0.0
        total_day_prod_h = 0.0
        total_day_idle_h = 0.0
        gap_type_sums = {"morning": 0.0, "midday": 0.0, "evening": 0.0}

        for t_name in sorted_techs:
            t_low = t_name.lower()
            hourly_rate = staff_hourly_lookup.get(t_low, 7.80)
            shift_str = wk_map.get(t_low, {}).get(sel_day_name, "")
            intervals = parse_shift_intervals(shift_str)

            t_appts = df_day_appts[df_day_appts["staff_name"].str.strip().str.lower() == t_low].copy()
            parsed_t_appts = []
            for _, ar in t_appts.iterrows():
                t_str = str(ar.get("appointment_time", "09:00:00"))
                tp = t_str.split(":")
                st_h = int(tp[0]) + (int(tp[1]) / 60.0 if len(tp) > 1 else 0.0)
                dur_m = float(ar.get("duration_minutes", 45))
                dur_h = max(0.25, dur_m / 60.0)
                en_h = st_h + dur_h
                parsed_t_appts.append({
                    "start": st_h,
                    "end": en_h,
                    "dur_m": dur_m,
                    "dur_h": dur_h,
                    "service": str(ar.get("raw_service", "Υπηρεσία")),
                    "category": str(ar.get("category", "Μανικιούρ")),
                    "price": float(ar.get("gross_price", 0.0)),
                    "order_id": str(ar.get("order_id", ""))
                })
            parsed_t_appts.sort(key=lambda x: x["start"])

            if intervals:
                shift_st = intervals[0][0]
                shift_en = intervals[-1][1]
                shift_dur = sum(en - st for st, en in intervals)
            elif parsed_t_appts:
                shift_st = max(8.5, parsed_t_appts[0]["start"] - 0.25)
                shift_en = min(21.0, parsed_t_appts[-1]["end"] + 0.25)
                shift_dur = shift_en - shift_st
            else:
                shift_st, shift_en, shift_dur = 9.0, 17.0, 8.0

            total_day_shift_h += shift_dur
            prod_dur = sum(a["dur_h"] for a in parsed_t_appts)
            total_day_prod_h += prod_dur

            tech_gaps = []
            if not parsed_t_appts:
                tech_gaps.append({
                    "type": "Ολόκληρη η Βάρδια Κενή (0 Ραντεβού!)",
                    "start": shift_st,
                    "end": shift_en,
                    "dur": shift_dur,
                    "cost": shift_dur * hourly_rate
                })
                gap_type_sums["morning"] += shift_dur
            else:
                if parsed_t_appts[0]["start"] > shift_st + 0.20:
                    g_dur = parsed_t_appts[0]["start"] - shift_st
                    tech_gaps.append({
                        "type": "🌅 Πρωινή Αναμονή Έναρξης",
                        "start": shift_st,
                        "end": parsed_t_appts[0]["start"],
                        "dur": g_dur,
                        "cost": g_dur * hourly_rate
                    })
                    gap_type_sums["morning"] += g_dur

                for i in range(len(parsed_t_appts) - 1):
                    p_end = parsed_t_appts[i]["end"]
                    n_start = parsed_t_appts[i+1]["start"]
                    if n_start > p_end + 0.20:
                        g_dur = n_start - p_end
                        tech_gaps.append({
                            "type": "🥪 Ενδιάμεσο Κενό (Ελβετικό Τυρί)",
                            "start": p_end,
                            "end": n_start,
                            "dur": g_dur,
                            "cost": g_dur * hourly_rate
                        })
                        gap_type_sums["midday"] += g_dur

                if parsed_t_appts[-1]["end"] < shift_en - 0.20:
                    g_dur = shift_en - parsed_t_appts[-1]["end"]
                    tech_gaps.append({
                        "type": "🌇 Απογευματινή Αναμονή Λήξης",
                        "start": parsed_t_appts[-1]["end"],
                        "end": shift_en,
                        "dur": g_dur,
                        "cost": g_dur * hourly_rate
                    })
                    gap_type_sums["evening"] += g_dur

            t_idle_h = sum(g["dur"] for g in tech_gaps)
            total_day_idle_h += t_idle_h

            timeline_data.append({
                "tech_name": t_name,
                "shift_st": shift_st,
                "shift_en": shift_en,
                "shift_dur": shift_dur,
                "hourly_rate": hourly_rate,
                "appts": parsed_t_appts,
                "gaps": tech_gaps,
                "idle_h": t_idle_h,
                "idle_cost": t_idle_h * hourly_rate
            })

        day_cost_idle = total_day_idle_h * 7.80
        day_occ_pct = (total_day_prod_h / max(0.1, total_day_shift_h)) * 100.0

        dk1, dk2, dk3, dk4 = st.columns(4)
        dk1.metric(
            "1. Ώρες Βάρδιας (Κατάστημα)",
            f"{total_day_shift_h:.1f} h",
            f"{len(sorted_techs)} τεχνίτριες παρούσες",
            delta_color="off",
            border=True
        )
        dk2.metric(
            "2. Παραγωγικές Ώρες Ραντεβού",
            f"{total_day_prod_h:.1f} h",
            f"{len(df_day_appts)} ραντεβού εκτελέστηκαν",
            delta_color="off",
            border=True
        )
        dk3.metric(
            "3. Νεκρές Ώρες (Idle Time)",
            f"{total_day_idle_h:.1f} h",
            f"{100.0 - day_occ_pct:.1f}% της ημέρας χαμένο",
            delta_color="inverse",
            border=True
        )
        dk4.metric(
            "4. Κόστος Νεκρού Χρόνου Σήμερα",
            f"-{day_cost_idle:,.2f} €",
            "Μισθοδοσία χωρίς παραγωγή",
            delta_color="inverse",
            border=True
        )

        fig_gantt = go.Figure()
        cat_color_map = {
            "Μανικιούρ": "#10b981",
            "Πεντικιούρ": "#06b6d4",
            "Πακέτο": "#8b5cf6",
            "Βλεφαρίδες/Φρύδια": "#f59e0b",
            "Τεχνητά / Επιμήκυνση": "#ec4899",
            "Άγνωστη": "#64748b"
        }

        for item in timeline_data:
            t_name = item["tech_name"]
            st_h = item["shift_st"]
            en_h = item["shift_en"]
            fig_gantt.add_trace(go.Bar(
                y=[t_name],
                x=[en_h - st_h],
                base=[st_h],
                orientation="h",
                marker=dict(color="#f1f5f9", line=dict(color="#cbd5e1", width=1.5)),
                name="Προγραμματισμένη Βάρδια",
                hoverinfo="text",
                hovertext=f"<b>{t_name} — Βάρδια:</b> {int(st_h):02d}:{int(round((st_h%1)*60)):02d} – {int(en_h):02d}:{int(round((en_h%1)*60)):02d} ({en_h-st_h:.1f}h)",
                showlegend=False
            ))

        seen_cats = set()
        for item in timeline_data:
            t_name = item["tech_name"]
            for a in item["appts"]:
                c_name = a["category"]
                col = cat_color_map.get(c_name, "#10b981")
                st_time_str = f"{int(a['start']):02d}:{int(round((a['start']%1)*60)):02d}"
                en_time_str = f"{int(a['end']):02d}:{int(round((a['end']%1)*60)):02d}"
                short_srv = (a["service"][:18] + "..") if len(a["service"]) > 20 else a["service"]
                show_leg = c_name not in seen_cats
                seen_cats.add(c_name)

                fig_gantt.add_trace(go.Bar(
                    y=[t_name],
                    x=[a["dur_h"]],
                    base=[a["start"]],
                    orientation="h",
                    marker=dict(color=col, line=dict(color="#ffffff", width=1)),
                    name=f"💅 {c_name}",
                    legendgroup=f"cat_{c_name}",
                    showlegend=show_leg,
                    hoverinfo="text",
                    hovertext=(
                        f"<b>{t_name}</b><br>"
                        f"💅 <b>Υπηρεσία:</b> {a['service']}<br>"
                        f"⏱️ <b>Ώρα:</b> {st_time_str} – {en_time_str} ({int(a['dur_m'])}\')<br>"
                        f"💶 <b>Αξία:</b> {a['price']:.2f} €"
                    ),
                    text=f"{short_srv} ({int(a['dur_m'])}\')",
                    textposition="inside",
                    insidetextanchor="middle",
                    textfont=dict(size=11, color="#ffffff")
                ))

        seen_gap_leg = False
        for item in timeline_data:
            t_name = item["tech_name"]
            for g in item["gaps"]:
                st_time_str = f"{int(g['start']):02d}:{int(round((g['start']%1)*60)):02d}"
                en_time_str = f"{int(g['end']):02d}:{int(round((g['end']%1)*60)):02d}"
                gap_mins = int(round(g["dur"] * 60))
                fig_gantt.add_trace(go.Bar(
                    y=[t_name],
                    x=[g["dur"]],
                    base=[g["start"]],
                    orientation="h",
                    marker=dict(
                        color="rgba(239, 68, 68, 0.85)",
                        pattern=dict(shape="/", fillmode="replace", bgcolor="rgba(239, 68, 68, 0.40)"),
                        line=dict(color="#ef4444", width=1.5)
                    ),
                    name="🔴 Νεκρό Κενό (Idle)",
                    legendgroup="idle_gap",
                    showlegend=not seen_gap_leg,
                    hoverinfo="text",
                    hovertext=(
                        f"<b>{t_name} — {g['type']}</b><br>"
                        f"⏳ <b>Διάρκεια:</b> {gap_mins} λεπτά ({g['dur']:.2f}h)<br>"
                        f"⏱️ <b>Ώρα:</b> {st_time_str} – {en_time_str}<br>"
                        f"💸 <b>Κόστος Μισθοδοσίας:</b> -{g['cost']:.2f} €"
                    ),
                    text=f"❌ {gap_mins}\' κενό (-{g['cost']:.0f}€)" if gap_mins >= 35 else f"❌ {gap_mins}\'",
                    textposition="inside",
                    insidetextanchor="middle",
                    textfont=dict(size=10, color="#ffffff")
                ))
                seen_gap_leg = True

        chart_height = max(320, len(sorted_techs) * 65 + 90)
        fig_gantt.update_layout(
            barmode="overlay",
            height=chart_height,
            margin=dict(t=25, b=25, l=15, r=15),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(
                title="Ώρα Ημέρας",
                tickmode="linear",
                tick0=8.0,
                dtick=1.0,
                tickformat="%d",
                ticksuffix=":00",
                range=[8.5, 21.5],
                gridcolor="#e2e8f0"
            ),
            yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
        )
        st.plotly_chart(fig_gantt, width="stretch")

        tot_gaps_sum = max(0.1, sum(gap_type_sums.values()))
        pct_morn = (gap_type_sums["morning"] / tot_gaps_sum) * 100.0
        pct_mid = (gap_type_sums["midday"] / tot_gaps_sum) * 100.0
        pct_eve = (gap_type_sums["evening"] / tot_gaps_sum) * 100.0

        st.markdown("##### 💡 Αυτόματη Διάγνωση: Πού Χάθηκε ο Χρόνος Σήμερα;")
        dc1, dc2, dc3 = st.columns(3, gap="medium")
        with dc1:
            st.info(
                f"**🌅 1. Πρωινή Αναμονή Έναρξης**\n\n"
                f"• **{gap_type_sums['morning']:.1f} ώρες** ({pct_morn:.0f}% του νεκρού χρόνου)\n\n"
                f"👉 *Αιτία: Τεχνίτριες ήρθαν νωρίς αλλά τα πρώτα ραντεβού ξεκίνησαν αργότερα. Λύση: Μετατόπιση έναρξης βάρδιας (π.χ. 11:00 αντί για 09:30).*"
            )
        with dc2:
            st.warning(
                f"**🥪 2. Ενδιάμεσα Κενά («Ελβετικό Τυρί»)**\n\n"
                f"• **{gap_type_sums['midday']:.1f} ώρες** ({pct_mid:.0f}% του νεκρού χρόνου)\n\n"
                f"👉 *Αιτία: Μικρά κενά 30'–50' ανάμεσα στα ραντεβού. Λύση: Ενεργοποίηση back-to-back κανόνα στο Treatwell / γραμματεία.*"
            )
        with dc3:
            st.error(
                f"**🌇 3. Απογευματινή Αναμονή Λήξης**\n\n"
                f"• **{gap_type_sums['evening']:.1f} ώρες** ({pct_eve:.0f}% του νεκρού χρόνου)\n\n"
                f"👉 *Αιτία: Τελευταία ραντεβού ολοκληρώθηκαν νωρίτερα από το κλείσιμο. Λύση: Πρόωρη αποχώρηση ή προσφορά happy hour.*"
            )


def render_shift_rebalancing_sandbox(sched_cap_matrix, demand_matrix, active_staff_grid, avg_h_cost=7.80):
    with st.container(border=True):
        st.subheader("🎛️ Live What-If Προσομοιωτής Ανακατάταξης Βαρδιών (Smart Roster Sandbox)", icon=":material/tune:")
        st.caption(
            "Δοκιμάστε **άμεσα και χωρίς ρίσκο** πώς θα άλλαζε το εβδομαδιαίο και μηνιαίο σας κέρδος αν κάνατε ανακατανομή των βαρδιών! "
            "Η προσομοίωση επανυπολογίζει σε πραγματικό χρόνο τις νέες νεκρές ώρες, την εξοικονόμηση μισθοδοσίας και τη νέα πληρότητα."
        )

        excess_matrix = sched_cap_matrix - demand_matrix
        daily_excess_sum = [float(np.sum(np.maximum(0.0, excess_matrix[d, :]))) for d in range(6)]
        daily_demand_sum = [float(np.sum(demand_matrix[d, :])) for d in range(6)]
        daily_sched_sum = [float(np.sum(sched_cap_matrix[d, :])) for d in range(6)]

        curr_tot_sched = sum(daily_sched_sum)
        curr_tot_dem = sum(daily_demand_sum)
        curr_tot_idle = sum(daily_excess_sum)
        curr_idle_cost_wk = curr_tot_idle * avg_h_cost
        curr_occ_pct = (curr_tot_dem / max(0.1, curr_tot_sched)) * 100.0

        col_cfg1, col_cfg2, col_cfg3 = st.columns([1.5, 1.5, 1.5], gap="medium")

        day_options = [
            f"{d_name} (+{daily_excess_sum[idx]:.1f}h πλεόνασμα)" if daily_excess_sum[idx] >= 3.0 else f"{d_name} (+{daily_excess_sum[idx]:.1f}h)"
            for idx, d_name in enumerate(DAYS_GR)
        ]
        max_excess_idx = int(np.argmax(daily_excess_sum))

        with col_cfg1:
            sel_target_day_lbl = st.selectbox(
                "1. Ημέρα προς Προσαρμογή",
                options=day_options,
                index=max_excess_idx,
                key="sandbox_day_picker"
            )
            sel_d_idx = day_options.index(sel_target_day_lbl)

        with col_cfg2:
            intervention_type = st.segmented_control(
                "2. Τύπος Παρέμβασης",
                options=[
                    "🌅 Κλιμακωτή Έναρξη (+1-3h αργότερα)",
                    "✂️ Μείωση Ωρών Βάρδιας",
                    "🔄 Μεταφορά σε Ημέρα Αιχμής"
                ],
                default="🌅 Κλιμακωτή Έναρξη (+1-3h αργότερα)",
                key="sandbox_intervention_type"
            )

        with col_cfg3:
            if "Κλιμακωτή" in intervention_type:
                hours_shifted = st.slider(
                    "Ώρες μετατόπισης έναρξης το πρωί (09:00-12:00)",
                    min_value=1.0, max_value=3.0, value=1.5, step=0.5,
                    key="sb_sl_morning_shift"
                )
                realloc_target = None
            elif "Μείωση" in intervention_type:
                hours_shifted = st.slider(
                    "Ώρες περικοπής πλεονάσματος",
                    min_value=1.0, max_value=6.0, value=2.0, step=0.5,
                    key="sb_sl_cut_hours"
                )
                realloc_target = None
            else:
                hours_shifted = st.slider(
                    "Ώρες προς μεταφορά",
                    min_value=1.0, max_value=5.0, value=2.0, step=0.5,
                    key="sb_sl_realloc_hours"
                )
                realloc_target = st.selectbox(
                    "Ημέρα Προορισμού",
                    options=["Παρασκευή", "Σάββατο", "Πέμπτη"],
                    index=0,
                    key="sb_realloc_target_day"
                )

        sim_sched = sched_cap_matrix.copy()

        if "Κλιμακωτή" in intervention_type:
            rem_h = hours_shifted
            for h_col in [0, 1, 2]:
                if rem_h <= 0:
                    break
                cut_amt = min(rem_h, max(0.0, sim_sched[sel_d_idx, h_col] - demand_matrix[sel_d_idx, h_col]))
                if cut_amt > 0:
                    sim_sched[sel_d_idx, h_col] -= cut_amt
                    rem_h -= cut_amt
            if rem_h > 0:
                sim_sched[sel_d_idx, 0] = max(0.0, sim_sched[sel_d_idx, 0] - rem_h)
        elif "Μείωση" in intervention_type:
            day_ex = sim_sched[sel_d_idx, :] - demand_matrix[sel_d_idx, :]
            sorted_h = np.argsort(-day_ex)
            rem_h = hours_shifted
            for h_col in sorted_h:
                if rem_h <= 0:
                    break
                cut_amt = min(rem_h, max(0.5, day_ex[h_col]))
                sim_sched[sel_d_idx, h_col] = max(0.0, sim_sched[sel_d_idx, h_col] - cut_amt)
                rem_h -= cut_amt
        else:
            day_ex = sim_sched[sel_d_idx, :] - demand_matrix[sel_d_idx, :]
            sorted_h = np.argsort(-day_ex)
            rem_h = hours_shifted
            for h_col in sorted_h:
                if rem_h <= 0:
                    break
                cut_amt = min(rem_h, max(0.5, day_ex[h_col]))
                sim_sched[sel_d_idx, h_col] = max(0.0, sim_sched[sel_d_idx, h_col] - cut_amt)
                rem_h -= cut_amt

            tgt_idx = DAYS_GR.index(realloc_target) if realloc_target in DAYS_GR else 4
            tgt_def = demand_matrix[tgt_idx, :] - sim_sched[tgt_idx, :]
            sorted_tgt_h = np.argsort(-tgt_def)
            add_h = hours_shifted
            for h_col in sorted_tgt_h:
                if add_h <= 0:
                    break
                sim_sched[tgt_idx, h_col] += min(1.0, add_h)
                add_h -= min(1.0, add_h)

        sim_excess = sim_sched - demand_matrix
        sim_daily_idle = [float(np.sum(np.maximum(0.0, sim_excess[d, :]))) for d in range(6)]
        sim_tot_idle = sum(sim_daily_idle)
        sim_tot_sched = float(np.sum(sim_sched))
        sim_idle_cost_wk = sim_tot_idle * avg_h_cost
        saved_idle_h_wk = max(0.0, curr_tot_idle - sim_tot_idle)
        saved_cost_wk = saved_idle_h_wk * avg_h_cost
        saved_cost_month = saved_cost_wk * 4.33
        sim_occ_pct = (curr_tot_dem / max(0.1, sim_tot_sched)) * 100.0

        r1, r2, r3, r4 = st.columns(4)
        r1.metric(
            "1. Νέες Νεκρές Ώρες / Εβδομάδα",
            f"{sim_tot_idle:.1f} h",
            f"-{saved_idle_h_wk:.1f} h μείωση",
            delta_color="normal",
            border=True
        )
        r2.metric(
            "2. Εβδομαδιαία Εξοικονόμηση",
            f"+{saved_cost_wk:,.2f} € / εβδ.",
            "Λιγότερη σπατάλη σε αδράνεια",
            delta_color="normal",
            border=True
        )
        r3.metric(
            "3. Μηνιαίο Καθαρό Κέρδος στην Τσέπη",
            f"+{saved_cost_month:,.2f} € / μήνα",
            f"+{saved_cost_month*12:,.0f} € ετησίως",
            delta_color="normal",
            border=True
        )
        r4.metric(
            "4. Νέα Πληρότητα Καταστήματος",
            f"{sim_occ_pct:.1f}%",
            f"{sim_occ_pct - curr_occ_pct:+.1f}% βελτίωση",
            delta_color="normal",
            border=True
        )

        fig_cmp = go.Figure()
        fig_cmp.add_trace(go.Bar(
            x=DAYS_GR,
            y=[daily_excess_sum[d] * avg_h_cost for d in range(6)],
            name="🔴 Τρέχον Κόστος Idle (€/ημέρα)",
            marker_color="#ef4444",
            text=[f"{daily_excess_sum[d]:.1f}h (-{daily_excess_sum[d]*avg_h_cost:.0f}€)" for d in range(6)],
            textposition="inside"
        ))
        fig_cmp.add_trace(go.Bar(
            x=DAYS_GR,
            y=[sim_daily_idle[d] * avg_h_cost for d in range(6)],
            name="🟢 Νέο Προσομοιωμένο Κόστος (€/ημέρα)",
            marker_color="#10b981",
            text=[f"{sim_daily_idle[d]:.1f}h (-{sim_daily_idle[d]*avg_h_cost:.0f}€)" for d in range(6)],
            textposition="inside"
        ))
        fig_cmp.update_layout(
            barmode="group",
            height=280,
            margin=dict(t=15, b=15, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(title="Κόστος Νεκρού Χρόνου (€)", ticksuffix=" €"),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0)
        )
        st.plotly_chart(fig_cmp, width="stretch")


def render_ai_shift_rebalancer(sched_cap_matrix, demand_matrix, ov_blocks, un_blocks, avg_h_cost, selected_gemini_model, ai_provider_choice, selected_thinking_level, gemini_api_key):
    with st.container(border=True):
        st.subheader("🤖 AI Smart Rebalancer: Πλάνο Νέου Εβδομαδιαίου Προγράμματος (Gemini 3.8 Flash High Thinking)", icon=":material/psychology:")
        st.caption(
            "Το AI CFO αναλύει συνδυαστικά τα ακριβή δεδομένα ζήτησης ανά ώρα, εντοπίζει τις κόκκινες ζώνες υπερστελέχωσης "
            "και συντάσσει **συγκεκριμένο νέο εβδομαδιαίο ωράριο ανά ημέρα & κανόνες κρατήσεων στο Treatwell**."
        )

        if "last_roster_plan" not in st.session_state:
            saved_plan = load_app_config_json("__LAST_ROSTER_REBALANCER_AI__")
            st.session_state["last_roster_plan"] = saved_plan if isinstance(saved_plan, dict) else None

        if st.button("🚀 Σύνταξη Νέου Βελτιστοποιημένου Προγράμματος Βαρδιών (AI CFO)", type="primary", icon=":material/auto_awesome:"):
            if not gemini_api_key:
                st.error(f"Συμπληρώστε πρώτα το API Key ({ai_provider_choice}) στην Πλαϊνή Μπάρα (Sidebar)!")
            elif GENAI_AVAILABLE or ai_provider_choice != "Google Gemini":
                with st.spinner(f"Το AI CFO ({selected_gemini_model} με Thinking: {selected_thinking_level}) σχεδιάζει το νέο πρόγραμμα βαρδιών..."):
                    try:
                        ai_roster_payload = {
                            "average_hourly_cost": round(float(avg_h_cost), 2),
                            "overstaffed_zones": ov_blocks[:5],
                            "understaffed_zones": un_blocks[:3],
                            "weekly_capacity_hours_by_day": {
                                DAYS_GR[d]: round(float(np.sum(sched_cap_matrix[d, :])), 1) for d in range(6)
                            },
                            "weekly_demand_hours_by_day": {
                                DAYS_GR[d]: round(float(np.sum(demand_matrix[d, :])), 1) for d in range(6)
                            },
                            "hourly_excess_by_day": {
                                DAYS_GR[d]: [round(float(sched_cap_matrix[d, h] - demand_matrix[d, h]), 1) for h in range(12)] for d in range(6)
                            }
                        }

                        prompt_roster = f"""
                        Είσαι ο Senior CFO & Operations Director ενός επιτυχημένου Nail Salon.
                        Έχεις μπροστά σου τα πραγματικά μαθηματικά δεδομένα ζήτησης (ραντεβού) και δυναμικότητας (βάρδιες τεχνιτριών) για κάθε ημέρα και ώρα:
                        ΔΕΔΟΜΕΝΑ: {json.dumps(ai_roster_payload, ensure_ascii=False)}

                        Στόχος σου είναι να ΕΞΑΛΕΙΨΕΙΣ τις νεκρές ώρες (idle time) και να αυξήσεις το καθαρό κέρδος στην τσέπη του ιδιοκτήτη.
                        Σύνταξε μία πρακτική, συγκεκριμένη έκθεση στα Ελληνικά με:
                        1. **Συγκεκριμένο Πρόγραμμα Νέων Βαρδιών (Ανά Ημέρα Δευτ-Σαβ):**
                           - Ποιες ώρες ακριβώς να ανοίγει η βάρδια κάθε τεχνίτριας (π.χ. Τρίτη: 1η τεχνίτρια 11:30-19:30 αντί για 09:30-17:30, 2η τεχνίτρια 13:00-21:00).
                           - Ποιες ημέρες να μειωθούν οι συνολικές ώρες και σε ποιες ημέρες αιχμής (Παρασκευή/Σάββατο) να μεταφερθούν.
                        2. **Κανόνας Κλειδώματος Ραντεβού στο Treatwell / Ημερολόγιο (Slot Packing Policy):**
                           - Πώς να κλείνονται τα ραντεβού (back-to-back, ελάχιστο κενό) ώστε να μην αφήνουν οι πελάτισσες ορφανά 30λεπτα.
                        3. **Οικονομική Αποτίμηση:**
                           - Πόσες νεκρές ώρες κερδίζονται ανά εβδομάδα.
                           - Ακριβής εκτίμηση μηνιαίας και ετήσιας εξοικονόμησης σε Ευρώ.
                        """

                        resp_txt, used_m = call_gemini_smart(
                            api_key=gemini_api_key,
                            contents=prompt_roster,
                            expect_json=False,
                            preferred_model=selected_gemini_model,
                            provider=ai_provider_choice,
                            thinking_level=selected_thinking_level
                        )
                        plan_obj = {
                            "model": used_m,
                            "ts": datetime.now().strftime("%d/%m/%Y %H:%M"),
                            "report_md": resp_txt
                        }
                        st.session_state["last_roster_plan"] = plan_obj
                        save_app_config_json("__LAST_ROSTER_REBALANCER_AI__", plan_obj)
                        st.toast("Το νέο πλάνο βαρδιών αποθηκεύτηκε μόνιμα στη βάση!", icon=":material/check_circle:")
                    except Exception as e:
                        st.error(f"Σφάλμα AI API: {e}")

        active_plan = st.session_state.get("last_roster_plan")
        if isinstance(active_plan, dict) and active_plan.get("report_md"):
            st.divider()
            st.info(
                f"📌 **Αποθηκευμένο Πλάνο AI Rebalancer** • Ημερομηνία: `{active_plan.get('ts', '-')}` • Μοντέλο: `{active_plan.get('model', '-')}`",
                icon=":material/bookmark_added:"
            )
            st.markdown(active_plan["report_md"])


# =====================================================================
# TAB 3: ΠΡΟΣΩΠΙΚΟ, IDLE TIME & ΒΕΛΤΙΣΤΟΠΟΙΗΤΗΣ ΒΑΡΔΙΩΝ
# =====================================================================
with tab_staff:
    if df_bookings_db.empty or df_tech_perf.empty:
        st.info("Δεν υπάρχουν δεδομένα προσωπικού για την επιλεγμένη περίοδο.")
    else:
        with st.container(border=True):
            st.subheader("💶 Ακτινογραφία Μισθοδοσίας Περιόδου (Καθαρά στην Τσέπη vs ΕΦΚΑ/ΦΜΥ vs Κόστος Εργοδότη)", icon=":material/payments:")
            pm1, pm2, pm3, pm4 = st.columns(4)
            pm1.metric(
                "1. Καθαρά στην Τσέπη Εργαζομένων",
                f"{total_net_pocket_payroll:,.2f} €",
                "Πληρωτέο ποσό στις εργαζόμενες",
                delta_color="off",
                border=True
            )
            pm2.metric(
                "2. ΕΦΚΑ (Εργαζ.+Εργοδ.) & ΦΜΥ",
                f"{total_efka_tax_payroll:,.2f} €",
                f"{(total_efka_tax_payroll / max(1.0, total_declared_payroll) * 100):.1f}% του συνολικού κόστους",
                delta_color="inverse",
                border=True
            )
            pm3.metric(
                "3. Προσωπικό Άδειας / Υποστήριξης",
                f"{support_payroll_cost:,.2f} €",
                "Άδεια / Καθαριότητα / Υποδοχή / Πρακτική",
                delta_color="off",
                border=True
            )
            pm4.metric(
                "4. Συνολικό Κόστος Εργοδότη",
                f"{total_declared_payroll:,.2f} €",
                f"{payroll_ratio_pct:.1f}% επί του Καθαρού Τζίρου",
                delta_color="inverse",
                border=True
            )

        st.space("small")

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
                st.subheader("👩‍🔧 Ταμείο, Μισθοδοσία & Παραγωγικότητα ανά Τεχνίτρια", icon=":material/badge:")
            with c_st_h2:
                staff_tbl_mode = st.segmented_control(
                    "Προβολή Πίνακα",
                    options=["Απλή Προβολή (9 Στήλες)", "Πλήρης Λογιστική Προβολή"],
                    default="Απλή Προβολή (9 Στήλες)",
                    key="staff_tbl_mode_ctrl"
                )

            if staff_tbl_mode.startswith("Απλή"):
                cols_to_show = [
                    "Τεχνίτρια", "Ραντεβού", "Ώρες Βάρδιας", "Παραγωγικές Ώρες",
                    "Πληρότητα (%)", "Καθαρά στην Τσέπη (€)", "ΕΦΚΑ & ΦΜΥ (€)", "Μισθοδοσία (€)", "Πραγματικό Ισοζύγιο (€)"
                ]
            else:
                cols_to_show = [
                    "Τεχνίτρια", "Ενεργές Εβδ.", "Ραντεβού", "Μικρά Ραντεβού (≤15')",
                    "Ώρες Βάρδιας", "Παραγωγικές Ώρες", "Νεκρές Ώρες (Idle)", "Πληρότητα (%)",
                    "Μικτός Τζίρος (€)", "Καθαρός Τζίρος (€)", "Προμήθειες TW (€)", "Υλικά (€)",
                    "Καθαρά στην Τσέπη (€)", "ΕΦΚΑ & ΦΜΥ (€)", "Μισθοδοσία (€)", "Κόστος Νεκρού Χρόνου (€)", "Πραγματικό Ισοζύγιο (€)"
                ]

            st.dataframe(
                df_tech_perf[cols_to_show],
                hide_index=True,
                width="stretch",
                column_config={
                    "Πληρότητα (%)": st.column_config.ProgressColumn("Πληρότητα %", min_value=0, max_value=100, format="%.1f%%"),
                    "Καθαρά στην Τσέπη (€)": st.column_config.NumberColumn("Καθαρά Τσέπης (€)", format="%.2f €"),
                    "ΕΦΚΑ & ΦΜΥ (€)": st.column_config.NumberColumn("ΕΦΚΑ & ΦΜΥ (€)", format="%.2f €"),
                    "Μισθοδοσία (€)": st.column_config.NumberColumn("Κόστος Εργοδότη (€)", format="%.2f €"),
                    "Κόστος Νεκρού Χρόνου (€)": st.column_config.NumberColumn("Κόστος Idle (€)", format="%.2f €"),
                    "Μικτός Τζίρος (€)": st.column_config.NumberColumn("Μικτός (€)", format="%.2f €"),
                    "Καθαρός Τζίρος (€)": st.column_config.NumberColumn("Καθαρός προ ΦΠΑ (€)", format="%.2f €"),
                    "Προμήθειες TW (€)": st.column_config.NumberColumn("TW Προμ. (€)", format="%.2f €"),
                    "Υλικά (€)": st.column_config.NumberColumn("Υλικά (€)", format="%.2f €"),
                    "Πραγματικό Ισοζύγιο (€)": st.column_config.NumberColumn("Καθαρό Ισοζύγιο (€)", format="%+.2f €")
                }
            )
            if support_staff_details and use_accounting_payroll and include_support_staff:
                with st.expander(f"📋 Ανάλυση Προσωπικού Άδειας / Υποστήριξης στην Περίοδο ({support_payroll_cost:,.2f} € συνολικό κόστος)"):
                    st.dataframe(pd.DataFrame(support_staff_details), hide_index=True, width="stretch")
            st.caption(
                "💡 **Πώς διαβάζεται το Καθαρό Ισοζύγιο (€):** Είναι ο Καθαρός Τζίρος (προ ΦΠΑ) που έφερε η τεχνίτρια, "
                "μείον τις προμήθειες Treatwell των ραντεβού της, μείον τα υλικά της, μείον ολόκληρο το εργοδοτικό κόστος μισθοδοσίας της (Καθαρά + ΕΦΚΑ + ΦΜΥ + Δώρα)."
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

        st.space("small")
        render_daily_gantt_idle_detector(df_comp, db_schedules, staff_hourly_lookup, use_rolling_schedule)

        st.space("small")
        render_shift_rebalancing_sandbox(sched_cap_matrix, demand_matrix, active_staff_grid, avg_h_cost)

        st.space("small")
        render_ai_shift_rebalancer(
            sched_cap_matrix,
            demand_matrix,
            ov_blocks,
            un_blocks,
            avg_h_cost,
            selected_gemini_model,
            ai_provider_choice,
            selected_thinking_level,
            gemini_api_key
        )

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
                                    "staff_name": "Reception / Ταμείο", "customer_name": "ΠΕΛΑΤΗΣ ΛΙΑΝΙΚΗΣ",
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
                                provider=ai_provider_choice,
                                thinking_level=selected_thinking_level
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
    admin_sub_nav_options = [
        "📅 1. Εβδομαδιαίες Βάρδιες (Editor, JSON & AI Screenshots)",
        "📥 2. Εισαγωγή Ραντεβού (Multi-CSV Importer)",
        "💳 3. Έξοδα & Τιμολόγια (Ημερολόγιο Παραστατικών & Πάγια)",
        "👥 4. Μισθοδοσία Προσωπικού (Καταστάσεις Λογιστή)",
        "💅 5. Κατάλογος Υπηρεσιών & Υλικών"
    ]
    if st.session_state.get("admin_sub_nav") not in admin_sub_nav_options:
        st.session_state["admin_sub_nav"] = admin_sub_nav_options[0]

    admin_section = st.segmented_control(
        "Επιλέξτε Εργαλείο Διαχείρισης",
        options=admin_sub_nav_options,
        default=admin_sub_nav_options[0],
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
                                  "staff_name": "Ονοματεπώνυμο",
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
                                        provider=ai_provider_choice,
                                        thinking_level=selected_thinking_level
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
                            df_staff_db, df_services_db, df_expenses_db, df_bookings_db, db_schedules, db_app_settings, df_payroll_db, df_expenses_ledger_db = load_all_db_data()

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

    elif admin_section.startswith("💳") or admin_section.startswith("💶 3"):
        df_all_exp_ledger = get_expenses_ledger_df()

        with st.container(border=True):
            st.subheader("💳 Κέντρο Διαχείρισης & Καταχώρησης Εξόδων (Ημερολόγιο Παραστατικών — `expenses_ledger`)", icon=":material/receipt_long:")
            st.caption(
                "Καταχωρήστε και διαχειριστείτε όλα τα παραστατικά εξόδων της επιχείρησης (ενοίκια, λογαριασμοί, υλικά, λογιστής, συντηρήσεις). "
                "Όταν υπάρχουν καταχωρημένα έξοδα για μία περίοδο, το σύστημα χρησιμοποιεί αυτόματα τα **πραγματικά έξοδα** στο P&L, υπολογίζει τον **ΦΠΑ Εισροών** "
                "και την **πραγματική ταμειακή εκροή**, αντικαθιστώντας τις προϋπολογιστικές εκτιμήσεις."
            )

            # Φίλτρα προβολής
            c_f_yr, c_f_cat, c_f_nat = st.columns([1, 1.5, 1], gap="medium")
            with c_f_yr:
                exp_years = ["Όλα τα Έτη"]
                if not df_all_exp_ledger.empty and "expense_date" in df_all_exp_ledger.columns:
                    avail_yrs = sorted(list(set(pd.to_datetime(df_all_exp_ledger["expense_date"]).dt.year.dropna().astype(int))), reverse=True)
                    exp_years += [str(y) for y in avail_yrs]
                else:
                    exp_years += ["2026", "2025", "2024"]
                sel_exp_year = st.selectbox("Φίλτρο Έτους:", options=exp_years, index=0, key="sel_exp_year")

            with c_f_cat:
                sel_exp_cat = st.selectbox("Φίλτρο Κατηγορίας:", options=["Όλες οι Κατηγορίες"] + EXPENSE_MAIN_CATEGORIES, index=0, key="sel_exp_cat")

            with c_f_nat:
                sel_exp_nat = st.selectbox("Φίλτρο Φύσης:", options=["Όλες"] + EXPENSE_NATURES, index=0, key="sel_exp_nat")

            # Εφαρμογή φίλτρων
            df_filtered_exp = df_all_exp_ledger.copy()
            if not df_filtered_exp.empty and "expense_date" in df_filtered_exp.columns:
                if sel_exp_year != "Όλα τα Έτη":
                    df_filtered_exp = df_filtered_exp[pd.to_datetime(df_filtered_exp["expense_date"]).dt.year == int(sel_exp_year)]
                if sel_exp_cat != "Όλες οι Κατηγορίες":
                    df_filtered_exp = df_filtered_exp[df_filtered_exp["main_category"] == sel_exp_cat]
                if sel_exp_nat != "Όλες":
                    df_filtered_exp = df_filtered_exp[df_filtered_exp["expense_nature"] == sel_exp_nat]

            # Υπολογισμός KPI Φιλτραρισμένων Εξόδων
            tot_exp_cnt = len(df_filtered_exp)
            tot_exp_net = float(df_filtered_exp["net_amount"].sum()) if not df_filtered_exp.empty else 0.0
            tot_exp_vat = float(df_filtered_exp["vat_amount"].sum()) if not df_filtered_exp.empty else 0.0
            tot_exp_gross = float(df_filtered_exp["gross_amount"].sum()) if not df_filtered_exp.empty else 0.0
            tot_exp_cogs = float(df_filtered_exp[df_filtered_exp["main_category"] == "Κόστος Υλικών & Εμπορευμάτων (COGS)"]["net_amount"].sum()) if not df_filtered_exp.empty else 0.0

            kpi_e1, kpi_e2, kpi_e3, kpi_e4 = st.columns(4)
            kpi_e1.metric("1. Καθαρή Αξία Εξόδων", f"{tot_exp_net:,.2f} €", f"{tot_exp_cnt:,} παραστατικά", delta_color="off", border=True)
            kpi_e2.metric("2. ΦΠΑ Εισροών", f"{tot_exp_vat:,.2f} €", "Προς έκπτωση από ΦΠΑ εσόδων", delta_color="off", border=True)
            kpi_e3.metric("3. Συνολική Ταμειακή Εκροή", f"{tot_exp_gross:,.2f} €", "Πραγματικά χρήματα ταμείου", delta_color="inverse", border=True)
            kpi_e4.metric("4. Κόστος Υλικών (COGS)", f"{tot_exp_cogs:,.2f} €", f"{(tot_exp_cogs / tot_exp_net * 100):.1f}% των εξόδων" if tot_exp_net > 0 else "0.0%", delta_color="off", border=True)

        st.space("small")

        # ⚡ ΓΡΗΓΟΡΗ ΚΑΤΑΧΩΡΗΣΗ ΝΕΟΥ ΕΞΟΔΟΥ
        with st.expander("⚡ Γρήγορη Καταχώρηση Νέου Εξόδου / Τιμολογίου", expanded=True):
            st.caption("Συμπληρώστε τα στοιχεία του παραστατικού. Το ποσό ΦΠΑ και το σύνολο υπολογίζονται αυτόματα:")
            c_qa1, c_qa2, c_qa3, c_qa4 = st.columns(4, gap="small")
            with c_qa1:
                qa_date = st.date_input("Ημερομηνία Εξόδου", value=date.today(), key="qa_exp_date")
            with c_qa2:
                qa_cat = st.selectbox("Βασική Κατηγορία", options=EXPENSE_MAIN_CATEGORIES, index=0, key="qa_exp_cat")
            with c_qa3:
                qa_subcat = st.text_input("Υποκατηγορία / Είδος", placeholder="π.χ. Ημιμόνιμα βερνίκια, Ρεύμα", key="qa_exp_subcat")
            with c_qa4:
                qa_vendor = st.text_input("Προμηθευτής / Εκδότης", placeholder="π.χ. ΔΕΗ, ProNails, Ενοικιαστής", key="qa_exp_vendor")

            c_qa5, c_qa6, c_qa7, c_qa8 = st.columns(4, gap="small")
            with c_qa5:
                qa_net = st.number_input("Καθαρή Αξία (€ προ ΦΠΑ)", min_value=0.0, step=10.0, format="%.2f", key="qa_exp_net")
            with c_qa6:
                qa_vat_rate = st.selectbox("Συντελεστής ΦΠΑ (%)", options=EXPENSE_VAT_RATES, index=0, format_func=lambda x: f"{x:.0f}%", key="qa_exp_vat_rate")
            with c_qa7:
                qa_nature = st.selectbox("Φύση Εξόδου", options=EXPENSE_NATURES, index=0, key="qa_exp_nature")
            with c_qa8:
                qa_pay_m = st.selectbox("Τρόπος Πληρωμής", options=EXPENSE_PAYMENT_METHODS, index=0, key="qa_exp_pay_m")

            c_qa9, c_qa10 = st.columns([1, 2], gap="small")
            with c_qa9:
                qa_inv = st.text_input("Αριθμός Παραστατικού / Τιμολογίου", placeholder="π.χ. ΤΔΑ-1025, Απόδειξη 44", key="qa_exp_inv")
            with c_qa10:
                qa_notes = st.text_input("Σημειώσεις / Αιτιολογία", placeholder="π.χ. Προμήθεια υλικών Οκτωβρίου", key="qa_exp_notes")

            calc_vat = round(qa_net * (qa_vat_rate / 100.0), 2)
            calc_gross = round(qa_net + calc_vat, 2)
            st.info(
                f"💡 **Αυτόματος Υπολογισμός:** Καθαρή Αξία: **{qa_net:,.2f} €** + ΦΠΑ ({qa_vat_rate:.0f}%): **{calc_vat:,.2f} €** "
                f"= **Συνολική Αξία Πληρωμής: {calc_gross:,.2f} €**",
                icon=":material/calculate:"
            )

            if st.button("➕ Καταχώρηση Εξόδου στο Ημερολόγιο", type="primary", width="stretch", key="btn_qa_submit"):
                if qa_net <= 0 and calc_gross <= 0:
                    st.error("Παρακαλούμε συμπληρώστε ένα ποσό εξόδου μεγαλύτερο του 0!")
                else:
                    new_exp_rec = {
                        "expense_date": qa_date,
                        "vendor_name": qa_vendor,
                        "main_category": qa_cat,
                        "sub_category": qa_subcat,
                        "expense_nature": qa_nature,
                        "net_amount": qa_net,
                        "vat_rate": qa_vat_rate,
                        "vat_amount": calc_vat,
                        "gross_amount": calc_gross,
                        "payment_method": qa_pay_m,
                        "invoice_number": qa_inv,
                        "notes": qa_notes
                    }
                    save_expense_entry(new_exp_rec)
                    st.toast(f"✅ Το έξοδο {qa_vendor} ({calc_gross:,.2f} €) καταχωρήθηκε επιτυχώς!", icon=":material/check_circle:")
                    refresh_data()

        st.space("small")

        # 📑 ΑΝΑΛΥΤΙΚΟ ΗΜΕΡΟΛΟΓΙΟ ΕΞΟΔΩΝ (DATA EDITOR)
        with st.container(border=True):
            st.markdown("##### 📑 Πλήρες Ημερολόγιο Παραστατικών & Εξόδων (`expenses_ledger`)")
            st.caption("Μπορείτε να τροποποιήσετε απευθείας οποιοδήποτε κελί, να προσθέσετε νέες γραμμές ή να διαγράψετε επιλεγμένες:")

            df_to_edit = df_filtered_exp.copy()
            if df_to_edit.empty:
                df_to_edit = pd.DataFrame(columns=DEFAULT_EXPENSE_COLUMNS)

            ed_exp_ledger = st.data_editor(
                df_to_edit,
                hide_index=True,
                num_rows="dynamic",
                width="stretch",
                key="ed_expenses_ledger_grid",
                column_config={
                    "id": st.column_config.NumberColumn("ID", disabled=True),
                    "expense_date": st.column_config.DateColumn("Ημερομηνία", required=True),
                    "vendor_name": st.column_config.TextColumn("Προμηθευτής / Εκδότης"),
                    "main_category": st.column_config.SelectboxColumn("Βασική Κατηγορία", options=EXPENSE_MAIN_CATEGORIES, required=True),
                    "sub_category": st.column_config.TextColumn("Υποκατηγορία / Είδος"),
                    "expense_nature": st.column_config.SelectboxColumn("Φύση Εξόδου", options=EXPENSE_NATURES),
                    "net_amount": st.column_config.NumberColumn("Καθαρή Αξία (€)", format="%.2f €", required=True),
                    "vat_rate": st.column_config.SelectboxColumn("ΦΠΑ (%)", options=EXPENSE_VAT_RATES),
                    "vat_amount": st.column_config.NumberColumn("Ποσό ΦΠΑ (€)", format="%.2f €"),
                    "gross_amount": st.column_config.NumberColumn("Σύνολο με ΦΠΑ (€)", format="%.2f €"),
                    "payment_method": st.column_config.SelectboxColumn("Τρόπος Πληρωμής", options=EXPENSE_PAYMENT_METHODS),
                    "invoice_number": st.column_config.TextColumn("Αρ. Παραστατικού"),
                    "notes": st.column_config.TextColumn("Σημειώσεις")
                }
            )

            c_exp_btn1, c_exp_btn2, c_exp_btn3 = st.columns([1.5, 1, 1], gap="medium")
            with c_exp_btn1:
                if st.button("💾 Αποθήκευση Αλλαγών Ημερολογίου Εξόδων", type="primary", width="stretch", key="btn_save_exp_ledger"):
                    saved_n = save_expenses_ledger_df(ed_exp_ledger)
                    st.toast(f"✅ Αποθηκεύτηκαν {saved_n} εγγραφές εξόδων στη Supabase!", icon=":material/check_circle:")
                    refresh_data()
            with c_exp_btn2:
                csv_exp_data = df_filtered_exp.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "📥 Εξαγωγή σε CSV",
                    data=csv_exp_data,
                    file_name=f"expenses_ledger_{sel_exp_year}.csv",
                    mime="text/csv",
                    width="stretch",
                    key="btn_dl_exp_csv"
                )
            with c_exp_btn3:
                if st.button("🔄 Ανανέωση από τη Βάση", width="stretch", key="btn_refresh_exp_db"):
                    st.toast("Ανανεώθηκαν τα δεδομένα εξόδων!", icon=":material/refresh:")
                    refresh_data()

        st.space("small")

        # 📥 ΜΑΖΙΚΗ ΕΙΣΑΓΩΓΗ ΑΠΟ CSV / EXCEL
        with st.expander("📥 Μαζική Εισαγωγή Εξόδων από CSV / Excel (Τραπεζικό Statement ή Αρχείο Λογιστή)", expanded=False):
            st.caption(
                "Ανεβάστε αρχείο `.csv` ή `.xlsx` με τιμολόγια ή έξοδα. "
                "Το σύστημα αναγνωρίζει αυτόματα τις στήλες (Ημερομηνία, Προμηθευτής, Καθαρή Αξία, ΦΠΑ, Σύνολο κ.λπ.) "
                "και υπολογίζει αυτόματα όσα ποσά λείπουν."
            )
            up_exp_file = st.file_uploader("Επιλογή αρχείου CSV ή Excel", type=["csv", "xlsx"], key="up_exp_bulk_file")
            if up_exp_file is not None:
                try:
                    df_bulk_parsed = parse_expenses_import_file(up_exp_file)
                    if not df_bulk_parsed.empty:
                        st.success(f"✅ Εντοπίστηκαν **{len(df_bulk_parsed):,} έγκυρες εγγραφές εξόδων** συνολικής αξίας **{df_bulk_parsed['gross_amount'].sum():,.2f} €**!")
                        st.dataframe(df_bulk_parsed, hide_index=True, width="stretch")
                        if st.button("🚀 Εισαγωγή Όλων των Εξόδων στο Ημερολόγιο", type="primary", width="stretch", key="btn_confirm_bulk_exp"):
                            saved_bulk = save_expenses_ledger_df(df_bulk_parsed)
                            st.toast(f"✅ Εισήχθησαν {saved_bulk} έξοδα στη βάση!", icon=":material/check_circle:")
                            refresh_data()
                    else:
                        st.warning("Δεν βρέθηκαν έγκυρες γραμμές εξόδων στο αρχείο. Ελέγξτε τις στήλες του αρχείου.")
                except Exception as e_exp_up:
                    st.error(f"Σφάλμα ανάγνωσης αρχείου: {e_exp_up}")

        st.space("small")

        # 🏢 ΜΗΝΙΑΙΟΣ ΠΡΟΫΠΟΛΟΓΙΣΜΟΣ ΠΑΓΙΩΝ (EXPENSES_LOG)
        with st.expander("🏢 Μηνιαίος Προϋπολογισμός Παγίων (`expenses_log` — Εφεδρικός Προϋπολογισμός)", expanded=False):
            st.caption(
                "Ο προϋπολογισμός παγίων χρησιμοποιείται ως εφεδρικό μέτρο (fallback) για περιόδους ή μήνες "
                "όπου δεν έχουν καταχωρηθεί ακόμα αναλυτικά παραστατικά στο ημερολόγιο εξόδων."
            )
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
            if st.button("💾 Αποθήκευση Προϋπολογισμού Παγίων", type="primary", width="stretch", key="btn_save_expenses"):
                supabase.table("expenses_log").delete().neq("expense_month", "0000").execute()
                supabase.table("expenses_log").insert(ed_exp.to_dict(orient="records")).execute()
                st.toast("Αποθηκεύτηκε ο προϋπολογισμός παγίων!", icon=":material/check_circle:")
                refresh_data()

    elif admin_section.startswith("👥") or admin_section.startswith("💶 4"):
        df_curr_payroll = get_payroll_profiles_df()

        with st.container(border=True):
            st.subheader("📑 Κέντρο Διοίκησης Μισθοδοσίας & Κοστολόγησης Προσωπικού (Επίσημες Μισθοδοτικές Καταστάσεις)", icon=":material/badge:")
            st.caption(
                "Τα ποσά προέρχονται απευθείας από τις **4 επίσημες μισθοδοτικές καταστάσεις του λογιστή** (2024, 2025, 01-08/2026, 09/2026). "
                "Μπορείτε να τροποποιήσετε οποιονδήποτε μισθό, καθαρά τσέπης, ΕΦΚΑ, ωρομίσθιο ή ιστορικό κόστος και να πατήσετε **Αποθήκευση** "
                "ώστε να ενημερωθεί άμεσα ολόκληρο το P&L, η σύγκριση περιόδων και οι δείκτες κερδοφορίας."
            )

            # Υπολογισμός συνολικών δεικτών τρέχοντος μισθολογίου
            curr_active_techs = df_curr_payroll[df_curr_payroll["is_active"] & (df_curr_payroll["role_type"] == "Τεχνίτρια")]
            curr_active_all = df_curr_payroll[df_curr_payroll["is_active"] & (df_curr_payroll["role_type"].isin(["Τεχνίτρια", "Άδεια / Υποστήριξη"]))]
            tot_reg_cost = float(curr_active_all["monthly_employer_regular"].sum())
            tot_reg_net = float(curr_active_all["monthly_net_pocket"].sum())
            tot_reg_efka = float(curr_active_all["monthly_efka_tax"].sum())
            tot_full_14 = float(curr_active_all["monthly_employer_full_14"].sum())

            tot_c2024 = float(df_curr_payroll["cost_2024"].sum())
            tot_n2024 = float(df_curr_payroll["net_2024"].sum())
            tot_c2025 = float(df_curr_payroll["cost_2025"].sum())
            tot_n2025 = float(df_curr_payroll["net_2025"].sum())
            tot_c2026_8m = float(df_curr_payroll["cost_2026_jan_aug"].sum())
            tot_c2026_sep = float(df_curr_payroll["cost_2026_sep"].sum())

            kpi_p1, kpi_p2, kpi_p3, kpi_p4, kpi_p5 = st.columns(5)
            kpi_p1.metric(
                "1. Τρέχον Τακτικό / Μήνα",
                f"{tot_reg_cost:,.2f} €",
                f"Καθαρά Τσέπης: {tot_reg_net:,.2f} € | ΕΦΚΑ: {tot_reg_efka:,.2f} €",
                delta_color="off",
                border=True
            )
            kpi_p2.metric(
                "2. Πλήρες με Δώρα (×14/12)",
                f"{tot_full_14:,.2f} € / μήνα",
                f"+{(tot_full_14 - tot_reg_cost):,.2f} € πρόβλεψη δώρων/αδείας",
                delta_color="off",
                border=True
            )
            kpi_p3.metric(
                "3. Επίσημο 2024 (Λογιστής)",
                f"{tot_c2024:,.2f} €",
                f"Καθαρά: {tot_n2024:,.2f} € (ΕΦΚΑ+ΦΜΥ: {(tot_c2024 - tot_n2024):,.2f} €)",
                delta_color="off",
                border=True
            )
            kpi_p4.metric(
                "4. Επίσημο 2025 (Λογιστής)",
                f"{tot_c2025:,.2f} €",
                f"Καθαρά: {tot_n2025:,.2f} € (ΕΦΚΑ+ΦΜΥ: {(tot_c2025 - tot_n2025):,.2f} €)",
                delta_color="off",
                border=True
            )
            kpi_p5.metric(
                "5. Επίσημο 2026 (Ιαν–Σεπτ)",
                f"{(tot_c2026_8m + tot_c2026_sep):,.2f} €",
                f"8μηνο: {tot_c2026_8m:,.0f} € | Σεπτ: {tot_c2026_sep:,.0f} €",
                delta_color="off",
                border=True
            )

            st.space("small")
            auto_recalc_chk = st.checkbox(
                "⚡ Αυτόματος επανυπολογισμός Πλήρους Μηνιαίου Κόστους (×14/12) & Ωρομισθίου όταν αλλάζετε Καθαρά Τσέπης, ΕΦΚΑ ή Ώρες/Εβδομάδα",
                value=True,
                key="chk_auto_recalc_payroll",
                help="Όταν είναι ενεργό, υπολογίζει αυτόματα: Τακτικό Κόστος = Καθαρά + ΕΦΚΑ, Πλήρες Κόστος = Τακτικό × 14/12, και Ωρομίσθιο = Πλήρες Κόστος / Μηνιαίες Ώρες."
            )

            ed_payroll_df = st.data_editor(
                df_curr_payroll,
                hide_index=True,
                num_rows="dynamic",
                width="stretch",
                key="ed_payroll_grid",
                column_config={
                    "staff_name": st.column_config.TextColumn("Εργαζόμενος", required=True),
                    "payroll_code": st.column_config.TextColumn("Κωδικός Μισθοδοσίας (ΑΠΔ / ΙΚΑ)"),
                    "role_type": st.column_config.SelectboxColumn("Ρόλος", options=["Τεχνίτρια", "Άδεια / Υποστήριξη", "Ιδιοκτήτης / Διοίκηση"], required=True),
                    "is_active": st.column_config.CheckboxColumn("Ενεργός Σήμερα"),
                    "weekly_hours_declared": st.column_config.NumberColumn("Ώρες/Εβδ.", format="%.1f h"),
                    "nominal_gross": st.column_config.NumberColumn("Μικτός Σύμβασης (€)", format="%.2f €"),
                    "monthly_net_pocket": st.column_config.NumberColumn("Καθαρά Τσέπης / Μήνα (€)", format="%.2f €"),
                    "monthly_efka_tax": st.column_config.NumberColumn("ΕΦΚΑ & ΦΜΥ / Μήνα (€)", format="%.2f €"),
                    "monthly_employer_regular": st.column_config.NumberColumn("Τακτικό Κόστος Εργοδότη (€)", format="%.2f €"),
                    "monthly_employer_full_14": st.column_config.NumberColumn("Πλήρες με Δώρα (×14/12) (€)", format="%.2f €"),
                    "hourly_cost": st.column_config.NumberColumn("Ωρομίσθιο (€/h)", format="%.2f €/h"),
                    "cost_2024": st.column_config.NumberColumn("Κόστος 2024 (€)", format="%.2f €"),
                    "net_2024": st.column_config.NumberColumn("Καθαρά 2024 (€)", format="%.2f €"),
                    "cost_2025": st.column_config.NumberColumn("Κόστος 2025 (€)", format="%.2f €"),
                    "net_2025": st.column_config.NumberColumn("Καθαρά 2025 (€)", format="%.2f €"),
                    "cost_2026_jan_aug": st.column_config.NumberColumn("Κόστος 01-08/2026 (€)", format="%.2f €"),
                    "net_2026_jan_aug": st.column_config.NumberColumn("Καθαρά 01-08/2026 (€)", format="%.2f €"),
                    "cost_2026_sep": st.column_config.NumberColumn("Κόστος 09/2026 (€)", format="%.2f €"),
                    "net_2026_sep": st.column_config.NumberColumn("Καθαρά 09/2026 (€)", format="%.2f €"),
                    "notes": st.column_config.TextColumn("Σημειώσεις")
                }
            )

            c_btn_p1, c_btn_p2 = st.columns([1.5, 1.5], gap="medium")
            with c_btn_p1:
                if st.button("💾 Αποθήκευση Μητρώου Μισθοδοσίας & Ενημέρωση P&L", type="primary", width="stretch", key="btn_save_payroll_prof"):
                    save_payroll_profiles_df(ed_payroll_df, auto_recalc_rates=auto_recalc_chk)
                    st.toast("✅ Το μητρώο μισθοδοσίας και τα ωρομίσθια αποθηκεύτηκαν επιτυχώς στη βάση!", icon=":material/check_circle:")
                    refresh_data()
            with c_btn_p2:
                if st.button("🔄 Ανανέωση από τη Βάση Δεδομένων", width="stretch", key="btn_reset_payroll_prof"):
                    st.toast("Ανανεώθηκαν τα δεδομένα μισθοδοσίας από τη βάση!", icon=":material/refresh:")
                    refresh_data()

    else:
        with st.container(border=True):
            st.subheader("💅 Κοστολόγηση Υπηρεσιών & Προϊόντων Λιανικής (`config_services`)", icon=":material/spa:")
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
            if st.button("💾 Αποθήκευση Καταλόγου Υπηρεσιών & Υλικών", type="primary", width="stretch", key="btn_save_services_cat"):
                supabase.table("config_services").upsert(ed_srv.to_dict(orient="records")).execute()
                st.toast("Αποθηκεύτηκε ο κατάλογος υπηρεσιών!", icon=":material/check_circle:")
                refresh_data()