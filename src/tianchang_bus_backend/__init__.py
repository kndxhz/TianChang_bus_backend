"""Read-only Tianchang bus query API."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import requests
from flask import Flask, jsonify, request
from dotenv import load_dotenv


load_dotenv()


UPSTREAM_URL = os.getenv(
    "TIANCHANG_UPSTREAM_URL", "http://www.tianchangapp.top:2001/BusService"
).rstrip("/")
APP_KEY = os.environ["TIANCHANG_APP_KEY"]
REQUEST_TIMEOUT = float(os.getenv("TIANCHANG_REQUEST_TIMEOUT", "10"))

# The legacy app calls this URL encoding helper EncryptCodeString. These are
# the fields that need the same treatment before they reach the source API.
URL_ENCODED_FIELDS: dict[str, set[str]] = {
    "QueryDetail_ByRouteID": {"RouteID", "Segmentid"},
    "Query_LinemomentNP": {"RouteID"},
    "Query_BusStationState": {"Busid"},
    "Query_ByStationID": {"RouteID", "StationID"},
    "Query_RouteLine": {"RouteID"},
    "Query_LinemomentDisplan": {"RouteID"},
    "Query_ByStationIDReturnAll": {"RouteID", "StationID", "IsmainsubCombine"},
    "Query_RouteStatData": {"RouteID"},
    "Query_ByNewInfoByID": {"ID"},
    "Query_GetPic": {"Busid"},
}


class UpstreamError(Exception):
    """An upstream request failed in a way that can be shown to API clients."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def signing_params() -> dict[str, str]:
    """Build fresh signing values for every upstream request.

    The legacy service expects a local-time timestamp and a random three-digit
    value. Neither value is cached so concurrent requests also get fresh data.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_value = str(secrets.randbelow(900) + 100)
    sign_key = hmac.new(
        APP_KEY.encode("utf-8"),
        (timestamp + random_value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"timeStamp": timestamp, "Random": random_value, "SignKey": sign_key}


def encrypt_code_string(value: Any) -> str:
    """Reproduce the legacy app's EncryptCodeString implementation.

    The HAR shows that each source character is shifted by 11 before normal
    URL encoding: ``14060`` becomes ``<?;A;`` and ``-1`` becomes ``8<``.
    """
    return "".join(chr(ord(char) + 11) for char in str(value))


def upstream_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = {key: value for key, value in (params or {}).items() if value is not None}
    query.update(signing_params())
    encoded_fields = URL_ENCODED_FIELDS.get(path.strip("/"), set())
    query = {
        key: encrypt_code_string(value) if key in encoded_fields else value
        for key, value in query.items()
    }
    # urlencode performs the legacy app's EncryptCodeString behaviour for the
    # fields above. It also correctly escapes ordinary query parameters.
    # Building the query string here avoids requests encoding an already
    # encoded value a second time.
    encoded_query = urlencode(query)
    try:
        response = requests.get(
            f"{UPSTREAM_URL}/{path.strip('/')}/?{encoded_query}",
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise UpstreamError("公交数据源暂时不可用") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError("公交数据源返回了无效数据") from exc


def required_arg(name: str) -> str:
    value = request.args.get(name, type=str)
    if not value or not value.strip():
        raise ValueError(f"缺少查询参数: {name}")
    return value.strip()


def route_name_matches(requested: str, actual: str) -> bool:
    requested = requested.strip().casefold()
    actual = actual.strip().casefold()
    if requested == actual:
        return True
    return requested.isdigit() and actual == f"{requested}路"


def direction_matches(requested: str, direction: dict[str, Any]) -> bool:
    """Match conversational destination text against a route direction."""
    target = requested.strip().casefold()
    for prefix in ("开往", "前往", "到", "往", "去"):
        if target.startswith(prefix):
            target = target[len(prefix) :].strip()
            break
    for suffix in ("方向", "方向的", "的方向"):
        if target.endswith(suffix):
            target = target[: -len(suffix)].strip()
            break
    if not target:
        return True

    destination = str(direction["to"]).strip().casefold()
    full_direction = str(direction["direction"]).strip().casefold()
    if "->" in target:
        return target in full_direction
    return target in destination


def route_directions(route_id: str) -> list[dict[str, Any]]:
    data = upstream_get("Query_RouteStatData", {"RouteID": route_id})
    if not isinstance(data, list):
        return []

    directions: list[dict[str, Any]] = []
    for route in data:
        for segment in route.get("SegmentList", []):
            stations = segment.get("StationList", [])
            if not stations:
                continue
            directions.append(
                {
                    "route_id": route.get("RouteID", route_id),
                    "route_name": route.get("RouteName"),
                    "direction": f"{stations[0].get('StationName')}->{segment.get('SegmentName')}",
                    "from": stations[0].get("StationName"),
                    "to": segment.get("SegmentName"),
                    "stations": [
                        {
                            "station_id": station.get("StationID"),
                            "station_name": station.get("StationName"),
                        }
                        for station in stations
                    ],
                }
            )
    return directions


def create_app() -> Flask:
    app = Flask(__name__)

    @app.errorhandler(ValueError)
    def handle_bad_request(error: ValueError):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(UpstreamError)
    def handle_upstream_error(error: UpstreamError):
        return jsonify({"error": str(error)}), error.status_code

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.get("/api/routes")
    def routes():
        # The legacy app uses sub-route IDs for station and realtime queries.
        return jsonify(upstream_get("Query_AllSubRouteData"))

    @app.get("/api/routes/search")
    def search_routes():
        name = required_arg("name")
        result = upstream_get("Query_AllSubRouteData")
        route_list = result.get("RouteList", []) if isinstance(result, dict) else []
        matches = [
            route
            for route in route_list
            if name.casefold() in str(route.get("RouteName", "")).casefold()
        ]
        return jsonify(matches)

    @app.get("/api/routes/<route_id>/directions")
    def directions(route_id: str):
        return jsonify(route_directions(route_id))

    @app.get("/api/realtime")
    def realtime_by_name():
        route_name = required_arg("route")
        station_name = required_arg("station")
        direction_name = request.args.get("direction", "").strip().casefold()
        route_data = upstream_get("Query_AllSubRouteData")
        route_list = route_data.get("RouteList", []) if isinstance(route_data, dict) else []
        route_matches = [
            route
            for route in route_list
            if route_name_matches(route_name, str(route.get("RouteName", "")))
        ]

        options: list[dict[str, Any]] = []
        for route in route_matches:
            for direction in route_directions(str(route["RouteID"])):
                station = next(
                    (
                        item
                        for item in direction["stations"]
                        if str(item["station_name"]).strip() == station_name
                    ),
                    None,
                )
                if station is None:
                    continue
                if direction_name and not direction_matches(direction_name, direction):
                    continue
                options.append({**direction, "station": station})

        if not options:
            raise ValueError("未找到匹配的线路、站点或方向")
        if len(options) > 1:
            return jsonify(
                {
                    "error": "匹配到多个方向，请提供 direction",
                    "options": [
                        {
                            "route_id": option["route_id"],
                            "route_name": option["route_name"],
                            "direction": option["direction"],
                            "station": option["station"],
                        }
                        for option in options
                    ],
                }
            ), 409

        selected = options[0]
        data = upstream_get(
            "Query_ByStationID",
            {
                "RouteID": selected["route_id"],
                "StationID": selected["station"]["station_id"],
            },
        )
        return jsonify({"query": selected, "data": data})

    @app.get("/api/routes/<route_id>/buses")
    def running_buses(route_id: str):
        segment_id = required_arg("segment_id")
        return jsonify(
            upstream_get(
                "QueryDetail_ByRouteID",
                {"RouteID": route_id, "Segmentid": segment_id},
            )
        )

    @app.get("/api/routes/<route_id>/schedule")
    def schedule(route_id: str):
        return jsonify(upstream_get("Query_LinemomentDisplan", {"RouteID": route_id}))

    @app.get("/api/routes/<route_id>/moments")
    def moments(route_id: str):
        return jsonify(upstream_get("Query_LinemomentNP", {"RouteID": route_id}))

    @app.get("/api/routes/<route_id>/stations")
    def route_stations(route_id: str):
        return jsonify(upstream_get("Query_RouteStatData", {"RouteID": route_id}))

    @app.get("/api/routes/<route_id>/line")
    def route_line(route_id: str):
        return jsonify(upstream_get("Query_RouteLine", {"RouteID": route_id}))

    @app.get("/api/stations/<station_id>/realtime")
    def station_realtime(station_id: str):
        route_id = required_arg("route_id")
        params: dict[str, Any] = {"RouteID": route_id, "StationID": station_id}
        if request.args.get("combine") is not None:
            params["IsmainsubCombine"] = request.args["combine"]
        return jsonify(upstream_get("Query_ByStationID", params))

    @app.get("/api/stations/nearby")
    def nearby_stations():
        return jsonify(
            upstream_get(
                "Query_NearbyStatInfo",
                {
                    "Latitude": required_arg("latitude"),
                    "Longitude": required_arg("longitude"),
                    "Range": request.args.get("range", "2000"),
                },
            )
        )

    @app.get("/api/stations/search")
    def station_search():
        return jsonify(
            upstream_get("Query_ByStaName", {"StationName": required_arg("name")})
        )

    @app.get("/api/buses/search")
    def bus_search():
        return jsonify(upstream_get("Query_BusStateVague", {"BusName": required_arg("name")}))

    return app


app = create_app()
app.json.ensure_ascii = False #关闭 jsonify 的 ASCII 编码，支持中文输出

def main() -> None:
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")),debug=True)

if __name__ == "__main__":
    main()
