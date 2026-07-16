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


os.makedirs("waypoints", exist_ok=True)

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
        with open("waypoints/" + str(properties["ROUTE_ID"]) + "-" + ("O" if properties["ROUTE_SEQ"] == 1 else "I") + ".json", "w", encoding='utf-8') as f:
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


logging.info(
    "Removing accidental Central Kowloon Route detours (see ckr_patch.json)")
with open('ckr_patch.json', encoding='utf-8') as f:
    ckr_patches = json.load(f)


def ckr_find(coords, seq, from_end):
    indices = range(len(coords) - len(seq), -1, -
                    1) if from_end else range(len(coords) - len(seq) + 1)
    for i in indices:
        if all(abs(coords[i + j][0] - x) < 3e-5 and abs(coords[i + j]
               [1] - y) < 3e-5 for j, (x, y) in enumerate(seq)):
            return i
    return None


def ckr_length(coords):
    return sum(math.hypot((coords[i + 1][0] - coords[i][0]) * 102730,
                          (coords[i + 1][1] - coords[i][1]) * 110852)
               for i in range(len(coords) - 1))


for name, patch in ckr_patches.items():
    path = "waypoints/" + name + ".json"
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    properties = data["features"][0]["properties"]
    geometry = data["features"][0]["geometry"]
    parts = [geometry["coordinates"]
             ] if geometry["type"] == "LineString" else geometry["coordinates"]
    coords = [c for part in parts for c in part]
    safe = (str(properties.get("ROUTE_ID")) == name.split("-")[0]
            and properties.get("ROUTE_NAMEE") == patch["route"]
            and all(math.hypot((b[0][0] - a[-1][0]) * 102730, (b[0][1] - a[-1][1]) * 110852) < 100
                    for a, b in zip(parts, parts[1:])))
    i0 = ckr_find(coords, patch["start_seq"], False) if safe else None
    i1 = ckr_find(coords, patch["end_seq"], True) if safe else None
    # witness: the matched run must still pass through the CKR bypass bore,
    # which these route-gated records are not documented to serve
    imid = ckr_find(coords, patch["mid_seq"], False) if safe else None
    end = None if i1 is None else i1 + len(patch["end_seq"]) - 1
    run_m = 0 if i0 is None or end is None else ckr_length(coords[i0:end + 1])
    if (i0 is None or end is None or imid is None or not i0 < imid < end
            or run_m < patch["min_m"]):
        logging.info(
            f"{name} ({patch['route']}): no accidental CKR detour present, patch skipped")
        continue
    len_before = ckr_length(coords)
    del coords[i0 + 1:end]
    if isinstance(properties.get("Shape_Length"),
                  (int, float)) and len_before > 0:
        properties["Shape_Length"] = round(
            properties["Shape_Length"] * ckr_length(coords) / len_before, 4)
    geometry["type"] = "MultiLineString"
    geometry["coordinates"] = [coords]
    logging.info(
        f"{name} ({patch['route']}): removed {run_m / 1000:.1f} km accidental CKR detour")
    with open(path, "w", encoding='utf-8') as f:
        f.write(
            re.sub(
                r"([0-9]+\.[0-9]{5})[0-9]+",
                r"\1",
                json.dumps(
                    data,
                    ensure_ascii=False,
                    separators=(
                        ",",
                        ":"))))

logging.info("Copying static data")
for file in glob.glob(r'./mtr/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./lrt/*.json'):
    shutil.copy(file, "waypoints")
for file in glob.glob(r'./ferry/*.json'):
    shutil.copy(file, "waypoints")
