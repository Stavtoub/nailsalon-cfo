# -*- coding: utf-8 -*-
"""
Schedule Screenshot & Image Ingestion for NailSalon CFO
Uses Google Gemini 3.5 Flash Lite Vision to extract weekly rosters and sync to Supabase.
"""

import os
import sys
import re
import glob
import json
import shutil
from datetime import datetime, timedelta
import pandas as pd
from google import genai
from google.genai import types
from supabase import create_client, Client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DROP_DIR = r"/home/tos/Desktop/Schedules_Drop"
ARCHIVE_DIR = os.path.join(DROP_DIR, "Archive")
SECRETS_PATH = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")

DAYS_GR = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο"]

def load_secrets():
    secrets = {}
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    secrets[k.strip()] = v.strip().strip('"').strip("'")
    return secrets

def get_supabase_client() -> Client:
    sec = load_secrets()
    url = sec.get("SUPABASE_URL")
    key = sec.get("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in secrets.toml")
    return create_client(url, key)

def get_gemini_client() -> genai.Client:
    sec = load_secrets()
    api_key = sec.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Missing GEMINI_API_KEY in secrets.toml")
    return genai.Client(api_key=api_key)

def normalize_to_monday_str(date_str: str) -> str:
    try:
        dt = pd.to_datetime(date_str, dayfirst=not re.match(r'^\d{4}-', str(date_str).strip()), errors="coerce")
        if pd.isna(dt):
            dt = pd.to_datetime(date_str, errors="coerce")
        if pd.notna(dt):
            mon_d = dt.date() - timedelta(days=dt.date().weekday())
            return mon_d.strftime("%Y-%m-%d")
    except Exception:
        pass
    return datetime.now().strftime("%Y-%m-%d")

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

def clean_json_response(raw_text: str):
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', str(raw_text).strip(), flags=re.MULTILINE)
    return json.loads(cleaned)

def extract_schedule_from_image(img_bytes: bytes, mime_type: str = "image/png"):
    client = get_gemini_client()
    
    prompt = """
    Διάβασε τον πίνακα εβδομαδιαίου προγράμματος βαρδιών από την εικόνα (Treatwell Connect / Πρόγραμμα Καταστήματος).
    
    Κανόνες:
    1. Αγνόησε τη γραμμή "Ώρες λειτουργίας καταστήματος".
    2. Αντιστοίχισε τα ονόματα των τεχνιτριών κατά προτίμηση στα επίσημα ονόματα της ομάδας:
       [Νάντια, Κατερίνα, Κατερίνα Π., Δόμνα, Δημήτρης, Αντιγόνη, Νάνσυ, Άννα, Μιχριμπάν, Θεοδώρα].
    3. Αν μια ημέρα (π.χ. Δευτέρα) είναι κενή (ρεπό ή χωρίς ωράριο), βάλε "" στην αντίστοιχη ημέρα και ΜΗΝ μετατοπίζεις τις υπόλοιπες ημέρες.
    4. Βρες την ημερομηνία της Δευτέρας της εβδομάδας ("monday_date": "YYYY-MM-DD").
    
    Επέστρεψε ΑΠΟΚΛΕΙΣΤΙΚΑ έγκυρο JSON (χωρίς άλλα σχόλια) με την εξής μορφή:
    {
      "monday_date": "YYYY-MM-DD",
      "schedule": [
        {
          "staff_name": "Νάντια",
          "Δευτέρα": "17:30 - 20:30",
          "Τρίτη": "",
          "Τετάρτη": "17:30 - 20:30",
          "Πέμπτη": "18:00 - 20:00",
          "Παρασκευή": "17:30 - 19:30",
          "Σάββατο": "09:00 - 16:00"
        }
      ]
    }
    """

    img_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
    
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[img_part, prompt]
    )
    
    raw_text = response.text.strip()
    return clean_json_response(raw_text)

def save_schedule_to_supabase(monday_str: str, schedule_rows: list):
    supabase = get_supabase_client()
    clean_mon = normalize_to_monday_str(monday_str)
    week_key = f"WEEK_{clean_mon}"
    cleaned_rows = deduplicate_and_clean_schedule_rows(schedule_rows)
    
    payload = {
        "service_name": week_key,
        "category": "__SHIFT_SCHEDULE__",
        "base_duration": 0,
        "gross_price": 0.0,
        "material_cost_estimate": 0.0,
        "price_variants_json": json.dumps(cleaned_rows, ensure_ascii=False)
    }
    
    supabase.table("config_services").upsert([payload]).execute()
    
    # Calculate summary metrics
    staff_summary = []
    total_hours = 0.0
    for r in cleaned_rows:
        s_name = r.get("staff_name", "")
        h = sum(calc_shift_hours(r.get(d, "")) for d in DAYS_GR)
        total_hours += h
        if h > 0:
            staff_summary.append({"name": s_name, "hours": round(h, 1)})
            
    return {
        "week_key": week_key,
        "monday_date": clean_mon,
        "total_staff": len(staff_summary),
        "total_hours": round(total_hours, 1),
        "staff_details": staff_summary,
        "raw_rows": cleaned_rows
    }

def process_single_image(image_path: str):
    if not os.path.exists(image_path):
        return {"status": "error", "message": f"File not found: {image_path}"}
        
    ext = os.path.splitext(image_path)[1].lower()
    mime_type = "image/png"
    if ext in [".jpg", ".jpeg"]:
        mime_type = "image/jpeg"
    elif ext == ".webp":
        mime_type = "image/webp"
        
    with open(image_path, "rb") as f:
        img_bytes = f.read()
        
    parsed = extract_schedule_from_image(img_bytes, mime_type)
    mon_raw = parsed.get("monday_date", datetime.now().strftime("%Y-%m-%d"))
    sched = parsed.get("schedule", [])
    
    if not sched:
        return {"status": "error", "message": "No shifts found in image"}
        
    res = save_schedule_to_supabase(mon_raw, sched)
    res["status"] = "success"
    res["source_file"] = os.path.basename(image_path)
    return res

def process_drop_folder():
    os.makedirs(DROP_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    
    images = []
    for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
        images.extend(glob.glob(os.path.join(DROP_DIR, ext)))
        
    if not images:
        return {"status": "no_files", "message": f"No image files found in {DROP_DIR}"}
        
    results = []
    for img_path in images:
        fname = os.path.basename(img_path)
        try:
            res = process_single_image(img_path)
            if res.get("status") == "success":
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                shutil.move(img_path, os.path.join(ARCHIVE_DIR, f"{ts}_{fname}"))
            results.append(res)
        except Exception as e:
            results.append({"status": "error", "file": fname, "error": str(e)})
            
    return {"status": "success", "processed_count": len(results), "details": results}

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--image" and len(sys.argv) > 2:
        out = process_single_image(sys.argv[2])
    else:
        out = process_drop_folder()
        
    print(json.dumps(out, ensure_ascii=False))
