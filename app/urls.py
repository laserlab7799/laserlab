NEW_ENGLAND = {"ME", "NH", "VT", "MA", "CT", "RI"}

ALL_STATES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","IA","ID","IL","IN","KS","KY",
    "LA","MA","MD","ME","MI","MN","MO","MS","MT","NC","ND","NE","NH","NJ","NM","NV","NY",
    "OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VA","VT","WA","WI","WV","WY","DC","PR"
]

STATES = [s for s in ALL_STATES if s not in NEW_ENGLAND]

HISTORY_TIMESTAMPS = [
    "2024-11-06T02:35:22.000Z",
    "2024-11-06T03:35:22.000Z",
    "2024-11-06T04:35:22.000Z",
    "2024-11-06T05:35:22.000Z",
    "2024-11-06T06:35:22.000Z",
    "2024-11-06T12:35:22.000Z",
    "2024-11-07T02:35:22.000Z",
    "2024-11-12T02:35:22.000Z",
]

BASE = (
    "https://api.ap.org/v3/elections/2024-11-05"
    "?testID=20241105"
    "&statepostal={state}"
    "&raceTypeID=G"
    "&level=ru"
    "&historyDateTime={ts}"
)

OUT_FILE = "full_data_urls.txt"

lines = []

for ts in HISTORY_TIMESTAMPS:
    for state in STATES:
        url = BASE.format(state=state, ts=ts)
        lines.append(
            f'os.getenv("FULL_DATA_URL_1", '
            f'os.getenv("FULL_DATA_URL", "{url}")),'
        )

with open(OUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print(f"Wrote {len(lines)} lines to {OUT_FILE}")
