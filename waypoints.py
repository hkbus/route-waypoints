import requests
import json
import re
import os
import zipfile
import io
import glob
import shutil
from pyproj import Transformer

r = requests.get("https://portal.csdi.gov.hk/geoportal/rest/metadata/item/td_rcd_1638844988873_41214")
src_id = json.loads(r.content)['_source']['fileid'].replace('-', '')

epsgTransformer = Transformer.from_crs('epsg:2326', 'epsg:4326')

r = requests.get("https://static.csdi.gov.hk/csdi-webpage/download/"+src_id+"/geojson")
z = zipfile.ZipFile(io.BytesIO(r.content))
with z.open("FB_ROUTE_LINE.json") as f:
  data = json.loads(f.read().decode("utf-8"))

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
  with open("waypoints/"+str(properties["ROUTE_ID"])+"-"+("O" if properties["ROUTE_SEQ"] == 1 else "I")+".json", "w") as f:
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

