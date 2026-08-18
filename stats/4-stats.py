import csv
import os
from collections import defaultdict

# Configuration
INPUT_FILENAME = "2024_county_results_with_calcs_new.csv"  # Change this if your file is named differently
OUTPUT_FILENAME = "output_statewide_stats.csv"

def safe_float(value):
    """Convert string to float, returning 0.0 on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def safe_int(value):
    """Convert string to int, returning 0 on failure."""
    try:
        return int(float(value)) # float first handles "123.0" strings
    except (ValueError, TypeError):
        return 0

def main():
    # 1. Read the input CSV
    if not os.path.exists(INPUT_FILENAME):
        print(f"Error: {INPUT_FILENAME} not found.")
        return

    print(f"Reading {INPUT_FILENAME}...")
    
    with open(INPUT_FILENAME, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if not rows:
        print("Error: No data found in CSV.")
        return

    # 2. Aggregate Statewide Totals per Timestamp
    # Structure: state_totals[(state, timestamp)] = {'harris': 0, 'trump': 0, 'other': 0, 'total': 0}
    state_totals = defaultdict(lambda: {'harris': 0, 'trump': 0, 'other': 0, 'total': 0})

    print("Aggregating statewide totals...")
    for row in rows:
        state = row.get('state', '').strip()
        timestamp = row.get('timestamp', '').strip()
        
        # Skip rows that don't have essential grouping keys
        if not state or not timestamp:
            continue
            
        key = (state, timestamp)
        
        h_votes = safe_int(row.get('harris_votes', 0))
        t_votes = safe_int(row.get('trump_votes', 0))
        o_votes = safe_int(row.get('other_votes', 0))
        
        state_totals[key]['harris'] += h_votes
        state_totals[key]['trump'] += t_votes
        state_totals[key]['other'] += o_votes
        state_totals[key]['total'] += (h_votes + t_votes + o_votes)

    # 3. Calculate percentages and add columns to rows
    new_columns = [
        'harris_statewide_votes',
        'trump_statewide_votes',
        'other_statewide_votes',
        'harris_statewide_percent',
        'trump_statewide_percent',
        'margin_statewide'
    ]
    
    # Add new column headers to the list
    for col in new_columns:
        if col not in fieldnames:
            fieldnames.append(col)

    print("Calculating percentages and margins...")
    processed_rows = []
    
    for row in rows:
        state = row.get('state', '').strip()
        timestamp = row.get('timestamp', '').strip()
        key = (state, timestamp)
        
        totals = state_totals.get(key)
        
        if totals and totals['total'] > 0:
            # Get totals
            h_tot = totals['harris']
            t_tot = totals['trump']
            o_tot = totals['other']
            grand_total = totals['total']
            
            # Calculate Percentages (0.0 to 1.0 scale)
            h_pct = h_tot / grand_total
            t_pct = t_tot / grand_total
            
            # Margin
            margin = h_pct - t_pct
            
            # Update row
            row['harris_statewide_votes'] = h_tot
            row['trump_statewide_votes'] = t_tot
            row['other_statewide_votes'] = o_tot
            row['harris_statewide_percent'] = f"{h_pct:.6f}" # Store as decimal with precision
            row['trump_statewide_percent'] = f"{t_pct:.6f}"
            row['margin_statewide'] = f"{margin:.6f}"
        else:
            # If no totals found (e.g. empty rows), fill with 0
            row['harris_statewide_votes'] = 0
            row['trump_statewide_votes'] = 0
            row['other_statewide_votes'] = 0
            row['harris_statewide_percent'] = 0
            row['trump_statewide_percent'] = 0
            row['margin_statewide'] = 0

        processed_rows.append(row)

    # 4. Write Output
    print(f"Writing to {OUTPUT_FILENAME}...")
    with open(OUTPUT_FILENAME, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(processed_rows)

    print("Done!")

if __name__ == "__main__":
    main()
