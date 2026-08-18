def _parse_full_dataset(xml_text: str):
    """
    Parse a nation-wide /v3/elections/<date> XML into the cache_by_combo shape:
      { "<OFFICE>:<TypeID>": { "states": { "CA": {...}, ... }, "updated": ts } }
    Supports OfficeID in P,S,G,A,M (state+county) and H (district).
    """
    out = {}  # combo -> {"states": {USPS: blob}, "updated": ts}

    if not _looks_like_xml(xml_text):
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    # iterate over all races in the file
    for race in root.findall(".//Race"):
        office = (race.attrib.get("OfficeID") or "").upper()   # P, S, G, A, M, H, ...
        race_type = (race.attrib.get("TypeID") or race.attrib.get("TypeId") or "G").upper()
        combo = _combo_key(office, race_type)
        bucket = out.setdefault(combo, {"states": {}, "updated": time.time()})

        # Prefer the state-level ReportingUnit for USPS, percent, and winner
        state_ru = race.find("./ReportingUnit[@Level='state']") \
                   or race.find("./ReportingUnit[@ReportingUnitLevel='1']")
        if state_ru is None:
            # If no clear state RU, skip this race
            continue

        usps = (state_ru.attrib.get("StatePostal") or "").upper()
        if not usps:
            continue
        
                # --- choose the best race per (office, race_type, state): keep only the one with highest state RU votes
        # --- choose the best race per (office, race_type, state) ---
        if office != "H":
            this_total_for_choice = _sum_state_ru_votes(state_ru)

            st_existing = bucket["states"].get(usps)
            if st_existing is not None:
                prev_best = int(st_existing.get("_best_state_total") or -1)
                # If this race has fewer or equal state votes than what we already kept, skip it
                if this_total_for_choice <= prev_best:
                    continue

            # Prefer this race: re-init statewide blob and clear prior county payloads
            st = bucket["states"].setdefault(usps, {
                "updated": time.time(),
                "office": office,
                "percent_in": None,
            })
            # For statewide offices, clearing previous subunits is correct
            st.pop("counties", None)
            st.pop("districts", None)
            st["_best_state_total"] = this_total_for_choice  # internal marker
        else:
            # House: keep all districts — never clear previously parsed ones
            st = bucket["states"].setdefault(usps, {
                "updated": time.time(),
                "office": office,
                "percent_in": None,
            })
            # Note: do NOT touch st["districts"] here; we'll add to it below


        
        
        
        # --- shared race-level bits
        state_status = (race.attrib.get("RaceCallStatus") or "No Decision").strip()
        percent = race.attrib.get("EEVP") or state_ru.attrib.get("EEVP")
        if not percent:
            prec = state_ru.find("./Precincts")
            if prec is not None:
                percent = prec.attrib.get("ReportingPct")

        # Winner from the state RU
        winner_payload = None
        if state_status == "Called":
            w_node = next((c for c in state_ru.findall("./Candidate")
                           if (c.attrib.get("Winner") or "").upper() == "X"), None)
            if w_node is not None:
                first = (w_node.attrib.get("First") or "").strip()
                last  = (w_node.attrib.get("Last")  or "").strip()
                party = _norm_party(w_node.attrib.get("Party"))
                nm    = (first + " " + last).strip()
                winner_payload = {"name": nm or None, "party": party or None}

        # Ensure a state blob for this office/type
        st = bucket["states"].setdefault(usps, {
            "updated": time.time(),
            "office": office,
            "percent_in": None,
        })
        st["percent_in"] = percent or st.get("percent_in")
        # --- Duplicate any W (runoff/special-coded) as S bucket as well ---


        if office == "H":
            # ---- House: each <Race> is a single district for a state
            # Canonical district id from USPS + SeatNum
            seat_num = str(race.attrib.get("SeatNum") or race.attrib.get("SeatNumber") or "").strip().zfill(2)
            if not seat_num:
                # fallback: District on RU (rare)
                seat_num = (state_ru.attrib.get("District") or "").strip().zfill(2)
            if not seat_num:
                # cannot place district
                continue

            statefp = USPS_TO_STATEFP.get(usps, "")
            did = (statefp + seat_num) if statefp else seat_num

            # Collect candidates from the state RU
            cands, total = [], 0
            for c in state_ru.findall("./Candidate"):
                first = (c.attrib.get("First") or "").strip()
                last  = (c.attrib.get("Last")  or "").strip()
                party = _norm_party(c.attrib.get("Party"))
                try:
                    votes = int(c.attrib.get("VoteCount") or "0")
                except Exception:
                    votes = 0
                total += votes
                cands.append({
                    "name": (first + " " + last).strip(),
                    "party": party,
                    "votes": votes,
                    "winner": (c.attrib.get("Winner","").upper() == "X")
                })

            st.setdefault("districts", {})
            st["districts"][did] = {
                "state": usps,
                "district_id": did,
                "district_num": seat_num,
                "name": race.attrib.get("SeatName") or f"District {seat_num}",
                "candidates": cands,
                "total": total,
                "percent_in": percent,
                "race_call": {
                    "status": state_status,
                    "winner": winner_payload
                }
            }
            # --- Duplicate any W (runoff/special-coded) as S bucket as well ---
            if (race_type or "").upper() == "W":
                dup_key = _combo_key(office, "S")
                s_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                # Deep-copy the state blob so we don't share references
                s_bucket["states"][usps] = json.loads(json.dumps(st))
                s_bucket["updated"] = time.time()


            # House overrides (by state) after we have at least one district
            _apply_house_overrides(usps, st.get("districts") or {}, office, race_type)
            
            # If Louisiana House is TypeID 'L' (LA jungle), also store into H:G
            if office == "H" and usps == "LA" and race_type in ("L", "NP", "X"):
                dup_key = _combo_key("H", "G")
                h_g_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                # Deep copy the LA state blob so we don't share references
                h_g_bucket["states"]["LA"] = json.loads(json.dumps(st))
                h_g_bucket["updated"] = time.time()


        else:
            # ---- Statewide offices (P, S, G, A, M ...): New England = statewide sum of subunits; NO counties ----
            st["race_call"] = {
                "status": state_status,
                "winner": winner_payload,
                "source": "api"
            }

            # Default topline from state RU
            state_topline, state_total = [], 0
            for c in state_ru.findall("./Candidate"):
                first = (c.attrib.get("First") or "").strip()
                last  = (c.attrib.get("Last")  or "").strip()
                party = _norm_party(c.attrib.get("Party"))
                try:
                    votes = int(c.attrib.get("VoteCount") or "0")
                except Exception:
                    votes = 0
                state_total += votes
                state_topline.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})

            # === NEW ENGLAND override: recompute statewide topline from subunits and SKIP counties ===
# === NEW ENGLAND: use API state RU topline exactly; do NOT aggregate subunits/counties ===
            if usps in NEW_ENGLAND:
                # (state_topline/state_total were just built above from the state RU)
                st["state_topline"] = state_topline
                st["state_total"]   = state_total

                # Carry state percent_in from race/state RU if present
                if not st.get("percent_in"):
                    if state_ru is not None:
                        pct = state_ru.attrib.get("EEVP")
                        if not pct:
                            prec = state_ru.find("./Precincts")
                            if prec is not None:
                                pct = prec.attrib.get("ReportingPct")
                        if pct:
                            st["percent_in"] = pct

                # Suppress county/subunit payload for NE
                st["counties"] = {}

                # Apply any state-level override after we set the RU topline
                _apply_psg_override(usps, st, office, race_type)

                # Duplicate W→S if needed
                if (race_type or "").upper() == "W":
                    dup_key = _combo_key(office, "S")
                    s_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                    s_bucket["states"][usps] = json.loads(json.dumps(st))
                    s_bucket["updated"] = time.time()

                # Timestamps & continue (skip normal counties path)
                st["updated"] = time.time()
                bucket["updated"] = time.time()
                continue


            # --- Non-NE states: keep your existing counties/subunits logic ---
            st["state_topline"] = state_topline
            st["state_total"]   = state_total

            st.setdefault("counties", {})

            # Pull RUs scoped to this Race (prefer Level='subunit' / ReportingUnitLevel='2')
            rus = race.findall("./ReportingUnit[@Level='subunit']") \
                or race.findall("./ReportingUnit[@ReportingUnitLevel='2']")
            if not rus:
                # Fallback: any RU with a FIPSCode under this race
                rus = [ru for ru in race.findall("./ReportingUnit") if ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS")]

            if not rus:
                # no county-level info for this state/race
                continue

            # First pass: FIPS values that have a county RU (ReportingUnitLevel == "2")
            fips_with_level2 = set()
            for ru in rus:
                lvl = (ru.attrib.get("ReportingUnitLevel") or "").strip()
                if lvl == "2":
                    f = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
                    if f and f.isdigit() and len(f) < 5:
                        f = f.zfill(5)
                    if f and f != "00000":
                        fips_with_level2.add(f)

            county_aggs = {}  # fips -> {"name": best_name, "cands": {(name,party): votes}}
            county_subs = {}  # fips -> { ru_id: {name, percent_in, candidates[], total} }

            for ru in rus:
                fips = (ru.attrib.get("FIPSCode") or ru.attrib.get("FIPS") or "").strip()
                if fips and fips.isdigit() and len(fips) < 5:
                    fips = fips.zfill(5)
                if not fips or fips == "00000":
                    continue

                # For NYC Mayor, keep only boroughs
                if office == "M" and usps == "NY" and fips not in NYC_BOROUGH_FIPS:
                    continue

                ru_level = (ru.attrib.get("ReportingUnitLevel") or "").strip()

                ru_id   = (ru.attrib.get("ID") or ru.attrib.get("ReportingUnitID") or "").strip()
                ru_name = (ru.attrib.get("Name") or f"FIPS {fips}").strip()

                # percent at RU level
                ru_percent = ru.attrib.get("EEVP")
                if not ru_percent:
                    prec = ru.find("./Precincts")
                    if prec is not None:
                        ru_percent = prec.attrib.get("ReportingPct")

                # candidates for this RU
                ru_cands, ru_total = [], 0
                for c in ru.findall("./Candidate"):
                    first = (c.attrib.get("First") or "").strip()
                    last  = (c.attrib.get("Last")  or "").strip()
                    party = _norm_party(c.attrib.get("Party"))
                    try:
                        votes = int(c.attrib.get("VoteCount") or "0")
                    except Exception:
                        votes = 0
                    ru_total += votes
                    ru_cands.append({"name": (first + " " + last).strip(), "party": party, "votes": votes})

                # init per-county holders
                ca = county_aggs.setdefault(fips, {"name": None, "cands": {}})
                # NEW: track the best county-level %in seen for this FIPS (use max of available RUs)
                if ru_percent is not None:
                    try:
                        p = float(ru_percent)
                        prev = ca.get("percent")
                        ca["percent"] = p if prev is None else max(prev, p)
                    except Exception:
                        pass

                cs = county_subs.setdefault(fips, {})

                # prefer a county-looking label if we ever see one; otherwise keep first RU name
                if ca["name"] is None or ("County" in ru_name and "County" not in (ca["name"] or "")):
                    ca["name"] = ru_name

                # Only aggregate if:
                #   - this RU is a county (level 2), OR
                #   - there is no county RU at all for this FIPS
                should_aggregate = (ru_level == "2") or (fips not in fips_with_level2)

                if should_aggregate:
                    if ru_percent and (ru_level == "2" or ca["percent"] is None):
                        ca["percent"] = ru_percent

                    for rc in ru_cands:
                        key = (rc["name"], rc["party"])
                        ca["cands"][key] = ca["cands"].get(key, 0) + rc["votes"]

                # retain the fine-grain RU under `subunits`
                if ru_id:
                    cs[ru_id] = {
                        "name": ru_name,
                        "percent_in": ru_percent,
                        "candidates": ru_cands,
                        "total": ru_total
                    }

            # write county aggregates + subunit breakdowns to the state blob
            for fips, agg in county_aggs.items():
                cands = [{"name": n, "party": p, "votes": v}
                         for (n, p), v in agg["cands"].items()]
                cands.sort(key=lambda x: x["votes"], reverse=True)
                st["counties"][fips] = {
                    "state": usps,
                    "fips": fips,
                    "name": agg["name"] or f"FIPS {fips}",
                    "candidates": cands,
                    "total": sum(c["votes"] for c in cands),
                    "percent_in": agg.get("percent"),
                    "subunits": county_subs.get(fips, {})
                }



            # Apply any state-level override calls
            _apply_psg_override(usps, st, office, race_type)
            if (race_type or "").upper() == "W":
                dup_key = _combo_key(office, "S")
                s_bucket = out.setdefault(dup_key, {"states": {}, "updated": time.time()})
                s_bucket["states"][usps] = json.loads(json.dumps(st))  # deep copy of fully-populated blob
                s_bucket["updated"] = time.time()

        # refresh per-state timestamp each time we touch it
        st["updated"] = time.time()

        # bump combo bucket updated
        bucket["updated"] = time.time()

    return out
