import xml.etree.ElementTree as ET

# Path to your XML file
filename = "1.txt"

# Parse the XML
tree = ET.parse(filename)
root = tree.getroot()

totals = {}

# Walk through each ReportingUnit (county)
for ru in root.findall("ReportingUnit"):
    for cand in ru.findall("Candidate"):
        name = f"{cand.get('First','')} {cand.get('Last','')}".strip()
        party = cand.get("Party", "")
        votes = int(cand.get("VoteCount") or 0)

        key = f"{name} ({party})"
        totals[key] = totals.get(key, 0) + votes

# Print results
print("Statewide totals:")
for cand, votes in totals.items():
    print(f"{cand}: {votes:,}")
