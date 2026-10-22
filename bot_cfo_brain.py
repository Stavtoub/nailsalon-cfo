# -*- coding: utf-8 -*-
"""
AI Virtual CFO Brain for NailSalon CFO Telegram Assistant.
Handles free-form conversational queries, financial diagnostics, root-cause analysis,
using Gemini 3.8 Flash with Automatic Function Calling (Universal Supabase Tools).
"""

import os
import sys
import re
import json
import time
from datetime import datetime, timedelta
import pandas as pd
from google import genai
from google.genai import types

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
NAIL_DIR = r"C:\Users\TOS\Desktop\NailSalon_CFO"
SECRETS_FILE = os.path.join(NAIL_DIR, ".streamlit", "secrets.toml")

DAYS_GR = ["Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο"]

def load_secrets():
    secrets = {}
    if os.path.exists(SECRETS_FILE):
        with open(SECRETS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    secrets[k.strip()] = v.strip().strip('"').strip("'")
    return secrets

def get_supabase_client():
    from supabase import create_client
    sec = load_secrets()
    url = sec.get("SUPABASE_URL")
    key = sec.get("SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

def get_gemini_client():
    sec = load_secrets()
    api_key = sec.get("GEMINI_API_KEY")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

def calc_shift_hours(shift_str):
    if not shift_str or pd.isna(shift_str):
        return 0.0
    s = str(shift_str).strip()
    if not s or "κλειστ" in s.lower() or "ρεπ" in s.lower() or s == "-":
        return 0.0
    intervals = []
    matches = re.findall(r'(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})', s)
    for h1, m1, h2, m2 in matches:
        st_h = int(h1) + int(m1) / 60.0
        en_h = int(h2) + int(m2) / 60.0
        if en_h > st_h:
            intervals.append((st_h, en_h))
    return round(sum(en - st for st, en in intervals), 2)

def execute_cfo_diagnostics(user_query: str) -> str:
    """
    Executes a dynamic forensic query using Gemini 3.8 Flash with Universal Tools
    connected directly to Supabase tables.
    """
    sb = get_supabase_client()
    client = get_gemini_client()
    if not sb or not client:
        return "⚠️ Σφάλμα σύνδεσης: Ελέγξτε τα κλειδιά Supabase & Gemini στο secrets.toml."

    # Universal Tool 1: Query Bookings & Turnover
    def query_bookings(
        start_date: str = "",
        end_date: str = "",
        staff_name: str = "",
        service_keyword: str = "",
        status_group: str = "",
        limit: int = 200
    ) -> dict:
        """
        Αναζητά ραντεβού, τζίρο και επιδόσεις τεχνιτριών από τον πίνακα bookings_master.
        Παράμετροι:
        - start_date: Ημερομηνία έναρξης σε YYYY-MM-DD (π.χ. '2026-10-21' ή '2025-10-21').
        - end_date: Ημερομηνία λήξης σε YYYY-MM-DD.
        - staff_name: Όνομα τεχνίτριας (π.χ. 'Κατερίνα', 'Δόμνα', 'Νάντια').
        - service_keyword: Λέξη-κλειδί υπηρεσίας (π.χ. 'Ημιμόνιμο', 'Gel', 'Πεντικιούρ').
        - status_group: Κατάσταση ('COMPLETED', 'CONFIRMED', 'CANCELLED').
        """
        try:
            q = sb.table("bookings_master").select(
                "appointment_date, appointment_time, staff_name, raw_service, gross_price, status_group, commission_fee, net_revenue, duration_minutes"
            )
            if start_date:
                q = q.gte("appointment_date", start_date)
            if end_date:
                q = q.lte("appointment_date", end_date)
            if staff_name:
                q = q.ilike("staff_name", f"%{staff_name.strip()}%")
            if service_keyword:
                q = q.ilike("raw_service", f"%{service_keyword.strip()}%")
            if status_group:
                q = q.eq("status_group", status_group.strip().upper())

            rows = q.order("appointment_date", desc=False).limit(limit).execute().data or []

            completed_gross = sum(float(r.get("gross_price", 0.0) or 0.0) for r in rows if r.get("status_group") == "COMPLETED")
            confirmed_gross = sum(float(r.get("gross_price", 0.0) or 0.0) for r in rows if r.get("status_group") == "CONFIRMED")
            cancelled_rows = [r for r in rows if r.get("status_group") == "CANCELLED"]
            active_rows = [r for r in rows if r.get("status_group") != "CANCELLED"]

            staff_breakdown = {}
            for r in active_rows:
                st = r.get("staff_name", "Άγνωστη")
                p = float(r.get("gross_price", 0.0) or 0.0)
                dur = float(r.get("duration_minutes", 0) or 0) / 60.0
                if st not in staff_breakdown:
                    staff_breakdown[st] = {"count": 0, "gross": 0.0, "hours": 0.0, "services": []}
                staff_breakdown[st]["count"] += 1
                staff_breakdown[st]["gross"] = round(staff_breakdown[st]["gross"] + p, 2)
                staff_breakdown[st]["hours"] = round(staff_breakdown[st]["hours"] + dur, 2)
                srv = r.get("raw_service")
                if srv and srv not in staff_breakdown[st]["services"]:
                    staff_breakdown[st]["services"].append(srv)

            service_counts = {}
            for r in active_rows:
                srv = r.get("raw_service", "Άλλη")
                service_counts[srv] = service_counts.get(srv, 0) + 1

            return {
                "total_bookings_found": len(rows),
                "active_bookings_count": len(active_rows),
                "cancelled_bookings_count": len(cancelled_rows),
                "total_active_gross_turnover": round(confirmed_gross + completed_gross, 2),
                "completed_turnover": round(completed_gross, 2),
                "confirmed_turnover": round(confirmed_gross, 2),
                "cancelled_potential_turnover": round(sum(float(r.get("gross_price", 0.0) or 0.0) for r in cancelled_rows), 2),
                "staff_breakdown": staff_breakdown,
                "top_services": sorted(service_counts.items(), key=lambda x: x[1], reverse=True)[:6],
                "sample_appointments": active_rows[:12]
            }
        except Exception as e:
            return {"error": f"Error querying bookings: {str(e)}"}

    # Universal Tool 2: Query Shift Schedules & Real Idle Time
    def query_shift_schedule(target_date: str) -> dict:
        """
        Ανακτά το πραγματικό καταχωρημένο εβδομαδιαίο πρόγραμμα βαρδιών για μια συγκεκριμένη ημερομηνία (YYYY-MM-DD),
        και υπολογίζει επακριβώς:
        - Πραγματικές ώρες βάρδιας (π.χ. 18:00 - 20:00 -> 2h, 14:30 - 20:30 -> 6h, 10:00 - 18:00 -> 8h).
        - Ώρες ραντεβού (παραγωγικός χρόνος).
        - Πραγματικό κενό χρόνο (idle hours = βάρδια - ραντεβού).
        - Ποσοστό αξιοποίησης (utilization %).
        """
        try:
            dt = datetime.strptime(target_date.strip(), "%Y-%m-%d")
            mon = dt.date() - timedelta(days=dt.weekday())
            week_key = "WEEK_" + mon.strftime("%Y-%m-%d")

            day_idx = dt.weekday()
            if day_idx > 5:
                return {"error": "Κυριακή: Το κατάστημα είναι κλειστό."}
            day_name = DAYS_GR[day_idx]

            res = sb.table("config_services").select("*").eq("service_name", week_key).execute()
            if not res.data:
                return {"error": f"Δεν βρέθηκε καταχωρημένο πρόγραμμα βαρδιών για την εβδομάδα {week_key} στη βάση."}

            shifts = json.loads(res.data[0].get("price_variants_json") or "[]")

            # Bookings for that day
            b_res = sb.table("bookings_master").select("staff_name, duration_minutes, status_group").eq("appointment_date", target_date).execute()
            staff_booked = {}
            for b in (b_res.data or []):
                if b.get("status_group") != "CANCELLED":
                    st = str(b.get("staff_name", "")).strip()
                    dur = float(b.get("duration_minutes", 0) or 0) / 60.0
                    staff_booked[st] = round(staff_booked.get(st, 0.0) + dur, 2)

            summary = []
            for s in shifts:
                name = s["staff_name"]
                shift_str = s.get(day_name, "")
                shift_h = calc_shift_hours(shift_str)
                booked_h = staff_booked.get(name, 0.0)
                if shift_h > 0 or booked_h > 0:
                    idle_h = round(max(0.0, shift_h - booked_h), 2)
                    util = round((booked_h / shift_h) * 100.0, 1) if shift_h > 0 else 0.0
                    summary.append({
                        "staff_name": name,
                        "declared_shift": shift_str or "Ρεπό / Χωρίς Βάρδια",
                        "shift_hours": shift_h,
                        "booked_hours": booked_h,
                        "idle_hours": idle_h,
                        "utilization_pct": util
                    })

            return {
                "target_date": target_date,
                "day_name": day_name,
                "week_program": week_key,
                "staff_utilization": summary
            }
        except Exception as e:
            return {"error": f"Error querying shift schedule: {str(e)}"}

    # Universal Tool 3: Query Expenses
    def query_expenses(
        start_date: str = "",
        end_date: str = "",
        vendor_name: str = "",
        main_category: str = "",
        expense_nature: str = "",
        limit: int = 150
    ) -> dict:
        """
        Αναζητά έξοδα, τιμολόγια και προμηθευτές από τον πίνακα expenses_ledger και expenses_log.
        Παράμετροι:
        - start_date: Ημερομηνία έναρξης σε YYYY-MM-DD.
        - end_date: Ημερομηνία λήξης σε YYYY-MM-DD.
        - vendor_name: Όνομα ή μέρος του προμηθευτή (π.χ. 'Metlen', 'Miss Kiss', 'ΔΕΗ', 'Σωτηρίου', 'Μισθοδοσία').
        - main_category: Βασική κατηγορία (π.χ. 'COGS', 'Στέγασης', 'Ωφελείας', 'Marketing', 'Μισθοδοσία').
        - expense_nature: Φύση εξόδου ('Πάγιο', 'Μεταβλητό', 'COGS', 'CapEx', 'Payroll').
        """
        try:
            q = sb.table("expenses_ledger").select("*")
            if start_date:
                q = q.gte("expense_date", start_date)
            if end_date:
                q = q.lte("expense_date", end_date)
            if vendor_name:
                q = q.ilike("vendor_name", f"%{vendor_name.strip()}%")
            if main_category:
                q = q.ilike("main_category", f"%{main_category.strip()}%")
            if expense_nature:
                q = q.ilike("expense_nature", f"%{expense_nature.strip()}%")

            rows = q.order("expense_date", desc=True).limit(limit).execute().data or []

            total_gross = sum(float(r.get("gross_amount", 0.0) or 0.0) for r in rows)
            total_net = sum(float(r.get("net_amount", 0.0) or 0.0) for r in rows)
            total_vat = sum(float(r.get("vat_amount", 0.0) or 0.0) for r in rows)

            cat_summary = {}
            for r in rows:
                c = r.get("main_category", "Λοιπά")
                g = float(r.get("gross_amount", 0.0) or 0.0)
                cat_summary[c] = round(cat_summary.get(c, 0.0) + g, 2)

            vendor_summary = {}
            for r in rows:
                v = r.get("vendor_name", "Άγνωστος")
                g = float(r.get("gross_amount", 0.0) or 0.0)
                vendor_summary[v] = round(vendor_summary.get(v, 0.0) + g, 2)

            return {
                "total_expense_records": len(rows),
                "total_gross_amount": round(total_gross, 2),
                "total_net_amount": round(total_net, 2),
                "total_vat_amount": round(total_vat, 2),
                "categories_breakdown": sorted(cat_summary.items(), key=lambda x: x[1], reverse=True),
                "top_vendors": sorted(vendor_summary.items(), key=lambda x: x[1], reverse=True)[:8],
                "sample_expenses": rows[:10]
            }
        except Exception as e:
            return {"error": f"Error querying expenses: {str(e)}"}

    # Universal Tool 4: Get Financial Summary & P&L
    def get_financial_summary(start_date: str = "", end_date: str = "") -> dict:
        """
        Υπολογίζει συνολικό ισοζύγιο P&L (Τζίρος, Έξοδα, Υλικά COGS, Καθαρό Κέρδος) για μια περίοδο.
        """
        try:
            bookings_info = query_bookings(start_date=start_date, end_date=end_date, limit=1000)
            expenses_info = query_expenses(start_date=start_date, end_date=end_date, limit=1000)

            turnover = bookings_info.get("total_active_gross_turnover", 0.0)
            expenses = expenses_info.get("total_gross_amount", 0.0)
            net_profit_est = round(turnover - expenses, 2)
            margin_pct = round((net_profit_est / turnover) * 100.0, 1) if turnover > 0 else 0.0

            return {
                "period": f"{start_date} έως {end_date}" if start_date or end_date else "Όλη η περίοδος",
                "gross_turnover": turnover,
                "gross_expenses": expenses,
                "net_profit_estimate": net_profit_est,
                "profit_margin_pct": margin_pct,
                "bookings_count": bookings_info.get("active_bookings_count", 0),
                "expenses_count": expenses_info.get("total_expense_records", 0),
                "top_expense_categories": expenses_info.get("categories_breakdown", [])[:4],
                "staff_turnover": bookings_info.get("staff_breakdown", {})
            }
        except Exception as e:
            return {"error": f"Error computing financial summary: {str(e)}"}

    # Universal Tool 5: Get Staff List & Declared Info
    def get_staff_info() -> dict:
        """Επιστρέφει τη λίστα με τις επίσημες τεχνίτριες και τις εβδομαδιαίες δηλωμένες ώρες."""
        try:
            staff_rows = sb.table("config_staff").select("staff_name, weekly_hours_declared, hourly_cost").execute().data or []
            return {"staff": staff_rows}
        except Exception as e:
            return {"error": str(e)}

    # Configure Gemini with Universal Tools
    tools_list = [query_bookings, query_shift_schedule, query_expenses, get_financial_summary, get_staff_info]

    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    year_str = today_dt.strftime("%Y")

    system_instruction = f"""
    Είσαι ο εξειδικευμένος AI Virtual CFO και Ορκωτός Ελεγκτής του Nail Salon στο Telegram.
    Απαντάς στον ιδιοκτήτη (Σταύρος) άμεσα, φιλικά, με απόλυτη ακρίβεια αριθμών και επαγγελματισμό.

    ΠΛΑΙΣΙΟ ΧΡΟΝΟΥ:
    - Σημερινή ημερομηνία συστήματος: {today_str} (Έτος {year_str}).
    - Στη βάση δεδομένων υπάρχουν ιστορικά δεδομένα για το 2024, 2025 και 2026 (με ραντεβού που φτάνουν έως και μέσα Νοεμβρίου 2026).

    ΚΡΙΣΙΜΟΙ ΚΑΝΟΝΕΣ:
    1. ΠΟΤΕ ΜΗΝ ΜΑΝΤΕΥΕΙΣ Ή ΥΠΟΘΕΤΕΙΣ ΝΟΥΜΕΡΑ! 
       Για οποιαδήποτε ερώτηση αφορά τζίρο, ραντεβού, έξοδα, βάρδιες, τεχνίτριες ή κέρδη, 
       ΠΡΕΠΕΙ ΝΑ ΚΑΛΕΣΕΙΣ ΤΑ ΚΑΤΑΛΛΗΛΑ ΕΡΓΑΛΕΙΑ.
    2. ΠΡΟΓΡΑΜΜΑ ΒΑΡΔΙΩΝ & ΚΕΝΟΣ ΧΡΟΝΟΣ (IDLE TIME):
       - Για ερωτήσεις σχετικά με πρόγραμμα εργασίας, βάρδιες, ώρες προσέλευσης/αποχώρησης, παραγωγικό χρόνο ή κενό χρόνο,
         ΚΑΛΕΣΕ ΤΟ ΕΡΓΑΛΕΙΟ `query_shift_schedule(target_date)`.
       - Στη βάση ΥΠΑΡΧΕΙ ΚΑΤΑΧΩΡΗΜΕΝΟ ΤΟ ΠΛΗΡΕΣ ΠΡΟΓΡΑΜΜΑ ΒΑΡΔΙΩΝ (config_services με category '__SHIFT_SCHEDULE__').
       - ΜΗΝ υποθέτεις ποτέ αυθαίρετο 8ωρο για όλες! Χρησιμοποίησε τις πραγματικές δηλωμένες βάρδιες (π.χ. Νάντια 2h ή 3h, Κατερίνα 6h, κλπ).
    3. ΚΟΣΤΟΣ ΜΙΣΘΟΔΟΣΙΑΣ:
       - Προσοχή: Το '7,50 €/ώρα' ήταν ένα απλό αρχικό παράδειγμα (default placeholder) και ΔΕΝ αποτελεί το πραγματικό ενιαίο ωρομίσθιο.
       - Οι πραγματικές μισθοδοσίες είναι καταχωρημένες μηνιαία στα myDATA έξοδα (κατηγορία 'Μισθοδοσία & Εισφορές (myDATA)').
       - Μην αναφέρεις flat 7,50 €/ώρα σαν να είναι ο πραγματικός μισθός τους.
    4. ΜΟΡΦΟΠΟΙΗΣΗ TELEGRAM HTML:
       - Χρησιμοποίησε <b>έντονα</b> για νούμερα, ποσά (€), ονόματα και τίτλους.
       - Χρησιμοποίησε <i>πλάγια</i> για επεξηγήσεις.
       - Χρησιμοποίησε ευδιάκριτα bullet points (•) και κατάλληλα emojis (💶, 📊, ⏱️, 👥, 💅).
       - ΜΗΝ χρησιμοποιείς markdown headers (### ή **). Χρησιμοποίησε ΑΠΟΚΛΕΙΣΤΙΚΑ Telegram HTML tags.
    """

    try:
        cfg = types.GenerateContentConfig(
            tools=tools_list,
            system_instruction=system_instruction,
            temperature=0.2
        )
        chat = client.chats.create(model="gemini-3.8-flash", config=cfg)
        response = chat.send_message(user_query)
        return response.text.strip()
    except Exception as e:
        return f"⚠️ <b>Σφάλμα κατά την επεξεργασία:</b>\n<code>{str(e)}</code>"
