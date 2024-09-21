import requests
import json
import re
import os
import zipfile
import io
import glob
import shutil
import geopandas
# noinspection PyUnresolvedReferences
import pyogrio
from pyproj import Transformer
from tempfile import TemporaryDirectory

r = requests.get("https://portal.csdi.gov.hk/geoportal/rest/metadata/item/td_rcd_1638844988873_41214")
src_id = json.loads(r.content)['_source']['fileid'].replace('-', '')

epsgTransformer = Transformer.from_crs('epsg:2326', 'epsg:4326')

r = requests.get("https://static.csdi.gov.hk/csdi-webpage/download/"+src_id+"/fgdb")
z = zipfile.ZipFile(io.BytesIO(r.content))
gdb_name = next(s[0:s.index('/')] for s in z.namelist() if s.startswith("FB_ROUTE"))

with TemporaryDirectory() as tmpdir:
  z.extractall(tmpdir)
  gdb_path = os.path.join(tmpdir, gdb_name)
  gdf = geopandas.read_file(gdb_path, encoding='utf-8', engine="pyogrio")
  geojson_path = os.path.join(tmpdir, "data.geojson")
  gdf.to_file(geojson_path, driver='GeoJSON', encoding='utf-8')
  with open(geojson_path, encoding='utf-8') as f:
    data = json.load(f)

for i, feature in enumerate(data["features"]):
  if feature["geometry"]["type"] == "MultiLineString":
    for j, coordinates in enumerate(feature["geometry"]["coordinates"]):
      for k, coordinate in enumerate(coordinates):
        lat, lng = epsgTransformer.transform(coordinate[1], coordinate[0])
        data["features"][i]["geometry"]["coordinates"][j][k] = [lng, lat]
  else:
    for j, coordinate in enumerate(feature["geometry"]["coordinates"]):
      lat, lng = epsgTransformer.transform(coordinate[1], coordinate[0])
      data["features"][i]["geometry"]["coordinates"][j] = [lng, lat]

os.makedirs("waypoints", exist_ok=True)
for file in glob.glob(r'./mtr/*.json'):
  shutil.copy(file, "waypoints")
for file in glob.glob(r'./lrt/*.json'):
  shutil.copy(file, "waypoints")
for file in glob.glob(r'./ferry/*.json'):
  shutil.copy(file, "waypoints")

for feature in data["features"]:
  properties = feature["properties"]
  with open("waypoints/"+str(properties["ROUTE_ID"])+"-"+("O" if properties["ROUTE_SEQ"] == 1 else "I")+".json", "w", encoding='utf-8') as f:
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

