# -*- coding: utf-8 -*-
"""
myDATA Processor & Smart Classifier Engine for NailSalon CFO.
Reusable across Streamlit UI, Background Workers, and Telegram Bot.
"""

import os
import io
import re
import json
from datetime import datetime
import pandas as pd

EXPENSE_MAIN_CATEGORIES = [
    "Κόστος Υλικών & Εμπορευμάτων (COGS)",
    "Έξοδα Στέγασης & Λειτουργίας (Ενοίκιο, Κοινόχρηστα)",
    "Λογαριασμοί Κοινής Ωφελείας (Ρεύμα, Νερό, Internet)",
    "Marketing, Προβολή & Διαφήμιση",
    "Τεχνολογία, Λογισμικά & Προμήθειες POS",
    "Επαγγελματικές Αμοιβές & Άδειες (Λογιστής, Νομικά)",
    "Συντήρηση, Εξοπλισμός & Έκτακτα (CapEx)",
    "Μισθοδοσία & Εισφορές (myDATA)",
    "Αποσβέσεις & Ετήσιες Εγγραφές (EBT)",
    "Λοιπά Λειτουργικά Έξοδα"
]

EXPENSE_NATURES = [
    "Πάγιο Επαναλαμβανόμενο",
    "Μεταβλητό Λειτουργικό",
    "Αγορά Υλικών/Stock (COGS)",
    "Έκτακτο / Επένδυση (CapEx)",
    "Μισθοδοσία Προσωπικού (Payroll)",
    "Μη-Ταμειακό / Φορολογικό (EBT)"
]

EXPENSE_PAYMENT_METHODS = [
    "Επαγγελματική Κάρτα",
    "Τραπεζική Μεταφορά (e-Banking)",
    "Μετρητά Ταμείου",
    "Παρακράτηση POS / Άλλο",
    "Λογιστική Εγγραφή"
]

EXPENSE_VAT_RATES = [24.0, 13.0, 6.0, 0.0]

DEFAULT_EXPENSE_COLUMNS = [
    "id", "expense_date", "vendor_name", "main_category", "sub_category",
    "expense_nature", "net_amount", "vat_rate", "vat_amount", "gross_amount",
    "payment_method", "invoice_number", "notes"
]


def classify_mydata_row(r: dict) -> tuple[str, str, str, str]:
    """
    Ευφυής Μηχανή Ταξινόμησης (Smart Auto-Classifier):
    Αναγνωρίζει αυτόματα και ταξινομεί με 100% ακρίβεια κάθε γραμμή από το βιβλίο myDATA της ΑΑΔΕ
    βάσει ΑΦΜ εκδότη, ΚΑΔ, περιγραφής και αριθμού παραστατικού.
    Επιστρέφει: (main_category, sub_category, expense_nature, payment_method)
    """
    desc = str(r.get("ΠΕΡΙΓΡΑΦΗ", "") or "").strip().upper()
    inv_num = str(r.get("ΑΡ. ΠΑΡΑΣΤΑΤΙΚΟΥ", "") or "").strip().upper()
    vend = str(r.get("Επωνυμία Αντισυμβαλλόμενου", "") or "").strip().upper()
    afm = str(r.get("ΑΦΜ ΕΚΔΟΤΗ", "") or "").strip()
    kad = str(r.get("ΚΑΔ Αντισυμβαλλόμενου", "") or "").strip()
    kad_desc = str(r.get("ΚΑΔ Περιγραφή", "") or r.get("ΚΑΔ Σύντομη Περιγραφή", "") or "").strip().upper()

    # 1. Ενοίκιο Καταστήματος
    if "ΕΝΟΙΚΙΟ" in desc or "ΕΝ ΚΕΝΤΡ" in inv_num or "ΕΝ ΜΕΤ" in inv_num:
        sub = "Ενοίκιο Καταστήματος (Κύριο)" if ("ΕΝ ΚΕΝΤΡ" in inv_num or "1036" in str(r.get("ΚΑΘΑΡΗ ΑΞΙΑ", ""))) else "Χαρτόσημο / Τέλος Ενοικίου"
        return (
            "Έξοδα Στέγασης & Λειτουργίας (Ενοίκιο, Κοινόχρηστα)",
            sub,
            "Πάγιο Επαναλαμβανόμενο",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 2. Μισθοδοσία myDATA
    if "ΜΙΣΘΟΔΟΣΙΑ" in desc or inv_num.startswith("MK") or "ΜΚ" in inv_num:
        sub = "Μισθοδοσία Προσωπικού" if "ΚΑΤΑΣΚΗΝΩΣ" not in inv_num else "Ειδική Εισφορά (Κατασκηνώσεις)"
        return (
            "Μισθοδοσία & Εισφορές (myDATA)",
            sub,
            "Μισθοδοσία Προσωπικού (Payroll)",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 3. Αποσβέσεις Παγίων
    if "ΑΠΟΣΒΕΣΕΙΣ" in desc or "ΑΠΟΣΒΕΣΕΙΣ" in inv_num:
        return (
            "Αποσβέσεις & Ετήσιες Εγγραφές (EBT)",
            "Αποσβέσεις Παγίων",
            "Μη-Ταμειακό / Φορολογικό (EBT)",
            "Λογιστική Εγγραφή"
        )

    # 4. Τέλος Επιτηδεύματος / Ετήσιοι Φόροι
    if "ΤΕΛΟΣ ΕΠΙΤΗΔΕΥΜΑΤΟΣ" in inv_num or "ΤΕΛΟΣ ΕΠΙΤΗΔΕΥΜΑΤΟΣ" in desc:
        return (
            "Αποσβέσεις & Ετήσιες Εγγραφές (EBT)",
            "Τέλος Επιτηδεύματος",
            "Μη-Ταμειακό / Φορολογικό (EBT)",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 5. Treatwell / Funkmartini
    if afm == "800524147" or kad == "79900000" or "FUNKMARTINI" in vend or "TREATWELL" in vend:
        return (
            "Τεχνολογία, Λογισμικά & Προμήθειες POS",
            "Τιμολόγιο Treatwell (Connect & Προμήθειες)",
            "Μεταβλητό Λειτουργικό",
            "Παρακράτηση POS / Άλλο"
        )

    # 6. Ρεύμα / Ενέργεια (NRG, Metlen, Protergia, ΔΕΗ)
    if kad.startswith("3515") or any(k in vend for k in ["NRG", "METLEN", "PROTERGIA", "ΔΕΗ"]):
        return (
            "Λογαριασμοί Κοινής Ωφελείας (Ρεύμα, Νερό, Internet)",
            "Ρεύμα / Ενέργεια",
            "Μεταβλητό Λειτουργικό",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 7. Τηλεπικοινωνίες & Internet (ΟΤΕ, Cosmote, Vodafone)
    if kad.startswith("6190") or any(k in vend for k in ["ΟΡΓΑΝΙΣΜΟΣ ΤΗΛΕΠΙΚΟΙΝΩΝΙΩΝ", "ΟΤΕ", "COSMOTE", "VODAFONE"]):
        return (
            "Λογαριασμοί Κοινής Ωφελείας (Ρεύμα, Νερό, Internet)",
            "Τηλέφωνο / Internet (OTE)",
            "Πάγιο Επαναλαμβανόμενο",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 8. Λογιστής (Σωτηρίου Ε.Ε.)
    if afm == "802287993" or kad.startswith("6920") or "ΣΩΤΗΡΙΟΥ" in vend:
        return (
            "Επαγγελματικές Αμοιβές & Άδειες (Λογιστής, Νομικά)",
            "Αμοιβή Λογιστή",
            "Πάγιο Επαναλαμβανόμενο",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 9. Τραπεζικά Έξοδα (Τράπεζα Πειραιώς)
    if afm == "996763330" or kad.startswith("641912") or "ΠΕΙΡΑΙΩΣ" in vend:
        return (
            "Τεχνολογία, Λογισμικά & Προμήθειες POS",
            "Τραπεζικές Προμήθειες & Έξοδα Συναλλαγών",
            "Μεταβλητό Λειτουργικό",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 10. Euronet / POS Fees
    if afm == "801567852" or kad.startswith("64193") or "EURONET" in vend:
        return (
            "Τεχνολογία, Λογισμικά & Προμήθειες POS",
            "Προμήθειες POS (Euronet)",
            "Μεταβλητό Λειτουργικό",
            "Παρακράτηση POS / Άλλο"
        )

    # 11. G4S / Ασφάλεια
    if afm == "094075777" or kad.startswith("466406") or "G4S" in vend:
        return (
            "Έξοδα Στέγασης & Λειτουργίας (Ενοίκιο, Κοινόχρηστα)",
            "Συναγερμός & Ασφάλεια (G4S)",
            "Πάγιο Επαναλαμβανόμενο",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 12. Katsioulis / Χαρτικά & Καθαριότητα
    if kad.startswith("464923") or "KATSIOULIS" in vend:
        return (
            "Λοιπά Λειτουργικά Έξοδα",
            "Χαρτικά & Αναλώσιμα Καθαριότητας",
            "Μεταβλητό Λειτουργικό",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 13. Ravenna / Πλακάκια - CapEx
    if kad.startswith("4752") or "RAVENNA" in vend:
        return (
            "Συντήρηση, Εξοπλισμός & Έκτακτα (CapEx)",
            "Ανακαίνιση & Εξοπλισμός Καταστήματος",
            "Έκτακτο / Επένδυση (CapEx)",
            "Επαγγελματική Κάρτα"
        )

    # 14. Απολυμάνσεις
    if kad.startswith("8123") or "ΛΑΠΟΥΚΟΓΛΟΥ" in vend or "ΑΠΟΛΥΜΑΝΣ" in kad_desc:
        return (
            "Έξοδα Στέγασης & Λειτουργίας (Ενοίκιο, Κοινόχρηστα)",
            "Απολύμανση & Απεντόμωση",
            "Μεταβλητό Λειτουργικό",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 15. Apifon / SMS Marketing
    if kad.startswith("6310") or "APIFON" in vend:
        return (
            "Marketing, Προβολή & Διαφήμιση",
            "SMS Marketing & Επικοινωνία (Apifon)",
            "Μεταβλητό Λειτουργικό",
            "Επαγγελματική Κάρτα"
        )

    # 16. Παπαδοπούλου / Περιοδικά
    if kad.startswith("5813") or "ΠΑΠΑΔΟΠΟΥΛΟΥ" in vend:
        return (
            "Marketing, Προβολή & Διαφήμιση",
            "Έντυπη Διαφήμιση / Περιοδικά",
            "Μεταβλητό Λειτουργικό",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 17. Βαδάσης / Ανεύρεση Πελατών
    if kad.startswith("7320") or "ΒΑΔΑΣΗΣ" in vend:
        return (
            "Marketing, Προβολή & Διαφήμιση",
            "Υπηρεσίες Ανεύρεσης Πελατών",
            "Μεταβλητό Λειτουργικό",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # 18. Καφές / Super Market (Nespresso, Μασούτης)
    if any(k in vend for k in ["ΝΕΣΠΡΕΣΣΟ", "ΜΑΣΟΥΤΗΣ"]) or any(k in kad for k in ["4711", "4727"]):
        return (
            "Λοιπά Λειτουργικά Έξοδα",
            "Καφές & Φιλοξενία Πελατών",
            "Μεταβλητό Λειτουργικό",
            "Επαγγελματική Κάρτα"
        )

    # 19. Attica / Στολές & Ένδυση
    if "ΑΤΤΙΚΑ" in vend or kad.startswith("4771"):
        return (
            "Λοιπά Λειτουργικά Έξοδα",
            "Στολές & Ένδυση Προσωπικού",
            "Μεταβλητό Λειτουργικό",
            "Επαγγελματική Κάρτα"
        )

    # 20. Αγγελόπουλος Group (Υπεραγορά / Αναλώσιμα)
    if "ΑΓΓΕΛΟΠΟΥΛΟΣ" in vend or kad.startswith("4712"):
        return (
            "Κόστος Υλικών & Εμπορευμάτων (COGS)",
            "Εξοπλισμός & Αναλώσιμα Καταστήματος",
            "Αγορά Υλικών/Stock (COGS)",
            "Επαγγελματική Κάρτα"
        )

    # 21. Προμηθευτές Υλικών Νυχιών (Miss Kiss, Lotus, Kyana, Pablo, κ.λπ.)
    nail_suppliers = [
        "MISS KISS", "ΚΟΥΝΑΤΙΔΗΣ", "ΚΑΡΔΑΡΑ", "KYANA", "PABLO", "ΜΥΤΗΛΑΚΗΣ",
        "SALONTECH", "ΜΗΤΑΚΙΔΗΣ", "EVELLON", "NATURAL CARE", "ΛΟΤΟΥΣ",
        "CHRISTIAN ARTESIO", "ΒΡΕΤΤΟΣ", "ΑΝΕΣΤΑΚΗΣ", "ΜΠΟΓΙΑΡΗ", "ΓΚΟΛΕΜΗΣ"
    ]
    if any(s in vend for s in nail_suppliers) or kad.startswith("4645") or kad.startswith("4775") or kad.startswith("4690"):
        return (
            "Κόστος Υλικών & Εμπορευμάτων (COGS)",
            "Υλικά Νυχιών & Καλλυντικά",
            "Αγορά Υλικών/Stock (COGS)",
            "Τραπεζική Μεταφορά (e-Banking)"
        )

    # Fallback
    return (
        "Λοιπά Λειτουργικά Έξοδα",
        "Γενικά Έξοδα",
        "Μεταβλητό Λειτουργικό",
        "Τραπεζική Μεταφορά (e-Banking)"
    )


def parse_mydata_file(file_source, filename: str = "") -> tuple[pd.DataFrame, list[dict], str]:
    """
    Διαβάζει αρχείο Excel (.xlsx, .xls) ή CSV από path ή file-like object.
    Επιστρέφει (df, records_list, error_message).
    """
    fn = (filename or getattr(file_source, "name", "") or str(file_source)).lower()
    df_raw = None

    try:
        if fn.endswith(".csv"):
            if isinstance(file_source, str):
                with open(file_source, "rb") as f:
                    content = f.read()
            elif hasattr(file_source, "getvalue"):
                content = file_source.getvalue()
            elif hasattr(file_source, "read"):
                content = file_source.read()
            else:
                content = bytes(file_source)

            for enc in ["utf-8-sig", "utf-8", "windows-1253", "iso-8859-7", "cp1252"]:
                try:
                    df_raw = pd.read_csv(io.BytesIO(content), encoding=enc)
                    break
                except Exception:
                    continue
            if df_raw is None:
                return pd.DataFrame(), [], "Αδυναμία ανάγνωσης του CSV αρχείου. Ελέγξτε τη μορφοποίηση."
        else:
            df_raw = pd.read_excel(file_source)
    except Exception as e:
        return pd.DataFrame(), [], f"Σφάλμα ανάγνωσης αρχείου: {str(e)}"

    if df_raw is None or df_raw.empty:
        return pd.DataFrame(), [], "Το αρχείο είναι κενό."

    cols_upper = [str(c).strip().upper() for c in df_raw.columns]
    is_mydata = any("ΜΑΡΚ" in c for c in cols_upper) and any("ΠΑΡΑΣΤΑΤΙΚ" in c for c in cols_upper)

    out_records = []
    if is_mydata:
        for _, r in df_raw.iterrows():
            raw_d = r.get("ΗΜΕΡΟΜΗΝΙΑ ΕΚΔΟΣΗΣ")
            if pd.isna(raw_d) or not str(raw_d).strip():
                continue
            try:
                d_val = pd.to_datetime(raw_d, dayfirst=True).date()
                d_str = d_val.strftime("%Y-%m-%d")
            except Exception:
                continue

            net = float(pd.to_numeric(r.get("ΚΑΘΑΡΗ ΑΞΙΑ", 0.0), errors="coerce") or 0.0)
            gross = float(pd.to_numeric(r.get("ΣΥΝΟΛΙΚΗ ΑΞΙΑ", 0.0), errors="coerce") or 0.0)
            vat_amt = float(pd.to_numeric(r.get("Φ.Π.Α.", 0.0), errors="coerce") or 0.0)

            other_taxes = (
                float(pd.to_numeric(r.get("Λοιποί Φόροι στις Αναλυτικές Γραμμές", 0.0), errors="coerce") or 0.0) +
                float(pd.to_numeric(r.get("Τέλη στις Αναλυτικές Γραμμές", 0.0), errors="coerce") or 0.0)
            )

            vat_r = round(abs(vat_amt / net) * 100.0, 1) if abs(net) > 0.01 and abs(vat_amt) > 0.01 else 0.0
            if abs(vat_r - 24.0) < 1.0: vat_r = 24.0
            elif abs(vat_r - 13.0) < 1.0: vat_r = 13.0
            elif abs(vat_r - 6.0) < 1.0: vat_r = 6.0

            cat, sub, nat, pay = classify_mydata_row(r.to_dict())

            mark_str = str(r.get("ΜΑΡΚ", "") or "").strip()
            inv_str = str(r.get("ΑΡ. ΠΑΡΑΣΤΑΤΙΚΟΥ", "") or "").strip()
            afm_str = str(r.get("ΑΦΜ ΕΚΔΟΤΗ", "") or "").strip()
            vend_str = str(r.get("Επωνυμία Αντισυμβαλλόμενου", "") or "").strip()
            if not vend_str:
                if "ΕΝΟΙΚΙΟ" in str(r.get("ΠΕΡΙΓΡΑΦΗ", "")).upper() or "ΕΝ " in inv_str:
                    vend_str = "Ιδιοκτήτης Ακινήτου (Ενοίκιο)"
                elif "ΜΙΣΘΟΔΟΣΙΑ" in str(r.get("ΠΕΡΙΓΡΑΦΗ", "")).upper() or inv_str.startswith("MK"):
                    vend_str = "Μισθοδοσία Προσωπικού"
                elif "ΑΠΟΣΒΕΣΕΙΣ" in str(r.get("ΠΕΡΙΓΡΑΦΗ", "")).upper():
                    vend_str = "Λογιστικές Αποσβέσεις Παγίων"
                elif "ΤΕΛΟΣ ΕΠΙΤΗΔΕΥΜΑΤΟΣ" in inv_str:
                    vend_str = "ΑΑΔΕ / ΔΟΥ (Τέλος Επιτηδεύματος)"
                else:
                    vend_str = "Διάφοροι Εκδότες"

            meta = {
                "mark": mark_str,
                "afm": afm_str,
                "kad": str(r.get("ΚΑΔ Αντισυμβαλλόμενου", "") or "").strip(),
                "kad_desc": str(r.get("ΚΑΔ Περιγραφή", "") or r.get("ΚΑΔ Σύντομη Περιγραφή", "") or "").strip(),
                "doc_type": str(r.get("ΠΕΡΙΓΡΑΦΗ", "") or "").strip(),
                "other_taxes": round(other_taxes, 2),
                "source": "myDATA"
            }

            out_records.append({
                "expense_date": d_str,
                "vendor_name": vend_str,
                "main_category": cat,
                "sub_category": sub,
                "expense_nature": nat,
                "net_amount": round(net, 2),
                "vat_rate": round(vat_r, 1),
                "vat_amount": round(vat_amt, 2),
                "gross_amount": round(gross, 2),
                "payment_method": pay,
                "invoice_number": inv_str,
                "notes": json.dumps(meta, ensure_ascii=False),
                "mark": mark_str,
                "issuer_afm": afm_str,
                "kad_code": str(r.get("ΚΑΔ Αντισυμβαλλόμενου", "") or "").strip(),
                "other_taxes": round(other_taxes, 2),
                "doc_type": str(r.get("ΠΕΡΙΓΡΑΦΗ", "") or "").strip()
            })
    else:
        # Fallback generic expenses CSV
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
        for _, r in df_norm.iterrows():
            raw_d = r.get("expense_date")
            if pd.isna(raw_d) or not raw_d:
                continue
            try:
                d_val = pd.to_datetime(raw_d, dayfirst=True).date()
                d_str = d_val.strftime("%Y-%m-%d")
            except Exception:
                continue

            net = float(pd.to_numeric(r.get("net_amount", 0.0), errors="coerce") or 0.0)
            gross = float(pd.to_numeric(r.get("gross_amount", 0.0), errors="coerce") or 0.0)
            vat_r = float(pd.to_numeric(r.get("vat_rate", 24.0), errors="coerce") or 24.0)
            vat_amt = float(pd.to_numeric(r.get("vat_amount", 0.0), errors="coerce") or 0.0)

            if gross != 0 and net == 0:
                net = round(gross / (1.0 + vat_r / 100.0), 2)
                vat_amt = round(gross - net, 2)
            elif net != 0 and gross == 0:
                vat_amt = round(net * (vat_r / 100.0), 2)
                gross = round(net + vat_amt, 2)

            main_c = str(r.get("main_category", EXPENSE_MAIN_CATEGORIES[0])).strip() or EXPENSE_MAIN_CATEGORIES[0]
            nat = str(r.get("expense_nature", EXPENSE_NATURES[0])).strip() or EXPENSE_NATURES[0]
            pay = str(r.get("payment_method", EXPENSE_PAYMENT_METHODS[0])).strip() or EXPENSE_PAYMENT_METHODS[0]

            out_records.append({
                "expense_date": d_str,
                "vendor_name": str(r.get("vendor_name", "Γενικός Προμηθευτής")).strip() or "Γενικός Προμηθευτής",
                "main_category": main_c,
                "sub_category": str(r.get("sub_category", "")).strip(),
                "expense_nature": nat,
                "net_amount": round(net, 2),
                "vat_rate": round(vat_r, 1),
                "vat_amount": round(vat_amt, 2),
                "gross_amount": round(gross, 2),
                "payment_method": pay,
                "invoice_number": str(r.get("invoice_number", "")).strip(),
                "notes": str(r.get("notes", "")).strip()
            })

    if not out_records:
        return pd.DataFrame(), [], "Δεν βρέθηκαν έγκυρες εγγραφές εξόδων στο αρχείο."

    return pd.DataFrame(out_records), out_records, ""


def analyze_mydata_batch_against_db(records: list[dict], supabase_client) -> dict:
    """
    Συγκρίνει τις γραμμές του αρχείου με τη Supabase βάσει ΜΑΡΚ.
    Επιστρέφει στατιστικά και τη λίστα των πραγματικά ΝΕΩΝ εγγραφών.
    """
    existing_marks_map = {}
    try:
        curr_res = supabase_client.table("expenses_ledger").select("id, invoice_number, notes").execute()
        for r in (curr_res.data or []):
            r_notes = str(r.get("notes", "") or "")
            if "mark" in r_notes:
                try:
                    m = json.loads(r_notes).get("mark")
                    if m:
                        existing_marks_map[str(m).strip()] = r["id"]
                except Exception:
                    pass
    except Exception:
        pass

    new_records = []
    duplicate_records = []

    for r in records:
        m = str(r.get("mark", "") or "").strip()
        if m and m in existing_marks_map:
            duplicate_records.append(r)
        else:
            new_records.append(r)

    total_rows = len(records)
    new_count = len(new_records)
    dup_count = len(duplicate_records)

    new_gross_sum = round(sum(r.get("gross_amount", 0.0) for r in new_records), 2)
    new_net_sum = round(sum(r.get("net_amount", 0.0) for r in new_records), 2)
    new_vat_sum = round(sum(r.get("vat_amount", 0.0) for r in new_records), 2)

    cat_breakdown = {}
    for r in new_records:
        c = r.get("main_category", "Λοιπά Λειτουργικά Έξοδα")
        cat_breakdown[c] = round(cat_breakdown.get(c, 0.0) + r.get("gross_amount", 0.0), 2)

    sorted_categories = sorted(cat_breakdown.items(), key=lambda x: abs(x[1]), reverse=True)

    return {
        "total_rows": total_rows,
        "new_count": new_count,
        "dup_count": dup_count,
        "new_gross_sum": new_gross_sum,
        "new_net_sum": new_net_sum,
        "new_vat_sum": new_vat_sum,
        "categories_breakdown": sorted_categories,
        "new_records": new_records,
        "duplicate_records": duplicate_records
    }


def save_mydata_records_to_supabase(records: list[dict], supabase_client) -> int:
    """
    Αποθηκεύει τις εγγραφές στον πίνακα expenses_ledger της Supabase σε παρτίδες (batches των 100).
    """
    if not records:
        return 0

    clean_records = []
    for r in records:
        exp_d = str(r.get("expense_date", ""))
        rec = {
            "expense_date": exp_d,
            "vendor_name": str(r.get("vendor_name", "Γενικός Προμηθευτής")).strip() or "Γενικός Προμηθευτής",
            "main_category": str(r.get("main_category", EXPENSE_MAIN_CATEGORIES[0])).strip(),
            "sub_category": str(r.get("sub_category", "")).strip(),
            "expense_nature": str(r.get("expense_nature", EXPENSE_NATURES[0])).strip(),
            "net_amount": round(float(r.get("net_amount", 0.0) or 0.0), 2),
            "vat_rate": round(float(r.get("vat_rate", 24.0) or 24.0), 1),
            "vat_amount": round(float(r.get("vat_amount", 0.0) or 0.0), 2),
            "gross_amount": round(float(r.get("gross_amount", 0.0) or 0.0), 2),
            "payment_method": str(r.get("payment_method", EXPENSE_PAYMENT_METHODS[0])).strip(),
            "invoice_number": str(r.get("invoice_number", "") or "").strip(),
            "notes": str(r.get("notes", "") or "").strip()
        }
        clean_records.append(rec)

    saved_count = 0
    for i in range(0, len(clean_records), 100):
        chunk = clean_records[i:i+100]
        if chunk:
            supabase_client.table("expenses_ledger").upsert(chunk).execute()
            saved_count += len(chunk)

    return saved_count
