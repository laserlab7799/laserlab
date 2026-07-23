#!/usr/bin/env python3
import math
import os
from copy import deepcopy
import xml.etree.ElementTree as ET
from collections import Counter

INPUT_FILE = "1.xml"          # change if needed
OUTPUT_BASENAME = "1_part"    # will write 1_part01.xml, 1_part02.xml, ...
MAX_FILES = 20                # split into at most this many files

def strip_ns(tag: str) -> str:
    # Turn "{ns}Tag" into "Tag"
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag

def main():
    tree = ET.parse(INPUT_FILE)
    root = tree.getroot()

    # Identify the most common direct child tag under the root
    child_tags = [strip_ns(ch.tag) for ch in list(root)]
    if not child_tags:
        raise SystemExit("No child elements under root; nothing to split.")

    counts = Counter(child_tags)
    repeat_tag, repeat_count = counts.most_common(1)[0]

    if repeat_count <= 1:
        raise SystemExit(
            f"Could not find a repeating top-level element (found '{repeat_tag}' only {repeat_count} time). "
            "This splitter expects many siblings like <Race>…</Race> directly under the root."
        )

    # Partition children into repeating vs non-repeating
    children = list(root)
    repeating = [ch for ch in children if strip_ns(ch.tag) == repeat_tag]
    non_repeating = [ch for ch in children if strip_ns(ch.tag) != repeat_tag]

    n = len(repeating)
    num_files = min(MAX_FILES, n)  # no empty files
    chunk_size = math.ceil(n / num_files)

    # Ensure output names don't collide with input if in same folder
    base_dir = os.path.dirname(os.path.abspath(INPUT_FILE))

    for i in range(num_files):
        chunk = repeating[i * chunk_size : (i + 1) * chunk_size]
        if not chunk:
            continue

        new_root = ET.Element(root.tag, root.attrib)

        # Preserve root text/tail, if present
        new_root.text = root.text
        new_root.tail = root.tail

        # Copy over non-repeating metadata children
        for meta in non_repeating:
            new_root.append(deepcopy(meta))

        # Add this chunk’s repeating elements
        for item in chunk:
            new_root.append(deepcopy(item))

        new_tree = ET.ElementTree(new_root)
        out_name = f"{OUTPUT_BASENAME}{str(i+1).zfill(2)}.xml"
        out_path = os.path.join(base_dir, out_name)
        new_tree.write(out_path, encoding="utf-8", xml_declaration=True)
        print(f"Wrote {out_name} ({len(chunk)} <{repeat_tag}> elements)")

if __name__ == "__main__":
    main()
