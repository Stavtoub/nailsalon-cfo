# -*- coding: utf-8 -*-
"""
Treatwell Drop Folder Automated Ingestion for NailSalon_CFO
Automatically monitors and ingests dropped Treatwell CSVs into Supabase.
"""

import os
import sys
if sys.platform == "win32":
    try:
        import ctypes
        _hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if _hwnd:
            ctypes.windll.user32.ShowWindow(_hwnd, 0)
    except Exception:
        pass
import glob
import re
import json
import shutil
import unicodedata
from datetime import datetime
import pandas as pd
from supabase import create_client, Client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DROP_DIR = r"/home/tos/Desktop/Treatwell_Drop"
ARCHIVE_DIR = os.path.join(DROP_DIR, "Archive")
SECRETS_PATH = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")

# Default Parameters
TW_NEW_COMM_PCT = 25.0
TW_RET_COMM_PCT = 2.0
TW_WIDGET_COMM_PCT = 0.0
VAT_RATE_PCT = 24.0

def load_secrets():
    secrets = {}
    if os.path.exists(SECRETS_PATH):
        with open(SECRETS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    secrets[k] = v
    return secrets

def normalize_greek_name(text):
    if pd.isna(text) or not str(text).strip():
        return "ΙΔΙΩΤΗΣ"
    text = str(text).strip()
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', text).upper()

def slugify(text):
    if not text:
        return "UNKNOWN"
    text = normalize_greek_name(text)
    text = re.sub(r'[^a-zA-Z0-9\u0370-\u03FF]', '_', text)
    return re.sub(r'_+', '_', text).strip('_')

def get_supabase_client():
    sec = load_secrets()
    url = sec.get("SUPABASE_URL")
    key = sec.get("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in secrets.toml")
    return create_client(url, key)

def fetch_table_paginated(supabase: Client, table_name: str, batch_size=1000):
    all_rows = []
    offset = 0
    while True:
        res = supabase.table(table_name).select("*").range(offset, offset + batch_size - 1).execute().data
        if not res:
            break
        all_rows.extend(res)
        if len(res) < batch_size:
            break
        offset += batch_size
    return pd.DataFrame(all_rows)

def process_and_calculate_bookings(df_raw, df_services, df_staff, df_existing_bookings):
    srv_map = {re.sub(r'\s+', ' ', str(row['service_name'])).strip().lower(): row.to_dict() for _, row in df_services.iterrows()} if not df_services.empty else {}
    staff_map = {str(row['staff_name']).strip().lower(): float(row.get('hourly_cost', 7.5)) for _, row in df_staff.iterrows()} if not df_staff.empty else {}

    first_visit_map = {}
    if not df_existing_bookings.empty and 'status_group' in df_existing_bookings.columns:
        completed_existing = df_existing_bookings[df_existing_bookings['status_group'] == 'COMPLETED']
        for _, r in completed_existing.iterrows():
            c_name = normalize_greek_name(r.get('customer_name'))
            a_date = str(r.get('appointment_date'))
            if c_name not in first_visit_map or a_date < first_visit_map[c_name]:
                first_visit_map[c_name] = a_date

    parsed_rows = []
    for idx, row in df_raw.iterrows():
        raw_oid = row.get("Αριθμός παραγγελίας #") if "Αριθμός παραγγελίας #" in row else row.get("Αριθμός παραγγελίας")
        order_id = "" if pd.isna(raw_oid) else str(raw_oid).strip()
        if order_id.lower() == "nan":
            order_id = ""

        raw_srv = re.sub(r'\s+', ' ', str(row.get("Υπηρεσία", ""))).strip()
        raw_stf = row.get("Εξυπηρετεί") if "Εξυπηρετεί" in row and pd.notna(row.get("Εξυπηρετεί")) else (row.get("Υπάλληλος") if "Υπάλληλος" in row and pd.notna(row.get("Υπάλληλος")) else row.get("Ειδικός", ""))
        staff_name = str(raw_stf).strip()
        cust_name = normalize_greek_name(row.get("Πελάτης", "ΙΔΙΩΤΗΣ"))
        raw_stat = row.get("Status") if "Status" in row and pd.notna(row.get("Status")) else row.get("Κατάσταση", "")
        status_raw = str(raw_stat).strip()

        raw_appt_dt = row.get("Ημερομηνία ραντεβού") or row.get("Ημερομηνία")
        appt_dt = pd.to_datetime(raw_appt_dt, format="%d/%m/%Y %H:%M", errors="coerce")
        if pd.isna(appt_dt):
            appt_dt = pd.to_datetime(raw_appt_dt, dayfirst=True, errors="coerce")
        if pd.isna(appt_dt):
            appt_dt = datetime.now()

        raw_order_dt = row.get("Ημερομηνία παραγγελίας")
        order_dt = pd.to_datetime(raw_order_dt, format="%d/%m/%Y %H:%M", errors="coerce")
        if pd.isna(order_dt):
            order_dt = pd.to_datetime(raw_order_dt, dayfirst=True, errors="coerce")
        if pd.isna(order_dt):
            order_dt = appt_dt

        appt_date_str = appt_dt.strftime("%Y-%m-%d")
        s_norm = normalize_greek_name(status_raw)
        if "ΑΚΥΡ" in s_norm or "CANCEL" in s_norm:
            status_group = "CANCELLED"
        elif "ΔΕΝ ΠΡΟΣ" in s_norm or "NO-SHOW" in s_norm or "NO SHOW" in s_norm:
            status_group = "NO_SHOW"
        elif "ΕΠΙΒΕΒΑΙ" in s_norm or "CONFIRM" in s_norm:
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
                comm_pct = TW_NEW_COMM_PCT
            else:
                channel = "Treatwell (Επαναλ.)"
                comm_pct = TW_RET_COMM_PCT
        elif order_id.upper().startswith("W"):
            channel = "Widget (Social)"
            comm_pct = TW_WIDGET_COMM_PCT
        else:
            channel = "Direct (Τηλέφωνο)"
            comm_pct = 0.0

        if item["status_group"] == "COMPLETED":
            net_rev = round(gross_price / (1.0 + (VAT_RATE_PCT / 100.0)), 2)
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

def run_ingestion():
    os.makedirs(DROP_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

    csv_files = glob.glob(os.path.join(DROP_DIR, "*.csv"))
    if not csv_files:
        print(json.dumps({"status": "no_files", "message": "No CSV files found in Treatwell_Drop folder."}))
        return

    print(f"Found {len(csv_files)} CSV file(s) in {DROP_DIR}. Connecting to Supabase...")
    supabase = get_supabase_client()

    df_services = fetch_table_paginated(supabase, "config_services")
    df_staff = fetch_table_paginated(supabase, "config_staff")
    df_existing_bookings = fetch_table_paginated(supabase, "bookings_master")

    total_records = 0
    archived_files = []

    for idx, f_path in enumerate(csv_files):
        fname = os.path.basename(f_path)
        print(f"Processing: {fname}...")
        try:
            df_raw = pd.read_csv(f_path)
            df_raw["_src_file"] = str(idx)
            records = process_and_calculate_bookings(df_raw, df_services, df_staff, df_existing_bookings)

            # Upsert in batches of 200
            for i in range(0, len(records), 200):
                supabase.table("bookings_master").upsert(records[i:i+200]).execute()

            total_records += len(records)

            # Archive file
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = os.path.join(ARCHIVE_DIR, f"{timestamp}_{fname}")
            shutil.move(f_path, target_path)
            archived_files.append(fname)
        except Exception as e:
            print(f"Error processing {fname}: {e}", file=sys.stderr)

    result = {
        "status": "success",
        "processed_files": archived_files,
        "total_records_upserted": total_records,
        "timestamp": datetime.now().isoformat()
    }
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    run_ingestion()
