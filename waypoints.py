import datetime
from zoneinfo import ZoneInfo
import requests
import json
import re
import os
import zipfile
import io
import glob
import shutil
import geopandas
from tempfile import TemporaryDirectory
import logging

logging.basicConfig(level=logging.INFO)


def store_version(key: str, version: str):
    logging.info(f"{key} version: {version}")
    # "0" is prepended in filename so that this file appears first in Github directory listing
    try:
        with open('waypoints/0versions.json', 'r') as f:
            version_dict = json.load(f)
    except BaseException:
        version_dict = {}
    version_dict[key] = version
    version_dict = dict(sorted(version_dict.items()))
    with open('waypoints/0versions.json', 'w', encoding='UTF-8') as f:
        json.dump(version_dict, f, indent=4)


def normalize_stop_name(name: str) -> str:
    """Normalize a stop name for fuzzy comparison.

    Strips HTML tags, lowercases, removes common suffixes and punctuation.
    """
    if not name:
        return ""
    # Strip HTML tags
    name = re.sub(r"<[^>]+>", "", name)
    # Lowercase
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in [
        "bus terminus", "bus termini", "bus station",
        "station", "terminus", "termini",
        "estate", "estate bus terminus",
        "bus terminus/<br>", "pier",
    ]:
        name = re.sub(rf"\b{re.escape(suffix)}\b", "", name)
    # Remove punctuation and extra whitespace
    name = re.sub(r"[/,()\-\\&'<>]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def name_matches(a: str, b: str) -> bool:
    """Check if two stop names refer to the same stop (fuzzy).

    Uses substring matching on normalized names, requiring at least
    one significant keyword (>=3 chars) to match.
    """
    na = normalize_stop_name(a)
    nb = normalize_stop_name(b)
    if not na or not nb:
        return False
    # Direct substring match
    if na in nb or nb in na:
        return True
    # Keyword-based matching: split into words and check overlap
    words_a = set(w for w in na.split() if len(w) >= 3)
    words_b = set(w for w in nb.split() if len(w) >= 3)
    if not words_a or not words_b:
        return False
    # At least 2 significant words must match, or all words of the shorter name
    overlap = words_a & words_b
    min_words = min(len(words_a), len(words_b))
    return len(overlap) >= min(2, min_words)


def fetch_kmb_route_directions() -> dict:
    """Fetch KMB/LWB route directions from the KMB API.

    Returns a dict: route_name -> {O: {orig, dest}, I: {orig, dest}}
    """
    directions = {}
    try:
        r = requests.get(
            "https://data.etabus.gov.hk/v1/transport/kmb/route/",
            timeout=30,
        )
        r.raise_for_status()
        for route in r.json()["data"]:
            name = route["route"]
            bound = route["bound"]  # "O" or "I"
            if name not in directions:
                directions[name] = {}
            directions[name][bound] = {
                "orig": route["orig_en"],
                "dest": route["dest_en"],
            }
        logging.info(f"Fetched KMB route directions: {len(directions)} routes")
    except Exception as e:
        logging.warning(f"Failed to fetch KMB route directions: {e}")
    return directions


def fetch_ctb_route_directions() -> dict:
    """Fetch CTB route directions from the Citybus API.

    Returns a dict: route_name -> {orig, dest}
    (CTB route list only has one entry per route, showing the outbound direction)
    """
    directions = {}
    try:
        r = requests.get(
            "https://rt.data.gov.hk/v2/transport/citybus/route/ctb",
            timeout=30,
        )
        r.raise_for_status()
        for route in r.json()["data"]:
            name = route["route"]
            directions[name] = {
                "orig": route["orig_en"],
                "dest": route["dest_en"],
            }
        logging.info(f"Fetched CTB route directions: {len(directions)} routes")
    except Exception as e:
        logging.warning(f"Failed to fetch CTB route directions: {e}")
    return directions


def determine_direction(
    properties: dict,
    kmb_directions: dict,
    ctb_directions: dict,
) -> str:
    """Determine the correct O/I direction label for a CSDI route feature.

    Cross-references the operator's API to determine whether the CSDI
    feature represents the outbound (O) or inbound (I) direction.

    Falls back to ROUTE_SEQ-based labeling if the direction cannot be determined.
    """
    route_name = properties.get("ROUTE_NAMEE", "")
    company = properties.get("COMPANY_CODE", "")
    st_stop = properties.get("ST_STOP_NAMEE", "")
    ed_stop = properties.get("ED_STOP_NAMEE", "")
    route_seq = properties.get("ROUTE_SEQ", 1)

    # Default: original behavior (ROUTE_SEQ=1 -> O, else I)
    fallback = "O" if route_seq == 1 else "I"

    # KMB and LWB routes: use KMB API
    if company in ("KMB", "LWB") and route_name in kmb_directions:
        dirs = kmb_directions[route_name]
        outbound = dirs.get("O", {})
        inbound = dirs.get("I", {})

        o_orig = outbound.get("orig", "")
        o_dest = outbound.get("dest", "")
        i_orig = inbound.get("orig", "")
        i_dest = inbound.get("dest", "")

        # Check if CSDI's start stop matches outbound origin
        # and end stop matches outbound destination
        o_start_match = name_matches(st_stop, o_orig)
        o_end_match = name_matches(ed_stop, o_dest)
        i_start_match = name_matches(st_stop, i_orig)
        i_end_match = name_matches(ed_stop, i_dest)

        if o_start_match and o_end_match:
            result = "O"
        elif i_start_match and i_end_match:
            result = "I"
        elif o_start_match and not i_start_match:
            result = "O"
        elif i_start_match and not o_start_match:
            result = "I"
        elif o_end_match and not i_end_match:
            result = "O"
        elif i_end_match and not o_end_match:
            result = "I"
        else:
            # Can't determine - fall back
            result = fallback
            logging.debug(
                f"Could not determine direction for {route_name} "
                f"(ROUTE_ID={properties.get('ROUTE_ID')}, SEQ={route_seq}), "
                f"using fallback {fallback}"
            )
            return fallback

        if result != fallback:
            logging.info(
                f"Corrected direction for {route_name} "
                f"(ROUTE_ID={properties.get('ROUTE_ID')}, COMPANY={company}): "
                f"ROUTE_SEQ={route_seq} was {fallback}, now {result} "
                f"(ST={st_stop[:30]}, ED={ed_stop[:30]}, "
                f"KMB O: {o_orig[:20]} -> {o_dest[:20]}, "
                f"KMB I: {i_orig[:20]} -> {i_dest[:20]})"
            )
        return result

    # CTB routes: use CTB API
    if company == "CTB" and route_name in ctb_directions:
        dirs = ctb_directions[route_name]
        o_orig = dirs.get("orig", "")
        o_dest = dirs.get("dest", "")

        # CTB route list shows outbound direction (orig=origin, dest=destination)
        # If CSDI's start matches CTB's origin -> outbound
        # If CSDI's start matches CTB's destination -> inbound (return)
        o_start_match = name_matches(st_stop, o_orig)
        i_start_match = name_matches(st_stop, o_dest)

        if o_start_match and not i_start_match:
            result = "O"
        elif i_start_match and not o_start_match:
            result = "I"
        else:
            # Try end stop
            o_end_match = name_matches(ed_stop, o_dest)
            i_end_match = name_matches(ed_stop, o_orig)
            if o_end_match and not i_end_match:
                result = "O"
            elif i_end_match and not o_end_match:
                result = "I"
            else:
                return fallback

        if result != fallback:
            logging.info(
                f"Corrected direction for {route_name} "
                f"(ROUTE_ID={properties.get('ROUTE_ID')}, COMPANY=CTB): "
                f"ROUTE_SEQ={route_seq} was {fallback}, now {result} "
                f"(ST={st_stop[:30]}, ED={ed_stop[:30]}, "
                f"CTB outbound: {o_orig[:20]} -> {o_dest[:20]})"
            )
        return result

    # GMB and other companies: fall back to ROUTE_SEQ
    return fallback


os.makedirs("waypoints", exist_ok=True)

# Fetch operator route directions for O/I correction
kmb_directions = fetch_kmb_route_directions()
ctb_directions = fetch_ctb_route_directions()

for csdi_dataset in [
    # 巴士路線
    # https://portal.csdi.gov.hk/geoportal/?lang=zh-hk&datasetId=td_rcd_1638844988873_41214
    {"name": "bus", "id": "td_rcd_1638844988873_41214"},
    # 專線小巴路線
    # https://portal.csdi.gov.hk/geoportal/?lang=zh-hk&datasetId=td_rcd_1697082463580_57453
    {"name": "gmb", "id": "td_rcd_1697082463580_57453"}
]:
    logging.info("csdi_dataset=" + json.dumps(csdi_dataset))
    logging.info("Fetching metadata")
    r = requests.get(
        "https://portal.csdi.gov.hk/geoportal/rest/metadata/item/" +
        csdi_dataset["id"])
    src_id = json.loads(r.content)['_source']['fileid'].replace('-', '')

    logging.info("Fetching FGDB")
    r = requests.get(
        "https://static.csdi.gov.hk/csdi-webpage/download/" + src_id + "/fgdb")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    version = min([f.date_time for f in z.infolist()])
    version = datetime.datetime(
        *version, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    store_version(csdi_dataset["name"], version.isoformat())
    gdb_name = next(s[0:s.index('/')]
                    for s in z.namelist() if s != "__MACOSX")

    with TemporaryDirectory() as tmpdir:
        logging.info("Extracting data")
        z.extractall(tmpdir)
        gdb_path = os.path.join(tmpdir, gdb_name)
        logging.info("Reading data (1)")
        gdf = geopandas.read_file(gdb_path, encoding='utf-8')
        logging.info("Transforming data")
        gdf.to_crs(epsg=4326, inplace=True)
        logging.info("Reading data (2)")
        data = gdf.to_geo_dict(drop_id=True)

    logging.info("Storing data")
    for feature in data["features"]:
        properties = feature["properties"]
        direction = determine_direction(
            properties, kmb_directions, ctb_directions)
        with open("waypoints/" + str(properties["ROUTE_ID"]) + "-" + direction + ".json", "w", encoding='utf-8') as f:
            f.write(
                re.sub(
                    r"([0-9]+\.[0-9]{5})[0-9]+",
                    r"\1",
                    json.dumps({
                        "features": [feature],
                        "type": "FeatureCollection"
                    },
                        ensure_ascii=False,
                        separators=(",", ":")
                    )
                )
            )


logging.info("Copying static data")
for file in glob.glob(r'./mtr/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./lrt/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./ferry/*.json'):
    shutil.copy(file, "waypoints")
