import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from bounciepy import AsyncRESTAPIClient
from dotenv import load_dotenv
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

from date_utils import (format_date, get_end_of_day, get_start_of_day,
                        parse_date)

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
        self.live_trip_data = {
            "last_updated": datetime.now(timezone.utc), "data": []}

    async def get_latest_bouncie_data(self):
        try:
            await self.client.get_access_token()
            vehicle_data = await self.client.get_vehicle_by_imei(imei=DEVICE_IMEI)
            if not vehicle_data or "stats" not in vehicle_data:
                logging.error(
                    "No vehicle data or stats found in Bouncie response")
                return None

            stats = vehicle_data["stats"]
            location = stats.get("location", {})

            if not location:
                logging.error("No location data found in Bouncie stats")
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
                logging.error(f"Error converting timestamp: {e}")
                return None

            bouncie_status = stats.get("battery", {}).get("status", "unknown")
            battery_state = (
                "full"
                if bouncie_status == "normal"
                else "unplugged"
                if bouncie_status == "low"
                else "unknown"
            )

            # Check the last entry in live_trip_data before adding the new one
            if self.live_trip_data["data"]:
                last_point = self.live_trip_data["data"][-1]
                if last_point["timestamp"] == timestamp_unix:
                    logging.info(
                        "Duplicate timestamp found, not adding new data point.")
                    return None  # Skip adding the duplicate point

            # If the timestamp is different, add the new point
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

            logging.info(
                f"Latest Bouncie data retrieved: {location.get('lat')}, {location.get('lon')} at {timestamp_unix}"
            )
            return new_data_point

        except Exception as e:
            logging.error(f"An error occurred while fetching live data: {e}")
            return None

    async def reverse_geocode(self, lat, lon, retries=3):
        for attempt in range(retries):
            try:
                location = await asyncio.to_thread(self.geolocator.reverse, (lat, lon), addressdetails=True)
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

    async def fetch_trip_data(self, start_date, end_date):
        async def attempt_fetch():
            await self.client.get_access_token()
            headers = {"Authorization": f"Bearer {self.client.access_token}"}

            start_time = format_date(get_start_of_day(start_date))
            end_time = format_date(get_end_of_day(end_date))

            summary_url = f"https://www.bouncie.app/api/vehicles/{VEHICLE_ID}/triplegs/details/summary?bands=true&defaultColor=%2355AEE9&overspeedColor=%23CC0000&startDate={start_time}&endDate={end_time}"

            async with self.client._session.get(summary_url, headers=headers) as response:
                if response.status == 200:
                    logging.info(
                        f"Successfully fetched data from {start_date} to {end_date}")
                    return await response.json()
                elif response.status == 401:
                    logging.warning(
                        "Received 401 Unauthorized. Attempting to get a new access token.")
                    return None
                else:
                    logging.error(
                        f"Error fetching data from {start_date} to {end_date}. Status: {response.status}")
                    return None

        result = await attempt_fetch()
        if result is None:
            # If we got a 401, try to get a new access token and fetch again
            await self.client.get_access_token()
            result = await attempt_fetch()

        return result

    async def get_trip_metrics(self):
        time_since_update = datetime.now(
            timezone.utc) - self.live_trip_data["last_updated"]
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

        logging.info(f"Returning trip metrics: {formatted_metrics}")
        return formatted_metrics

    def _format_time(self, seconds):
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
