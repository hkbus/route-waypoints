# HK Bus WayPoints Crawling

![Data fetching status](https://github.com/hkbus/route-waypoints/actions/workflows/crawl.yml/badge.svg) 

This project is to fetch the waypoints from [CSDI](https://portal.csdi.gov.hk/geoportal/#metadataInfoPanel). It is daily synced to the sources and launched in gh-pages.

During the crawling, it will minified the result by truncating to 5 decimal places, (i.e., ±1m), and minified the json by cleaning useless space characters. Also, as the data is provided statically by `gh-pages`, the data transfer supports `Content-Encoding: gzip` for largely preserving your bandwidth.

Links are in format:
- MTR: https://hkbus.github.io/route-waypoints/{LINE_CODE}.json
- LRT: https://hkbus.github.io/route-waypoints/{LINE_NUMBER}-{[O|I]}.json
- Ferry: https://hkbus.github.io/route-waypoints/{ROUTE_ID}.json
- Everything else: https://hkbus.github.io/route-waypoints/{GTFS_ID}-{[O|I]}.json

Example link: (https://hkbus.github.io/route-waypoints/1001-O.json)

## Crawling by yourself

### Usage
Daily fetched GeoJSONs are in [gh-pages](https://github.com/hkbus/route-waypoints/tree/gh-pages).

### Installation

To install the dependencies,
```
pip install -r ./crawling/requirements.txt
```

### Data Fetching

To fetch data, run the followings,
```
python ./waypoints.py
```

## Citing 

Please kindly state you are using this app as
`
HK Bus Crawling@2021, https://github.com/hkbus/route-waypoints
`

## Contributors
[ChunLaw](https://github.com/chunlaw/)
[chakflying](https://github.com/chakflying) 
