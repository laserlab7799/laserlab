import os
import shutil

# -----------------------
# HARD-CODED PATHS
# -----------------------

INPUT_ROOT = "/Users/news/Desktop/model-testing/win_prob"
OUTPUT_DIR = "/Users/news/Desktop/model-testing/normalized_json"

RACES = ["H", "P", "G", "S"]

SOURCE_JSON_NAME_BY_RACE = {
    "H": "statewide_plot_data_{}.json",
    "P": "statewide_plot_data_{}.json",
    "G": "statewide_plot_data_{}.json",
    "S": "statewide_plot_data_{}.json",
}

# -----------------------
# UTILS
# -----------------------

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def copy_json(src, dst):
    shutil.copyfile(src, dst)
    print(f"✓ {dst}")

# -----------------------
# MAIN
# -----------------------

def main():
    ensure_dir(OUTPUT_DIR)

    for race in RACES:
        race_dir = os.path.join(INPUT_ROOT, race)
        if not os.path.isdir(race_dir):
            continue

        # -----------------------
        # HOUSE: H / STATE / DISTRICT
        # -----------------------
        if race == "H":
            for state in sorted(os.listdir(race_dir)):
                state_dir = os.path.join(race_dir, state)
                if not os.path.isdir(state_dir):
                    continue

                for district in sorted(os.listdir(state_dir)):
                    district_dir = os.path.join(state_dir, district)
                    if not os.path.isdir(district_dir):
                        continue

                    src = os.path.join(
                        district_dir,
                        SOURCE_JSON_NAME_BY_RACE[race].format(state.lower())
                    )

                    if not os.path.isfile(src):
                        continue

                    out_name = f"H-{state}-{district}.json"
                    dst = os.path.join(OUTPUT_DIR, out_name)

                    copy_json(src, dst)

        # -----------------------
        # STATEWIDE: P / G / S
        # -----------------------
        else:
            for state in sorted(os.listdir(race_dir)):
                state_dir = os.path.join(race_dir, state)
                if not os.path.isdir(state_dir):
                    continue

                src = os.path.join(
                    state_dir,
                    SOURCE_JSON_NAME_BY_RACE[race].format(state.lower())
                )

                if not os.path.isfile(src):
                    continue

                out_name = f"{race}-{state}.json"
                dst = os.path.join(OUTPUT_DIR, out_name)

                copy_json(src, dst)


if __name__ == "__main__":
    main()
