from math import radians, sin, cos, sqrt, atan2
from typing import Any, Sequence

EARTH_RADIUS_METERS = 6_371_000


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance between two points on Earth in meters."""
    lat1_r, lon1_r, lat2_r, lon2_r = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return EARTH_RADIUS_METERS * c


def validate_coordinates(lat: float, lon: float) -> bool:
    """Checks if coordinates are valid WGS84 latitude and longitude."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
        return -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0
    except (TypeError, ValueError):
        return False


def find_nearest_campus(lat: float, lon: float, campuses: Sequence[Any]) -> tuple[Any | None, float]:
    """
    Finds the nearest campus from a list of campus records and returns (nearest_campus, distance_in_meters).
    Returns (None, infinity) if campuses list is empty.
    """
    if not campuses:
        return None, float("inf")

    best_campus = None
    min_distance = float("inf")

    for campus in campuses:
        dist = haversine_distance(lat, lon, campus["latitude"], campus["longitude"])
        if dist < min_distance:
            min_distance = dist
            best_campus = campus

    return best_campus, min_distance
