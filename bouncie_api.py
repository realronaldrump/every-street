import asyncio
import logging
from datetime import datetime, timedelta, timezone

from geopy.distance import geodesic
from bounciepy import AsyncRESTAPIClient
from geopy.geocoders import Nominatim
from dotenv import load_dotenv
import os

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
AUTH_CODE = os.getenv("AUTH_CODE")
VEHICLE_ID = os.getenv("VEHICLE_ID")
DEVICE_IMEI = os.getenv("DEVICE_IMEI")

ENABLE_GEOCODING = True


class BouncieAPI:
    def __init__(self):
        self.client = AsyncRESTAPIClient(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_url=REDIRECT_URI,
            auth_code=AUTH_CODE,
        )
        self.geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)
        self.live_trip_data = {"last_updated": datetime.now(timezone.utc), "data": []}

    async def get_latest_bouncie_data(self):
        try:
            await self.client.get_access_token()
            vehicle_data = await self.client.get_vehicle_by_imei(imei=DEVICE_IMEI)
            if not vehicle_data or "stats" not in vehicle_data:
                logging.error("No vehicle data or stats found in Bouncie response")
                return None

            stats = vehicle_data["stats"]
            location = stats.get("location")

            if not location:
                logging.error("No location data found in Bouncie stats")
                return None

            location_address = (
                await self.reverse_geocode(location["lat"], location["lon"])
                if ENABLE_GEOCODING
                else "N/A"
            )

            try:
                timestamp_iso = stats["lastUpdated"]
                timestamp_dt = datetime.fromisoformat(
                    timestamp_iso.replace("Z", "+00:00")
                )
                timestamp_unix = int(timestamp_dt.timestamp())
            except Exception as e:
                logging.error(f"Error converting timestamp: {e}")
                return None

            bouncie_status = stats["battery"]["status"]
            battery_state = (
                "full"
                if bouncie_status == "normal"
                else "unplugged"
                if bouncie_status == "low"
                else "unknown"
            )

            logging.info(
                f"Latest Bouncie data retrieved: {location['lat']}, {location['lon']} at {timestamp_unix}"
            )
            return {
                "latitude": location["lat"],
                "longitude": location["lon"],
                "timestamp": timestamp_unix,
                "battery_state": battery_state,
                "speed": stats["speed"],
                "device_id": DEVICE_IMEI,
                "address": location_address,
            }
        except Exception as e:
            logging.error(f"An error occurred while fetching live data: {e}")
            return None

    async def reverse_geocode(self, lat, lon, retries=3):
        for attempt in range(retries):
            try:
                location = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.geolocator.reverse((lat, lon), addressdetails=True)
                )
                if location:
                    address = location.raw["address"]
                    place = address.get("place", "")
                    building = address.get("building", "")
                    house_number = address.get("house_number", "")
                    road = address.get("road", "")
                    city = address.get("city", "")
                    state = address.get("state", "")
                    postcode = address.get("postcode", "")

                    formatted_address = f"{place}<br>" if place else ""
                    formatted_address += f"{building}<br>" if building else ""
                    formatted_address += f"{house_number} {road}<br>{city}, {state} {postcode}"

                    return formatted_address
                else:
                    return "N/A"
            except Exception as e:
                logging.error(
                    f"Reverse geocoding attempt {attempt + 1} failed with error: {e}"
                )
                if attempt < retries - 1:
                    await asyncio.sleep(1)
        return "N/A"

    async def fetch_trip_data(self, session, vehicle_id, date, headers):
        start_time = f"{date}T00:00:00-05:00"
        end_time = f"{date}T23:59:59-05:00"
        summary_url = f"https://www.bouncie.app/api/vehicles/{vehicle_id}/triplegs/details/summary?bands=true&defaultColor=%2355AEE9&overspeedColor=%23CC0000&startDate={start_time}&endDate={end_time}"

        async with session.get(summary_url, headers=headers) as response:
            if response.status == 200:
                logging.info(f"Successfully fetched data for {date}")
                return await response.json()
            else:
                logging.error(f"Error fetching data for {date}. Status: {response.status}")
                return None

    def get_trip_metrics(self):
        time_since_update = datetime.now(timezone.utc) - self.live_trip_data[
            "last_updated"
        ]
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
            "start_time": datetime.fromtimestamp(start_time).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if start_time
            else "N/A",
            "end_time": datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S")
            if end_time
            else "N/A",
        }

        logging.info(f"Returning trip metrics: {formatted_metrics}")
        return formatted_metrics

    def _format_time(self, seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
