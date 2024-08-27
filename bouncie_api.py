import asyncio
import logging
from datetime import datetime, timezone, timedelta
from geopy.distance import geodesic
from bounciepy import AsyncRESTAPIClient
from geopy.geocoders import Nominatim
from dotenv import load_dotenv
import os
import aiohttp
from logging_config import setup_logging
setup_logging()

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
AUTH_CODE = os.getenv("AUTH_CODE")
VEHICLE_ID = os.getenv("VEHICLE_ID")
DEVICE_IMEI = os.getenv("DEVICE_IMEI")

ENABLE_GEOCODING = True

logger = logging.getLogger(__name__)

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
                logger.error("No vehicle data or stats found in Bouncie response")
                return None

            stats = vehicle_data["stats"]
            location = stats.get("location")

            if not location:
                logger.error("No location data found in Bouncie stats")
                return None

            location_address = (
                await self.reverse_geocode(location["lat"], location["lon"])
                if ENABLE_GEOCODING
                else "N/A"
            )

            timestamp_unix = self._parse_timestamp(stats["lastUpdated"])
            if timestamp_unix is None:
                return None

            battery_state = self._get_battery_state(stats["battery"]["status"])

            logger.info(
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
            logger.error(f"An error occurred while fetching live data: {e}")
            return None

    async def reverse_geocode(self, lat, lon, retries=3):
        for attempt in range(retries):
            try:
                location = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: self.geolocator.reverse((lat, lon), addressdetails=True)
                )
                if location:
                    return self._format_address(location.raw["address"])
                else:
                    return "N/A"
            except Exception as e:
                logger.error(
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
                logger.info(f"Successfully fetched data for {date}")
                return await response.json()
            else:
                logger.error(f"Error fetching data for {date}. Status: {response.status}")
                return None

    async def get_trip_metrics(self):
        time_since_update = datetime.now(timezone.utc) - self.live_trip_data["last_updated"]
        if time_since_update.total_seconds() > 45:
            self.live_trip_data["data"] = []

        total_distance, total_time, max_speed, start_time, end_time = self._calculate_trip_metrics()

        formatted_metrics = {
            "total_distance": round(total_distance, 2),
            "total_time": self._format_time(total_time),
            "max_speed": max_speed,
            "start_time": self._format_datetime(start_time),
            "end_time": self._format_datetime(end_time),
        }

        logger.info(f"Returning trip metrics: {formatted_metrics}")
        return formatted_metrics

    def _parse_timestamp(self, timestamp_iso):
        try:
            timestamp_dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
            return int(timestamp_dt.timestamp())
        except Exception as e:
            logger.error(f"Error converting timestamp: {e}")
            return None

    def _get_battery_state(self, bouncie_status):
        return (
            "full"
            if bouncie_status == "normal"
            else "unplugged"
            if bouncie_status == "low"
            else "unknown"
        )

    def _format_address(self, address):
        components = [
            address.get("place", ""),
            address.get("building", ""),
            f"{address.get('house_number', '')} {address.get('road', '')}",
            f"{address.get('city', '')}, {address.get('state', '')} {address.get('postcode', '')}"
        ]
        return "<br>".join(filter(bool, components))

    def _calculate_trip_metrics(self):
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

        return total_distance, total_time, max_speed, start_time, end_time

    def _format_time(self, seconds):
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _format_datetime(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S") if timestamp else "N/A"

    async def fetch_historical_data(self, start_date, end_date):
        try:
            await self.client.get_access_token()
            headers = {
                "Accept": "application/json",
                "Authorization": self.client.access_token,
            }
            async with aiohttp.ClientSession() as session:
                tasks = []
                current_date = start_date
                while current_date <= end_date:
                    date_str = current_date.strftime("%Y-%m-%d")
                    tasks.append(self.fetch_trip_data(session, VEHICLE_ID, date_str, headers))
                    current_date += timedelta(days=1)

                results = await asyncio.gather(*tasks)
                return [result for result in results if result]
        except Exception as e:
            logger.error(f"Error fetching historical data: {e}")
            return []