import datetime
from zoneinfo import ZoneInfo
import requests
import json
import re
import os
import zipfile
import io
import glob
import math
import shutil
import geopandas
from collections import defaultdict
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


# O/I direction (issue #14): ROUTE_SEQ doesn't track the operators' outbound/
# inbound, so labelling by it swaps some routes. Resolve by ID instead: hkbus's
# routeFareList gtfsId == CSDI ROUTE_ID gives each direction's bound + first stop;
# the feature whose line starts nearest a direction's first stop takes
# that bound.
ROUTE_FARE_LIST_URL = "https://data.hkbus.app/routeFareList.json"
_CO_ALIAS = {
    "LWB": "kmb",
    "KMB": "kmb",
    "CTB": "ctb",
    "NLB": "nlb",
    "GMB": "gmb"}
_MAX_MATCH_M = 500


def _haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(p1) * \
        math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def fetch_direction_index():
    """(ROUTE_ID, co) -> [(bound, lat, lng)] first stop of each O/I direction."""
    index = defaultdict(list)
    try:
        rfl = requests.get(ROUTE_FARE_LIST_URL, timeout=120).json()
        stops = rfl["stopList"]
        for v in rfl["routeList"].values():
            for co in v.get("co", [])[:1]:
                bound = v.get("bound", {}).get(co)
                seq = v.get("stops", {}).get(co)
                loc = stops.get(seq[0], {}).get("location") if seq else None
                if bound in ("O", "I") and v.get("gtfsId") and loc:
                    index[(v["gtfsId"], co)].append(
                        (bound, loc["lat"], loc["lng"]))
    except Exception as e:
        logging.warning(f"routeFareList fetch failed; O/I via ROUTE_SEQ: {e}")
    return index


def _resolve(feature, direction_index):
    """O/I for one feature by the direction whose first stop is nearest its line
    start, or None if unavailable / farther than the guard distance."""
    # geopandas yields coords as (possibly nested) tuples, 2D or 3D; descend to
    # the first point [lon, lat, ...] of the first line
    coords = (feature.get("geometry") or {}).get("coordinates") or []
    while (coords and isinstance(coords[0], (list, tuple))
           and isinstance(coords[0][0], (list, tuple))):
        coords = coords[0]
    if not coords or not isinstance(coords[0], (list, tuple)):
        return None
    lon, lat = coords[0][0], coords[0][1]
    p = feature["properties"]
    cos = [_CO_ALIAS.get(c, c.lower())
           for c in str(p.get("COMPANY_CODE", "")).split("+")]
    best = None
    for co in cos:
        for bound, blat, blng in direction_index.get(
                (str(p["ROUTE_ID"]), co), []):
            d = _haversine(lat, lon, blat, blng)
            if best is None or d < best[1]:
                best = (bound, d)
    return best[0] if best and best[1] <= _MAX_MATCH_M else None


def assign_directions(features, direction_index):
    """Label a route's features jointly so it always emits one O and one I;
    fall back to ROUTE_SEQ when a direction can't be resolved."""
    opp = {"O": "I", "I": "O"}
    def seq(f): return "O" if f["properties"].get("ROUTE_SEQ", 1) == 1 else "I"
    r = [_resolve(f, direction_index) for f in features]
    if len(features) == 2:
        a, b = r
        if a and b and a != b:
            return [a, b]
        if a and not b:
            return [a, opp[a]]
        if b and not a:
            return [opp[b], b]
        return [seq(features[0]), opp[seq(features[0])]]
    return [(x or seq(f)) for x, f in zip(r, features)]


os.makedirs("waypoints", exist_ok=True)
direction_index = fetch_direction_index()

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
    by_route = defaultdict(list)
    for feature in data["features"]:
        by_route[feature["properties"]["ROUTE_ID"]].append(feature)

    for route_id, feats in by_route.items():
        for feature, direction in zip(
            feats, assign_directions(
                feats, direction_index)):
            with open("waypoints/" + str(route_id) + "-" + direction + ".json", "w", encoding='utf-8') as f:
                f.write(
                    # collapse repeated vertices (CSDI ships sub-mm geometry)
                    re.sub(r"(\[[-\d.,]+\])(?:,\1)+", r"\1",
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
                )


logging.info("Copying static data")
for file in glob.glob(r'./mtr/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./lrt/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./ferry/*.json'):
    shutil.copy(file, "waypoints")
