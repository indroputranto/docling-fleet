"""
Combined clause audit script for all vessels (Batch 1 + Batch 2).
Compares source .docx files against output .json files.
"""

import os
import json
import re
from pathlib import Path
from docx import Document

SOURCE_DIR = "/sessions/elegant-funny-albattani/mnt/docling/source/vessels"
OUTPUT_DIR = "/sessions/elegant-funny-albattani/mnt/docling/output/vessels"

VESSELS = {
    # ── Batch 2 ──────────────────────────────────────────────────────────────
    "MV ADRIATIC":        ("AI_ADRIATIC.docx",              "MV ADRIATIC"),
    "MV ATLANTIC":        ("AI_ATLANTIC.docx",              "MV ATLANTIC"),
    "MV ATLANTIC DAWN":   ("AI_ATLANTIC_DAWN.docx",         "MV ATLANTIC DAWN"),
    "MV BALTIC TIMBER":   ("AI_BALTIC_TIMBER_FINAL.docx",   "AI_BALTIC_TIMBER_FINAL"),
    "MV BALTIC IRON":     ("AI_BALTIC_IRON_FINAL.docx",     "MV BALTIC IRON"),
    "MV BOTHNIAMAR":      ("AI_BOTHNIAMAR.docx",            "BOTHNIAMAR"),
    "MV CELTIC":          ("AI_CELTIC.docx",                "MV CELTIC"),
    "MV CORSICA":         ("AI_CORISCA.docx",               "MV CORSICA"),
    "MV EENDRACHT":       ("AI_EENDRACHT.docx",             "MV EENDRACHT"),
    "MV FAST ANNA SOFIA": ("AI_FAST_ANNA_SOFIA.docx",       "MV FAST ANNA SOFIA"),
    "MV FWN SEA":         ("AI_FWN_SEA.docx",               "MV FWN SEA"),
    "MV GERD SIBUM":      ("AI_GERD_SIBUM_FINAL.docx",      "MV GERD SIBUM"),
    "MV HAMMARLAND":      ("AI_HAMMARLAND.docx",            "MV HAMMARLAND"),
    "MV HEINZ":           ("AI_HEINZ.docx",                 "HEINZ"),
    "MV JESPER":          ("AI_JESPER.docx",                "JESPER"),
    "MV LADY LUCIANA":    ("AI_LADY_LUCIANA.docx",          "MV LADY LUCIANA"),
    "MV LANGELAND":       ("AI_LANGELAND.docx",             "MV LANGELAND"),
    "MV LUCA":            ("AI_LUCA.docx",                  "MV LUCA"),
    "MV MARIA SIBUM":     ("AI_MARIA_SIBUM_FINAL.docx",     "MV MARIA SIBUM"),
    "MV MARLON":          ("AI_MARLON.docx",                "MV M V MARLON"),
    "MV NORDIC":          ("AI_NORDIC.docx",                "MV NORDIC"),
    "MV NORDIC FELICIA":  ("AI_NORDIC_FELICIA.docx",        "MV NORDIC FELICIA"),
    "MV OCEANIC":         ("AI_OCEANIC.docx",               "MV OCEANIC"),
    "MV PACIFIC DAWN":    ("AI_PACIFICDAWN.docx",           "MV PACIFIC DAWN"),
    "MV PACIFIC VENTURE": ("AI_PACIFIC_VENTURE.docx",       "MV PACIFIC VENTURE"),
    "MV PANTHERA J":      ("AI_PANTHERA_J.docx",            "MV PANTHERA J"),
    "MV RIJNVLIET":       ("AI_RIJNVLIET.docx",             "MV RIJNVLIET"),
    "MV VEGA CHRISTINA":  ("AI_VEGA_CHRISTINA.docx",        "MV VEGA CHRISTINA"),
    "MV VEGA PHILIPPA":   ("AI_VEGA_PHILIPPA.docx",         "MV VEGA PHILIPPA"),
    "VIRUMAA":            ("AI_VIRUMAA.docx",               "VIRUMAA"),
    # ── Batch 1 (original 19) ────────────────────────────────────────────────
    "MV MORGENSTOND II":  ("AI_MORGENSTOND_II.docx",        "MV MORGENSTOND II"),
    "MV SLOMAN DISPATCHER": ("AI_SLOMANDISPATCHER.docx",    "MV SLOMAN DISPATCHER"),
    "MV SLOMAN DISCHARGER": ("AI_SLOMANDISCHARGER.docx",    "MV SLOMAN DISCHARGER"),
    "MV OCEAN7 RANGER":   ("AI_OCEAN7_RANGER.docx",         "MV OCEAN7 RANGER"),
    "MV INDUSTRIAL RUBY": ("AI_INDUSTRIALRUBY.docx",        "MV INDUSTRIAL RUBY"),
    "MV OCEAN7 REVOLUTION": ("AI_OCEAN7_REVOLUTION.docx",   "MV OCEAN7 REVOLUTION"),
    "MV OCEAN7 ROYAL":    ("AI_OCEAN7_ROYAL.docx",          "MV OCEAN7 ROYAL"),
    "MV RUDOLF":          ("AI_RUDOLF.docx",                "MV RUDOLF"),
    "MV OCEAN7 MUGA":     ("AI_OCEAN7_MUGA.docx",           "MV OCEAN7 MUGA"),
    "MV O7 LAFITE":       ("AI_O7LAFITE.docx",              "MV O7 LAFITE"),
    "MV FREDENSBORG":     ("AI_FREDENSBORG.docx",           "MV FREDENSBORG"),
    "MV OCEAN7 ALGORA":   ("AI_OCEAN7_ALGORA.docx",         "MV OCEAN7 ALGORA"),
    "MV O7 GAJA":         ("AI_O7GAJA.docx",                "MV O7 GAJA"),
    "MV INDUSTRIAL CONSTANT": ("AI_INDUSTRIAL_CONSTANT.docx", "MV INDUSTRIAL CONSTANT"),
    "MV EVA MARIE":       ("AI_EVA_MARIE.docx",             "MV EVA MARIE"),
    "MV FRANZISKA":       ("AI_FRANZISKA.docx",             "MV FRANZISKA"),
    "MV ARA ROTTERDAM":   ("AI_ARA_ROTTERDAM.docx",         "MV ARA ROTTERDAM"),
    "O7 ALSACE":          ("AI_O7_ALSACE.docx",             "O7_ALSACE"),
    "MV TITUS":           ("AI_TITUS.docx",                 "MV TITUS"),
}


def find_source_file(vessel_name, hint_filename):
    path = os.path.join(SOURCE_DIR, hint_filename)
    if os.path.exists(path):
        return path
    # fuzzy fallback
    vessel_upper = vessel_name.replace("MV ", "").replace(" ", "_").upper()
    for fname in os.listdir(SOURCE_DIR):
        if fname.startswith("~$"):
            continue
        fname_upper = fname.upper().replace("AI_", "").replace(".DOCX", "")
        if vessel_upper in fname_upper or fname_upper in vessel_upper:
            return os.path.join(SOURCE_DIR, fname)
    return None


def find_json_file(output_folder_name):
    folder = os.path.join(OUTPUT_DIR, output_folder_name)
    if not os.path.exists(folder):
        return None
    for fname in os.listdir(folder):
        if fname.endswith("_data.json"):
            return os.path.join(folder, fname)
    return None


def extract_source_clauses(docx_path):
    doc = Document(docx_path)
    all_paras = [(p.text.strip(), p.style.name if p.style else "") for p in doc.paragraphs if p.text.strip()]

    results = {
        "charter_party": [],
        "fixture_recap": [],
        "addendum": [],
        "all_headings": [],
        "structure_notes": [],
    }

    cp_start = fixture_start = addendum_start = None
    for i, (text, style) in enumerate(all_paras):
        tl = text.lower()
        if re.search(r'2\.3\s+charter|^2\.3\s+charter|^charter.party$', tl):
            cp_start = i
        elif re.search(r'2\.2\s+fixture|^fixture recap$', tl):
            fixture_start = i
        elif re.search(r'2\.1\s+addend|^addendum$', tl):
            addendum_start = i

    results["structure_notes"].append(
        f"CP starts at para {cp_start}, Fixture at {fixture_start}, Addendum at {addendum_start}"
    )

    # Charter party clauses
    if cp_start is not None:
        for i in range(cp_start + 1, len(all_paras)):
            text, style = all_paras[i]
            sl = style.lower()
            if re.match(r'^3[\.\)]\s', text) and "heading" in sl:
                break
            if "heading" in sl:
                results["charter_party"].append((text, style))
            elif re.match(r'^\d+[\.\)]\s+[A-Z]', text) and len(text) < 200:
                results["charter_party"].append((text, style + " [numbered-normal]"))
    else:
        results["structure_notes"].append("WARNING: No explicit Charter Party section found")
        for text, style in all_paras:
            if "heading" in style.lower() and re.match(r'^\d+[\.\)]\s', text):
                results["charter_party"].append((text, style))

    # Fixture recap
    if fixture_start is not None:
        end = cp_start if cp_start and cp_start > fixture_start else len(all_paras)
        for i in range(fixture_start + 1, end):
            text, style = all_paras[i]
            if "heading" in style.lower():
                results["fixture_recap"].append((text, style))
            elif re.match(r'^fixture recap\s*[-–]', text, re.IGNORECASE):
                results["fixture_recap"].append((text, style + " [fr-item]"))

    # Addendum
    if addendum_start is not None:
        end = min(
            fixture_start if fixture_start and fixture_start > addendum_start else len(all_paras),
            cp_start if cp_start and cp_start > addendum_start else len(all_paras),
        )
        for i in range(addendum_start + 1, end):
            text, style = all_paras[i]
            if "heading" in style.lower():
                results["addendum"].append((text, style))

    results["all_headings"] = [(t, s) for t, s in all_paras if "heading" in s.lower()]
    return results


def extract_json_clauses(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = {
        "charter_party": {},
        "fixture_recap": {},
        "addendum": {},
        "structure_notes": [],
        "has_sub_chapters": False,
    }

    chapters = data.get("chapters", {})
    contract_details = chapters.get("2_contract_details", {})
    sub_chapters = contract_details.get("sub_chapters", {})

    if not sub_chapters:
        content = contract_details.get("content", [])
        result["structure_notes"].append(
            f"No sub_chapters — basic structure with {len(content)} content lines"
        )
        return result

    result["has_sub_chapters"] = True

    for section in ("charter_party", "fixture_recap", "addendum"):
        sec = sub_chapters.get(section, {})
        for key, val in sec.get("clauses", {}).items():
            result[section][key] = {
                "title": val.get("title", ""),
                "content_count": len(val.get("content", [])),
                "content_empty": len(val.get("content", [])) == 0,
            }

    # Also check rider_clause under charter_party
    cp_sec = sub_chapters.get("charter_party", {})
    rider = cp_sec.get("rider_clause", {})
    rider_clauses = rider.get("clauses", {}) if rider else {}
    result["rider_clause_count"] = len(rider_clauses)

    result["structure_notes"].append(
        f"CP: {len(result['charter_party'])} clauses, "
        f"FR: {len(result['fixture_recap'])} clauses, "
        f"ADD: {len(result['addendum'])} clauses, "
        f"Rider: {result['rider_clause_count']} clauses"
    )
    return result


def compare_vessel(vessel_name, source_file, output_folder):
    report = {
        "vessel": vessel_name,
        "source_file": source_file,
        "output_folder": output_folder,
        "status": "OK",
        "errors": [],
        "warnings": [],
        "missing_cp_clauses": [],
        "empty_cp_clauses": [],
        "missing_fixture_clauses": [],
        "missing_addendum_clauses": [],
        "json_cp_count": 0,
        "source_cp_count": 0,
        "json_fr_count": 0,
        "source_fr_count": 0,
        "json_add_count": 0,
        "source_add_count": 0,
        "rider_clause_count": 0,
        "source_notes": [],
        "details": [],
    }

    src_path = find_source_file(vessel_name, source_file)
    if not src_path:
        report["status"] = "ERROR"
        report["errors"].append(f"Source file not found: {source_file}")
        return report
    report["source_file"] = os.path.basename(src_path)

    json_path = find_json_file(output_folder)
    if not json_path:
        report["status"] = "ERROR"
        report["errors"].append(f"Output JSON not found in folder: {output_folder}")
        return report

    try:
        src = extract_source_clauses(src_path)
    except Exception as e:
        report["status"] = "ERROR"
        report["errors"].append(f"Failed to read source docx: {e}")
        return report

    try:
        jsn = extract_json_clauses(json_path)
    except Exception as e:
        report["status"] = "ERROR"
        report["errors"].append(f"Failed to read JSON: {e}")
        return report

    report["source_notes"] = src["structure_notes"] + jsn["structure_notes"]
    report["source_cp_count"] = len(src["charter_party"])
    report["json_cp_count"] = len(jsn["charter_party"])
    report["source_fr_count"] = len(src["fixture_recap"])
    report["json_fr_count"] = len(jsn["fixture_recap"])
    report["source_add_count"] = len(src["addendum"])
    report["json_add_count"] = len(jsn["addendum"])
    report["rider_clause_count"] = jsn.get("rider_clause_count", 0)

    # Build numbered maps for CP comparison
    src_map = {}
    for text, style in src["charter_party"]:
        m = re.match(r'^(\d+)[\.\)]\s*(.+)$', text)
        if m:
            src_map[int(m.group(1))] = text

    json_map = {}
    for key, val in jsn["charter_party"].items():
        m = re.match(r'\d+', key.replace("clause_", ""))
        if m:
            json_map[int(m.group())] = val

    for num, src_title in src_map.items():
        if num not in json_map:
            report["missing_cp_clauses"].append(f"Clause {num}: {src_title[:80]}")
        elif json_map[num]["content_empty"]:
            report["empty_cp_clauses"].append(f"Clause {num}: {src_title[:80]} [empty content]")

    # Fixture recap
    if src["fixture_recap"] and not jsn["fixture_recap"]:
        report["missing_fixture_clauses"].append(
            f"Fixture recap section has {len(src['fixture_recap'])} items in source but 0 clauses in JSON"
        )

    # Addendum
    if src["addendum"] and not jsn["addendum"]:
        report["missing_addendum_clauses"].append(
            f"Addendum section has {len(src['addendum'])} items in source but 0 clauses in JSON"
        )

    issues = (
        report["missing_cp_clauses"]
        + report["empty_cp_clauses"]
        + report["missing_fixture_clauses"]
        + report["missing_addendum_clauses"]
    )
    if issues:
        report["status"] = "ISSUES FOUND"
    elif report["source_cp_count"] == 0 and not jsn["has_sub_chapters"]:
        report["status"] = "NO CP SECTION"

    return report


if __name__ == "__main__":
    print("Running combined audit for all vessels...\n")
    results = []
    for vessel_name, (src_file, out_folder) in VESSELS.items():
        r = compare_vessel(vessel_name, src_file, out_folder)
        results.append(r)
        tag = f"[{r['status']}]" if r["status"] != "OK" else ""
        print(f"  {vessel_name} {tag}")

    ok = [r for r in results if r["status"] == "OK"]
    issues = [r for r in results if r["status"] == "ISSUES FOUND"]
    no_cp = [r for r in results if r["status"] == "NO CP SECTION"]
    errors = [r for r in results if r["status"] == "ERROR"]

    print(f"\nTotal: {len(results)} | OK: {len(ok)} | Issues: {len(issues)} | No CP: {len(no_cp)} | Errors: {len(errors)}")

    for r in issues + errors:
        print(f"\n  {r['vessel']} [{r['status']}]")
        for e in r["errors"]:
            print(f"    ERROR: {e}")
        for m in r["missing_cp_clauses"]:
            print(f"    MISSING CP: {m}")
        for m in r["empty_cp_clauses"]:
            print(f"    EMPTY CP: {m}")
        for m in r["missing_fixture_clauses"]:
            print(f"    MISSING FR: {m}")
        for m in r["missing_addendum_clauses"]:
            print(f"    MISSING ADD: {m}")
        print(f"    Source CP: {r['source_cp_count']} | JSON CP: {r['json_cp_count']} | Rider: {r['rider_clause_count']}")
