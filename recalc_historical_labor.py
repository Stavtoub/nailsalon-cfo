import sys
import tomllib
from supabase import create_client

def main():
    with open('.streamlit/secrets.toml', 'rb') as f:
        sec = tomllib.load(f)

    url = sec['SUPABASE_URL']
    key = sec['SUPABASE_KEY']
    sb = create_client(url, key)

    cs = sb.table('config_staff').select('*').execute().data
    rates = {r['staff_name'].strip().lower(): float(r['hourly_cost']) for r in cs}
    print("Rates loaded from config_staff:")
    for k, v in rates.items():
        print(f"  • {k}: {v:.2f} €/h")

    print("\nScanning completed bookings in bookings_master...")
    offset = 0
    batch_size = 1000
    rows_to_update = []
    total_completed = 0

    while True:
        res = sb.table('bookings_master') \
            .select('unique_id, staff_name, duration_minutes, net_revenue, commission_fee, material_cost, labor_cost, net_profit') \
            .eq('status_group', 'COMPLETED') \
            .range(offset, offset + batch_size - 1) \
            .execute().data

        if not res:
            break

        total_completed += len(res)
        for r in res:
            s_name = str(r.get('staff_name', '')).strip()
            rate = rates.get(s_name.lower(), 7.50)
            dur = float(r.get('duration_minutes', 0) or 0)
            net_rev = float(r.get('net_revenue', 0) or 0)
            comm = float(r.get('commission_fee', 0) or 0)
            mat = float(r.get('material_cost', 0) or 0)
            old_lc = float(r.get('labor_cost', 0) or 0)
            old_np = float(r.get('net_profit', 0) or 0)

            new_lc = round((dur / 60.0) * rate, 2)
            new_np = round(net_rev - comm - mat - new_lc, 2)

            # Check if values changed by more than 0.005
            if abs(new_lc - old_lc) > 0.005 or abs(new_np - old_np) > 0.005:
                rows_to_update.append({
                    'unique_id': r['unique_id'],
                    'labor_cost': new_lc,
                    'net_profit': new_np
                })

        offset += len(res)
        print(f"Scanned {offset:,} completed bookings (Found {len(rows_to_update):,} needing update)...")
        if len(res) < batch_size:
            break

    print(f"\nScan complete. Total completed: {total_completed:,}. Rows needing update: {len(rows_to_update):,}")

    if not rows_to_update:
        print("All completed bookings are already up-to-date!")
        return

    print("Starting batch update in chunks of 500...")
    chunk_size = 500
    for i in range(0, len(rows_to_update), chunk_size):
        chunk = rows_to_update[i:i + chunk_size]
        sb.table('bookings_master').upsert(chunk, on_conflict='unique_id').execute()
        updated_so_far = min(i + chunk_size, len(rows_to_update))
        print(f"  -> Updated {updated_so_far:,} / {len(rows_to_update):,} ({updated_so_far/len(rows_to_update)*100:.1f}%)")

    print("\n✅ Successfully updated all historical bookings with exact real technician hourly rates!")

if __name__ == '__main__':
    main()
