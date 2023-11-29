# HK Bus WayPoints Crawling (a.k.a. hk-bus-eta)

![Data fetching status](https://github.com/hkbus/route-waypoints/actions/workflows/crawl.yml/badge.svg) 

This project is to fetch the waypoints from [CSDI](https://portal.csdi.gov.hk/geoportal/#metadataInfoPanel). It is daily synced to the sources and launched in gh-pages.

During the crawling, it will minified the result by truncating to 5 decimal places, (i.e., ±1m), and minified the json by cleaning useless space characters. Also, as the data is provided statically by `gh-pages`, the data transfer supports `Content-Encoding: gzip` for largely preserving your bandwidth.


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
[ChunLaw](http://github.com/chunlaw/)
