import asyncio
import logging
from datetime import datetime, timedelta, timezone
import numpy as np
from geopy.distance import geodesic
from bounciepy import AsyncRESTAPIClient
from bounciepy.exceptions import BouncieException
from geopy.geocoders import Nominatim
import os
import aiohttp
from date_utils import parse_date, format_date, get_start_of_day, get_end_of_day

# Use os.getenv directly for environment variables
CLIENT_ID = "python-test"
CLIENT_SECRET = "v023rK8ZLVSh7pp0dhkrRu9rqYonaCbRDLSQ1Hh9JG5VR6REVr"
REDIRECT_URI = "http://localhost:8080/callback"
AUTH_CODE = "UfHLWwJJqrJkLyA2uy2a7fJvAsTUOOmkAq2H5Tfkuwc1ZMxsO2"
DEVICE_IMEI = "352602113969379"
VEHICLE_ID = "5f31babdad03810038e10c32"

ENABLE_GEOCODING = os.getenv("ENABLE_GEOCODING", "False").lower() == "true"

logger = logging.getLogger(__name__)

class BouncieAPI:
    def __init__(self):
        if not all([CLIENT_ID, CLIENT_SECRET, REDIRECT_URI, AUTH_CODE, VEHICLE_ID, DEVICE_IMEI]):
            raise ValueError("Missing required environment variables for BouncieAPI")

        self.client = AsyncRESTAPIClient(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_url=REDIRECT_URI,
            auth_code=AUTH_CODE,
        )
        self.geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)
        self.live_trip_data = {"last_updated": datetime.now(timezone.utc), "data": []}

    async def get_access_token(self):
        try:
            success = await self.client.get_access_token()
            if not success:
                logger.error("Failed to obtain access token.")
                return False
            return True
        except Exception as e:
            logger.error(f"Error getting access token: {e}")
            return False

    async def fetch_summary_data(self, session, date):
        start_time = f"{date}T00:00:00-05:00"
        end_time = f"{date}T23:59:59-05:00"
        summary_url = f"https://www.bouncie.app/api/vehicles/{VEHICLE_ID}/triplegs/details/summary?bands=true&defaultColor=%2355AEE9&overspeedColor=%23CC0000&startDate={start_time}&endDate={end_time}"

        headers = {
            "Accept": "application/json",
            "Authorization": self.client.access_token,
            "Content-Type": "application/json"
        }

        async with session.get(summary_url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            else:
                logger.error(f"Error: Failed to fetch data for {date}. HTTP Status code: {response.status}")
                return None

    async def fetch_trip_data(self, start_date, end_date):
        if not await self.get_access_token():
            return None

        all_trips = []
        current_date = start_date
        async with aiohttp.ClientSession() as session:
            while current_date <= end_date:
                date_str = current_date.strftime("%Y-%m-%d")
                logger.info(f"Fetching trips for: {date_str}")
                trips_data = await self.fetch_summary_data(session, date_str)

                if trips_data:
                    all_trips.extend(trips_data)

                current_date += timedelta(days=1)
                await asyncio.sleep(0.1)  # Small delay to prevent overwhelming the API

        return all_trips

    async def get_latest_bouncie_data(self):
        try:
            await self.get_access_token()
            vehicle_data = await self.client.get_vehicle_by_imei(imei=DEVICE_IMEI)
            if not vehicle_data or "stats" not in vehicle_data:
                logger.error("No vehicle data or stats found in Bouncie response")
                return None

            stats = vehicle_data["stats"]
            location = stats.get("location", {})

            if not location:
                logger.error("No location data found in Bouncie stats")
                return None

            location_address = (
                await self.reverse_geocode(location.get("lat"), location.get("lon"))
                if ENABLE_GEOCODING and location.get("lat") is not None and location.get("lon") is not None
                else "N/A"
            )

            try:
                timestamp_iso = stats["lastUpdated"]
                timestamp_dt = parse_date(timestamp_iso)
                timestamp_unix = int(timestamp_dt.timestamp())
            except Exception as e:
                logger.error(f"Error converting timestamp: {e}")
                return None

            bouncie_status = stats.get("battery", {}).get("status", "unknown")
            battery_state = (
                "full" if bouncie_status == "normal"
                else "unplugged" if bouncie_status == "low"
                else "unknown"
            )

            if self.live_trip_data["data"] and self.live_trip_data["data"][-1]["timestamp"] == timestamp_unix:
                logger.info("Duplicate timestamp found, not adding new data point.")
                return None

            new_data_point = {
                "latitude": location.get("lat"),
                "longitude": location.get("lon"),
                "timestamp": timestamp_unix,
                "battery_state": battery_state,
                "speed": stats.get("speed", 0),
                "device_id": DEVICE_IMEI,
                "address": location_address,
            }
            self.live_trip_data["data"].append(new_data_point)
            self.live_trip_data["last_updated"] = datetime.now(timezone.utc)

            logger.info(f"Latest Bouncie data retrieved: {location.get('lat')}, {location.get('lon')} at {timestamp_unix}")
            return new_data_point

        except Exception as e:
            logger.error(f"An error occurred while fetching live data: {e}")
            return None

    async def reverse_geocode(self, lat, lon, retries=3):
        for attempt in range(retries):
            try:
                location = await asyncio.to_thread(self.geolocator.reverse, (lat, lon), addressdetails=True)
                if location:
                    address = location.raw["address"]
                    formatted_address = f"{address.get('place', '')}<br>"
                    formatted_address += f"{address.get('building', '')}<br>"
                    formatted_address += f"{address.get('house_number', '')} {address.get('road', '')}<br>"
                    formatted_address += f"{address.get('city', '')}, {address.get('state', '')} {address.get('postcode', '')}"
                    return formatted_address.strip("<br>")
                else:
                    return "N/A"
            except Exception as e:
                logger.error(f"Reverse geocoding attempt {attempt + 1} failed with error: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(1)
        return "N/A"

    async def get_trip_metrics(self):
        time_since_update = datetime.now(timezone.utc) - self.live_trip_data["last_updated"]
        if time_since_update.total_seconds() > 45:
            self.live_trip_data["data"] = []

        total_distance = 0
        total_time = 0
        max_speed = 0
        start_time = None
        end_time = None

        for i in range(1, len(self.live_trip_data["data"])):
            prev_point = self.live_trip_data["data"][i - 1]
            curr_point = self.live_trip_data["data"][i]

            distance = geodesic(
                (prev_point["latitude"], prev_point["longitude"]),
                (curr_point["latitude"], curr_point["longitude"]),
            ).miles
            total_distance += distance

            time_diff = curr_point["timestamp"] - prev_point["timestamp"]
            total_time += time_diff

            max_speed = max(max_speed, curr_point["speed"])

            if start_time is None:
                start_time = prev_point["timestamp"]
            end_time = curr_point["timestamp"]

        formatted_metrics = {
            "total_distance": round(total_distance, 2),
            "total_time": self._format_time(total_time),
            "max_speed": max_speed,
            "start_time": format_date(datetime.fromtimestamp(start_time, timezone.utc)) if start_time else "N/A",
            "end_time": format_date(datetime.fromtimestamp(end_time, timezone.utc)) if end_time else "N/A",
        }

        logger.info(f"Returning trip metrics: {formatted_metrics}")
        return formatted_metrics

    def _format_time(self, seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def create_geojson_features_from_trips(data):
        features = []
        logger.info(f"Processing {len(data)} trips")

        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
            data = data[0].get('bands', [])

        for trip in data:
            if not isinstance(trip, dict):
                logger.warning(f"Skipping non-dict trip data: {trip}")
                continue

            coordinates = []
            timestamp = None
            for band in trip.get("bands", []):
                for path in band.get("paths", []):
                    path_array = np.array(path)
                    if path_array.shape[1] >= 5:  # Check for lat, lon, timestamp at least
                        coordinates.extend(path_array[:, [1, 0]])  # lon, lat
                        timestamp = path_array[-1, 4]  # last timestamp
                    else:
                        logger.warning(f"Skipping invalid path: {path}")

            if len(coordinates) > 1 and timestamp is not None:
                feature = {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates},  # Removed .tolist()
                    "properties": {"timestamp": int(timestamp)},
                }
                features.append(feature)
            else:
                logger.warning(f"Skipping trip with insufficient data: coordinates={len(coordinates)}, timestamp={timestamp}")

        logger.info(f"Created {len(features)} GeoJSON features from trip data")
        return features 