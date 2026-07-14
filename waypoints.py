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


def _route_directions(
    route_name: str,
    company: str,
    kmb_directions: dict,
    ctb_directions: dict,
):
    """Return (o_orig, o_dest, i_orig, i_dest) for a route, or None if unknown."""
    if company in ("KMB", "LWB") and route_name in kmb_directions:
        dirs = kmb_directions[route_name]
        outbound = dirs.get("O", {})
        inbound = dirs.get("I", {})
        if outbound or inbound:
            return (outbound.get("orig", ""), outbound.get("dest", ""),
                    inbound.get("orig", ""), inbound.get("dest", ""))
    if company == "CTB" and route_name in ctb_directions:
        dirs = ctb_directions[route_name]
        # CTB's route list only carries the outbound leg; the inbound leg is
        # its reverse (origin/destination swapped).
        return (dirs.get("orig", ""), dirs.get("dest", ""),
                dirs.get("dest", ""), dirs.get("orig", ""))
    return None


def _direction_score(properties: dict, orig: str, dest: str) -> int:
    """Score 0-2 for how well a feature's start/end match a given orig/dest."""
    score = 0
    if orig and name_matches(properties.get("ST_STOP_NAMEE", ""), orig):
        score += 1
    if dest and name_matches(properties.get("ED_STOP_NAMEE", ""), dest):
        score += 1
    return score


def assign_directions(
    features: list,
    kmb_directions: dict,
    ctb_directions: dict,
) -> list:
    """Assign an O/I label to every feature of a single route, together.

    The operators' outbound/inbound convention is not tracked by CSDI's
    ROUTE_SEQ, so labelling by ROUTE_SEQ alone swaps some routes (issue #14).
    This resolves a route's features *jointly* against the operator API:

    * a two-direction route always gets exactly one O and one I -- the pair
      is placed in whichever orientation best matches the operator's real
      origin/destination, so it can never stamp both features the same label
      and overwrite one direction's file;
    * a single-direction route is labelled by whichever direction it matches.

    Falls back to the original ROUTE_SEQ behaviour whenever the operator data
    is unavailable or can't disambiguate, so no route regresses below status quo.
    """
    def seq_label(feature: dict) -> str:
        return "O" if feature["properties"].get("ROUTE_SEQ", 1) == 1 else "I"

    if not features:
        return []

    props0 = features[0]["properties"]
    dirs = _route_directions(
        props0.get("ROUTE_NAMEE", ""), props0.get("COMPANY_CODE", ""),
        kmb_directions, ctb_directions)

    # GMB / unknown route / API down: keep the original behaviour verbatim.
    if dirs is None:
        return [seq_label(f) for f in features]
    o_orig, o_dest, i_orig, i_dest = dirs

    if len(features) == 1:
        props = features[0]["properties"]
        so = _direction_score(props, o_orig, o_dest)
        si = _direction_score(props, i_orig, i_dest)
        if so != si:
            return ["O" if so > si else "I"]
        return [seq_label(features[0])]

    if len(features) == 2:
        a, b = features
        keep = (_direction_score(a["properties"], o_orig, o_dest)
                + _direction_score(b["properties"], i_orig, i_dest))
        swap = (_direction_score(a["properties"], i_orig, i_dest)
                + _direction_score(b["properties"], o_orig, o_dest))
        if keep != swap:
            return ["O", "I"] if keep > swap else ["I", "O"]
        # Tie: fall back to ROUTE_SEQ, but keep the pair complementary so
        # neither file overwrites the other.
        first = seq_label(a)
        return [first, "I" if first == "O" else "O"]

    # More than two features for one route is unexpected; keep it safe.
    return [seq_label(f) for f in features]


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
    features_by_route = {}
    for feature in data["features"]:
        features_by_route.setdefault(
            feature["properties"]["ROUTE_ID"], []).append(feature)

    for route_id, route_features in features_by_route.items():
        directions = assign_directions(
            route_features, kmb_directions, ctb_directions)
        for feature, direction in zip(route_features, directions):
            with open("waypoints/" + str(route_id) + "-" + direction + ".json", "w", encoding='utf-8') as f:
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
