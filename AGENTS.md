# AGENTS.md — route-waypoints

Guidance for AI coding agents working in this repository. Humans: see `README.md`.

## What this repo is

The map geometry for [hkbus.app](https://hkbus.app). A single script,
`waypoints.py`, downloads the Transport Department's route-alignment datasets
from [CSDI](https://portal.csdi.gov.hk/) as File Geodatabases, reprojects them to
WGS 84, and splits them into **one minified GeoJSON file per route direction**.
Static route geometry that CSDI does not cover (MTR, Light Rail, ferries) is
committed by hand under `mtr/`, `lrt/` and `ferry/` and copied through unchanged.

GitHub Actions runs it daily and publishes the `waypoints/` directory to
`gh-pages`, served at `https://hkbus.github.io/route-waypoints/`.

**This repo only ever draws lines.** It does not know about ETAs, stops, fares or
timetables. If a route's polyline is missing, truncated, or traces the wrong
roads, that is here. Anything else is a different repo.

## Repository family

| Repo | Role |
| --- | --- |
| `hkbus/route-waypoints` | **this repo** — route polylines (GeoJSON) |
| `hkbus/hk-bus-crawling` | route/stop/fare database, and the source of the `gtfsId` values used in filenames |
| `hkbus/hk-independent-bus-eta` | the hkbus.app PWA — fetches these files in `src/hooks/useRoutePath.tsx` |
| `hkbus/hk-bus-eta` | npm ETA package |

## Output contract — do not break these filenames

The app builds URLs by string concatenation, so **the filename scheme is the
API**:

```text
bus / minibus : {GTFS_ID}-{O|I}.json      O = ROUTE_SEQ 1, I = ROUTE_SEQ 2
MTR           : {LINE_CODE}.json          e.g. tml.json, eal.json
Light Rail    : {LINE_NUMBER}_{O|I}.json  underscore, not hyphen — see below
                                          circular lines have no suffix: 705.json
Ferry         : {ROUTE_ID}.json           e.g. 7021.json, NPKC.json
```

> The Light Rail separator really is an **underscore** (`615_I.json`), matching
> what the app builds in `useRoutePath.tsx`. `README.md` documents a hyphen for
> LRT; the README is wrong. Trust the files on disk.

**The compaction applies only to the CSDI-generated bus/minibus files.** Each of
those is a GeoJSON `FeatureCollection` holding exactly one feature, with
coordinates **truncated to 5 decimal places** (≈1 m) by regex and the JSON
written with `separators=(",", ":")`. These files are large and served to mobile
clients over gzip, so the compaction is deliberate: do not pretty-print them, do
not raise the precision, and do not change the `O`/`I` convention without
coordinating with the app and `hk-bus-crawling`.

Two things in the same output directory are **not** subject to that:

- **Static geometry** (`mtr/`, `lrt/`, `ferry/`) is `shutil.copy`'d verbatim, so
  it keeps whatever formatting is committed — `ferry/7021.json`, for instance,
  carries full-precision coordinates. Match the file you are editing; do not
  "normalise" these to the generated style.
- **`waypoints/0versions.json`** is not GeoJSON at all — a plain map of CSDI
  dataset → source version, written with `indent=4`. It is prefixed with `0` so
  it sorts first in directory listings.

## Layout

```text
waypoints.py         the entire pipeline (~100 lines)
requirements.txt     pinned; geopandas/pyogrio/shapely/pyproj do the geo work
mtr/ lrt/ ferry/     hand-maintained static GeoJSON, copied verbatim to output
waypoints/           generated output (git-ignored) → deployed to gh-pages
```

## Commands

```sh
pip install -r ./requirements.txt
python ./waypoints.py        # writes ./waypoints/
```

Use an isolated environment (`uv venv`, `conda`, `python -m venv`); never install
into the system Python. `geopandas` and `pyogrio` pull in GDAL — expect the
install to be the slow part.

A full run downloads two File Geodatabase archives from CSDI and writes several
thousand JSON files. Be considerate: CSDI is a public government service, so do
not loop the script or parallelise the downloads.

## CI

- `.github/workflows/crawl.yml` — runs `waypoints.py` daily (and on every push)
  and deploys `waypoints/` to `gh-pages`.
- `.github/workflows/format.yml` — runs autopep8 and **auto-commits** the result.

There are no tests and no syntax gate. A crash shows up as a red Data Fetching
run, which means the map silently stops updating — treat a red run as urgent.

## House rules

### Formatting: four-space indent, autopep8-aggressive

The Format workflow runs:

```sh
autopep8 --exit-code --recursive --in-place --aggressive --aggressive .
```

Note there is **no `--indent-size`** here, so this repo is standard four-space
Python. (The sibling repo `hkbus/hk-bus-crawling` runs the same action *with*
`--indent-size=2` and is two-space indented — do not carry style between them.)

Check before pushing:

```sh
autopep8 --aggressive --aggressive --diff waypoints.py
```

An empty diff is the goal (`waypoints.py` is currently clean under autopep8
2.3.2). Aggressive autopep8 also enforces the 79-character line limit. Keep the
check scoped to files you changed rather than committing whole-repo
reformatting — your local autopep8 version may not match the action's.

The workflow pushes a "Formatted Code!" commit itself. On a **fork** PR that push
403s, so a red Format job on a fork PR often means "needed reformatting *and* the
bot could not do it for you" — format locally and push again.

### Diffs and comments

- Minimal, single-concern diffs. The script is ~100 lines; keep it that way.
- Comments are rare and terse — one short line where the *why* is not obvious.
  The existing comments (e.g. why `0versions.json` is named that way) are the
  right density. Reasoning belongs in the PR body.
- `requirements.txt` is exact-pinned. Do not bump it as a drive-by; a geopandas
  or GDAL bump can silently change geometry output.
- Static geometry under `mtr/`, `lrt/`, `ferry/` is hand-curated. If you edit it,
  say where the new geometry came from and keep the same minification.

## Verifying a change

CI will happily deploy geometrically wrong output, so verify locally:

1. Run `waypoints.py` before and after your change into separate directories.
2. Compare file counts and total size — a large swing means routes were dropped
   or duplicated.
3. Diff a handful of individual files, and plot at least one changed route
   (geojson.io, or any GeoJSON viewer) to confirm it still traces real roads.
4. Put those counts and a screenshot in the PR body. For geometry, a picture is
   the evidence; a description is not.

## Pull requests

- Branch from `main`; PRs target `main`.
- Filename or precision changes need a heads-up in `hk-independent-bus-eta` —
  the app hard-codes the URL shape.

## Licence

See `LICENSE`.
