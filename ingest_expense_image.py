# -*- coding: utf-8 -*-
"""
Expense & Invoice Image Ingestion for NailSalon CFO
Uses Google Gemini 3.5 Flash Lite Vision to extract receipts, invoices, and expense notes,
verifies duplicates, and syncs directly into Supabase expenses_ledger.
"""

import os
import sys
import re
import json
import shutil
from datetime import datetime, date
import pandas as pd
from google import genai
from google.genai import types
from supabase import create_client, Client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DROP_DIR = r"/home/tos/Desktop/Expenses_Drop"
ARCHIVE_DIR = os.path.join(DROP_DIR, "Archive")
SECRETS_PATH = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")

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

def clean_json_response(raw_text: str):
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', str(raw_text).strip(), flags=re.MULTILINE)
    return json.loads(cleaned)

def analyze_document_image(img_bytes: bytes, mime_type: str = "image/png"):
    """
    Unified Multimodal Vision Engine:
    Determines if an image is a SCHEDULE (weekly roster) or an EXPENSE (receipt/invoice/bill),
    and extracts structured JSON accordingly.
    """
    client = get_gemini_client()
    today_str = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""
    Είσαι ο εξειδικευμένος AI CFO και Ορκωτός Ελεγκτής του Nail Salon.
    Ανάλυσε την εικόνα που σου δόθηκε και προσδιόρισε πρώτα τον τύπο του εγγράφου:
    1. "SCHEDULE": Αν είναι πίνακας εβδομαδιαίου προγράμματος βαρδιών τεχνιτριών (με ημέρες Δευτέρα-Σάββατο και ονόματα όπως Νάντια, Κατερίνα, Δόμνα κλπ).
    2. "EXPENSE": Αν είναι απόδειξη ταμειακής, τιμολόγιο αγοράς, λογαριασμός (ΔΕΗ, ΕΥΔΑΠ, Cosmote), τραπεζικό statement, απόδειξη POS ή χειρόγραφο σημείωμα εξόδου.
    3. "UNKNOWN": Αν δεν είναι τίποτα από τα παραπάνω.

    ΑΝ ΕΙΝΑΙ "SCHEDULE":
    {{
      "document_type": "SCHEDULE",
      "monday_date": "YYYY-MM-DD",
      "schedule": [
        {{
          "staff_name": "Ονοματεπώνυμο",
          "Δευτέρα": "18:00 - 20:30",
          "Τρίτη": "",
          "Τετάρτη": "17:30 - 20:30",
          "Πέμπτη": "18:00 - 20:00",
          "Παρασκευή": "17:30 - 19:30",
          "Σάββατο": "09:00 - 16:00"
        }}
      ]
    }}

    ΑΝ ΕΙΝΑΙ "EXPENSE":
    Ανάλυσε τα στοιχεία του παραστατικού. Αν δεν αναφέρεται ρητά ημερομηνία, χρησιμοποίησε τη σημερινή: "{today_str}".
    
    Κανόνες Κατηγοριοποίησης main_category (ΕΠΙΛΕΞΕ ΑΥΣΤΗΡΑ ΜΙΑ ΑΠΟ ΑΥΤΕΣ):
    - "Κόστος Υλικών & Εμπορευμάτων (COGS)" (για gel, βερνίκια, λίμες, αναλώσιμα νυχιών, ασετόν, γάντια)
    - "Έξοδα Στέγασης & Λειτουργίας (Ενοίκιο, Κοινόχρηστα)" (για ενοίκιο, κοινόχρηστα κτιρίου)
    - "Λογαριασμοί Κοινής Ωφελείας (Ρεύμα, Νερό, Internet)" (για ρεύμα/ΔΕΗ, νερό, τηλέφωνο/ίντερνετ)
    - "Marketing, Προβολή & Διαφήμιση" (για διαφημίσεις, flyers, social media)
    - "Τεχνολογία, Λογισμικά & Προμήθειες POS" (για Treatwell, τραπεζικές προμήθειες, λογισμικά)
    - "Επαγγελματικές Αμοιβές & Άδειες (Λογιστής, Νομικά)" (για αμοιβή λογιστή, ΕΦΚΑ, φόρους)
    - "Συντήρηση, Εξοπλισμός & Έκτακτα (CapEx)" (για service κλιματιστικού, αγορά τροχών, εξοπλισμό)
    - "Λοιπά Λειτουργικά Έξοδα" (για καθαριστικά, καφέδες, σνακς πελατών, μικροέξοδα)

    expense_nature (επίλεξε ένα):
    - "Αγορά Υλικών/Stock (COGS)" (αν είναι υλικά)
    - "Πάγιο Επαναλαμβανόμενο" (αν είναι ενοίκιο/λογαριασμός)
    - "Μεταβλητό Λειτουργικό" (αν είναι καθημερινό έξοδο)
    - "Έκτακτο / Επένδυση (CapEx)" (αν είναι επισκευή/εξοπλισμός)

    payment_method (επίλεξε ένα):
    - "Επαγγελματική Κάρτα"
    - "Μετρητά Ταμείου"
    - "Τραπεζική Μεταφορά (e-Banking)"
    - "Παρακράτηση POS / Άλλο"

    Επέστρεψε ΑΠΟΚΛΕΙΣΤΙΚΑ έγκυρο JSON:
    {{
      "document_type": "EXPENSE",
      "expense_date": "YYYY-MM-DD",
      "vendor_name": "Επωνυμία επιχείρησης ή προμηθευτή",
      "invoice_number": "Αριθμός παραστατικού/απόδειξης αν υπάρχει",
      "main_category": "...",
      "sub_category": "Σύντομη υποκατηγορία (π.χ. Ημιμόνιμα, Καθαριστικά, Καφές)",
      "expense_nature": "...",
      "net_amount": 0.0,
      "vat_rate": 24.0,
      "vat_amount": 0.0,
      "gross_amount": 0.0,
      "payment_method": "...",
      "notes": "Συνοπτική περιγραφή των προϊόντων ή υπηρεσιών"
    }}
    """

    img_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=[img_part, prompt]
    )
    raw_text = response.text.strip()
    return clean_json_response(raw_text)

def check_duplicate_expense(vendor_name: str, expense_date: str, gross_amount: float) -> list:
    """
    Ελέγχει αν υπάρχει ήδη εγγραφή με ίδιο προμηθευτή, ημερομηνία και μικτό ποσό.
    """
    try:
        supabase = get_supabase_client()
        res = supabase.table("expenses_ledger").select("*")\
            .eq("expense_date", expense_date)\
            .ilike("vendor_name", f"%{vendor_name.strip()}%")\
            .execute()
        
        matches = []
        for r in res.data:
            r_gross = float(r.get("gross_amount", 0.0) or 0.0)
            if abs(r_gross - float(gross_amount)) < 0.05:
                matches.append(r)
        return matches
    except Exception as e:
        return []

def save_expense_to_supabase(exp_data: dict) -> dict:
    """
    Αποθηκεύει μία εγγραφή εξόδου στον πίνακα expenses_ledger της Supabase.
    """
    supabase = get_supabase_client()
    
    gross = float(exp_data.get("gross_amount", 0.0) or 0.0)
    net = float(exp_data.get("net_amount", 0.0) or 0.0)
    vat_r = float(exp_data.get("vat_rate", 24.0) or 24.0)
    vat_amt = float(exp_data.get("vat_amount", 0.0) or 0.0)
    
    # Auto-balance VAT/Net/Gross if any are missing
    if gross > 0 and net == 0:
        net = round(gross / (1.0 + vat_r / 100.0), 2)
        vat_amt = round(gross - net, 2)
    elif net > 0 and gross == 0:
        if vat_amt == 0:
            vat_amt = round(net * (vat_r / 100.0), 2)
        gross = round(net + vat_amt, 2)
    elif gross > 0 and net > 0 and vat_amt == 0:
        vat_amt = round(gross - net, 2)
        
    main_cat = str(exp_data.get("main_category", EXPENSE_MAIN_CATEGORIES[0])).strip()
    if main_cat not in EXPENSE_MAIN_CATEGORIES:
        main_cat = EXPENSE_MAIN_CATEGORIES[0]
        
    nature = str(exp_data.get("expense_nature", EXPENSE_NATURES[0])).strip()
    if nature not in EXPENSE_NATURES:
        nature = EXPENSE_NATURES[0]
        
    pay_m = str(exp_data.get("payment_method", EXPENSE_PAYMENT_METHODS[0])).strip()
    if pay_m not in EXPENSE_PAYMENT_METHODS:
        pay_m = EXPENSE_PAYMENT_METHODS[0]

    record = {
        "expense_date": str(exp_data.get("expense_date", datetime.now().strftime("%Y-%m-%d"))),
        "vendor_name": str(exp_data.get("vendor_name", "Γενικός Προμηθευτής")).strip() or "Γενικός Προμηθευτής",
        "main_category": main_cat,
        "sub_category": str(exp_data.get("sub_category", "")).strip(),
        "expense_nature": nature,
        "net_amount": round(net, 2),
        "vat_rate": round(vat_r, 1),
        "vat_amount": round(vat_amt, 2),
        "gross_amount": round(gross, 2),
        "payment_method": pay_m,
        "invoice_number": str(exp_data.get("invoice_number", "") or "").strip(),
        "notes": str(exp_data.get("notes", "") or "").strip()
    }
    
    res = supabase.table("expenses_ledger").insert([record]).execute()
    inserted_id = res.data[0].get("id") if res.data else None
    record["id"] = inserted_id
    return record
