import csv
import io
import json
import math
from pyodide.ffi import create_proxy
from pyodide.http import pyfetch
from pyscript import document
from js import h3 as h3js


def el(id_):
    return document.getElementById(id_)


def set_status(message: str) -> None:
    el("status").textContent = message


def _val(id_: str) -> str:
    node = el(id_)
    return (node.value if node else "").strip()


def _fval(id_: str, default: float = 0.0) -> float:
    try:
        return float(_val(id_))
    except Exception:
        return float(default)


async def fetch_text(path_or_url: str) -> str:
    response = await pyfetch(path_or_url)
    if response.status != 200:
        raise RuntimeError(f"Fetch failed: {path_or_url} (HTTP {response.status})")
    return await response.string()


async def fetch_json(path_or_url: str) -> dict:
    return json.loads(await fetch_text(path_or_url))


async def fetch_csv(path_or_url: str) -> list[dict]:
    text = await fetch_text(path_or_url)
    return list(csv.DictReader(io.StringIO(text)))


def to_float(x, default=0.0) -> float:
    try:
        value = float(x)
        return default if math.isnan(value) else value
    except Exception:
        return default


def to_int(x, default=0) -> int:
    try:
        return int(float(x))
    except Exception:
        return default


async def load_h3_geojson(res: str) -> dict:
    filename = "SouthSudanH3res6.geojson" if res == "6" else "SouthSudanH3res5.geojson"
    return await fetch_json(filename)


async def load_health_facilities() -> list[dict]:
    rows = await fetch_csv("SouthSudanHealthFacilities1282026.csv")
    facilities = []
    for row in rows:
        lat = to_float(row.get("Latitude"), math.nan)
        lon = to_float(row.get("Longitude"), math.nan)
        if math.isnan(lat) or math.isnan(lon):
            continue
        facilities.append(
            {
                "lat": lat,
                "lon": lon,
                "name": row.get("Facility_N", ""),
                "type": row.get("Facility_T", ""),
                "state": row.get("State", ""),
                "county": row.get("County", ""),
                "functional": row.get("Functional", ""),
            }
        )
    return facilities


async def load_total_pop_csv(popcsv: str) -> dict:
    if not popcsv:
        return {}
    try:
        rows = await fetch_csv(popcsv)
    except Exception:
        return {}

    population = {}
    for row in rows:
        idx = row.get("index") or row.get("h3") or row.get("H3") or row.get("Index")
        if not idx or idx.strip() == "...":
            continue
        population[idx] = to_int(row.get("total_pop", 0), 0)
    return population


async def fetch_acled_points(acled_key: str) -> list[dict]:
    if not acled_key:
        return []

    url = (
        "https://api.acleddata.com/acled/read"
        f"?key={acled_key}"
        "&country=South%20Sudan"
        "&limit=500"
    )
    try:
        payload = await fetch_json(url)
    except Exception:
        return []

    points = []
    for event in payload.get("data", []) if isinstance(payload, dict) else []:
        lat = to_float(event.get("latitude"), math.nan)
        lon = to_float(event.get("longitude"), math.nan)
        if math.isnan(lat) or math.isnan(lon):
            continue
        points.append({"lat": lat, "lon": lon, "metric": to_int(event.get("fatalities", 0), 0)})
    return points


async def fetch_hdx_idp_points(hdx_token: str) -> list[dict]:
    # Placeholder for a specific HDX IDP dataset/API endpoint.
    # Return records shaped like: {"lat": 0.0, "lon": 0.0, "pop": 0}
    if not hdx_token:
        return []
    return []


def latlng_to_h3(lat: float, lon: float, res: int) -> str | None:
    try:
        if hasattr(h3js, "latLngToCell"):
            return h3js.latLngToCell(lat, lon, res)
        return h3js.geoToH3(lat, lon, res)
    except Exception:
        return None


def aggregate_points_to_h3(points: list[dict], res: int, value_key: str) -> dict:
    totals = {}
    for point in points:
        lat = to_float(point.get("lat"), math.nan)
        lon = to_float(point.get("lon"), math.nan)
        if math.isnan(lat) or math.isnan(lon):
            continue
        idx = latlng_to_h3(lat, lon, res)
        if not idx:
            continue
        totals[idx] = totals.get(idx, 0.0) + to_float(point.get(value_key, 0.0), 0.0)
    return totals


def iter_positions(coords):
    if isinstance(coords, (list, tuple)):
        if len(coords) >= 2 and isinstance(coords[0], (int, float)) and isinstance(coords[1], (int, float)):
            yield coords
        else:
            for item in coords:
                yield from iter_positions(item)


def compute_viewstate_from_geojson(geojson: dict) -> dict:
    positions = []
    for feature in geojson.get("features", [])[:600]:
        positions.extend(iter_positions(feature.get("geometry", {}).get("coordinates", [])))

    if not positions:
        return {"longitude": 31.0, "latitude": 7.0, "zoom": 5, "pitch": 45, "bearing": 0}

    lons = [p[0] for p in positions]
    lats = [p[1] for p in positions]
    return {
        "longitude": (min(lons) + max(lons)) / 2,
        "latitude": (min(lats) + max(lats)) / 2,
        "zoom": 5.2,
        "pitch": 50,
        "bearing": 0,
    }


async def run_app():
    set_status("Loading GeoJSON and CSV files...")

    res = _val("res")
    popcsv = _val("popcsv")
    acled_key = _val("acledKey")
    hdx_token = _val("hdxToken")

    scale_total = _fval("scaleTotal", 0.002)
    scale_idp = _fval("scaleIdp", 0.006)
    scale_conflict = _fval("scaleConflict", 2.0)

    h3_geojson = await load_h3_geojson(res)
    facilities = await load_health_facilities()
    total_pop_map = await load_total_pop_csv(popcsv)

    set_status("Aggregating optional ACLED/HDX layers...")
    h3_res_int = 6 if res == "6" else 5
    conflict_map = aggregate_points_to_h3(await fetch_acled_points(acled_key), h3_res_int, "metric")
    idp_map = aggregate_points_to_h3(await fetch_hdx_idp_points(hdx_token), h3_res_int, "pop")

    for feature in h3_geojson.get("features", []):
        props = feature.setdefault("properties", {})
        idx = props.get("index")
        total_pop = int(total_pop_map.get(idx, 0))
        idp_pop = float(idp_map.get(idx, 0.0))
        conflict_metric = float(conflict_map.get(idx, 0.0))

        props.update(
            {
                "total_pop": total_pop,
                "idp_pop": idp_pop,
                "conflict_metric": conflict_metric,
                "total_elev": total_pop * scale_total,
                "idp_elev": idp_pop * scale_idp,
                "conflict_elev": conflict_metric * scale_conflict,
            }
        )

    from js import renderDeck

    renderDeck(h3_geojson, facilities, compute_viewstate_from_geojson(h3_geojson))
    set_status(f"Rendered {len(h3_geojson.get('features', [])):,} H3 cells and {len(facilities):,} facilities.")


async def _on_click(event):
    try:
        await run_app()
    except Exception as exc:
        set_status(f"Error: {exc}")
        print(exc)


el("run").addEventListener("click", create_proxy(_on_click))
set_status("Ready. Click Load + Render.")
