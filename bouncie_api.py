import asyncio
import logging
from datetime import datetime, timezone, timedelta
from geopy.distance import geodesic
from bounciepy import AsyncRESTAPIClient
from geopy.geocoders import Nominatim
from dotenv import load_dotenv
import os
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
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
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def reverse_geocode(self, lat, lon):
        try:
            location = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.geolocator.reverse((lat, lon), addressdetails=True)
            )
            if location:
                return self._format_address(location.raw["address"])
            return "N/A"
        except Exception as e:
            logger.error(f"Reverse geocoding failed with error: {e}")
            raise

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    async def fetch_trip_data(self, session, vehicle_id, date, headers):
        start_time = f"{date}T00:00:00-05:00"
        end_time = f"{date}T23:59:59-05:00"
        summary_url = f"https://www.bouncie.app/api/vehicles/{vehicle_id}/triplegs/details/summary?bands=true&defaultColor=%2355AEE9&overspeedColor=%23CC0000&startDate={start_time}&endDate={end_time}"

        async with session.get(summary_url, headers=headers) as response:
            if response.status == 200:
                logger.info(f"Successfully fetched data for {date}")
                return await response.json()
            logger.error(f"Error fetching data for {date}. Status: {response.status}")
            response.raise_for_status()

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

    async def update_historical_data(self, db_handler):
        try:
            # Fetch the latest timestamp from the database
            latest_timestamp = db_handler.get_latest_historical_timestamp()

            # If no data exists, start from a default date
            if latest_timestamp is None:
                latest_timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc)

            end_date = datetime.now(timezone.utc)

            all_trips = await self.fetch_historical_data(latest_timestamp, end_date)

            for trip_data in all_trips:
                # Check if trip_data is a list (which seems to be the case)
                if isinstance(trip_data, list):
                    for trip in trip_data:
                        for band in trip.get('bands', []):
                            for path in band.get('paths', []):
                                for point in path:
                                    if len(point) >= 6:
                                        lat, lon, _, speed, timestamp, heading = point
                                        db_handler.add_historical_data_point({
                                            "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc),
                                            "latitude": lat,
                                            "longitude": lon,
                                            "speed": speed,
                                            "heading": heading
                                        })
                else:
                    # Handle the case where trip_data is not a list (as we originally expected)
                    for band in trip_data.get('bands', []):
                        for path in band.get('paths', []):
                            for point in path:
                                if len(point) >= 6:
                                    lat, lon, _, speed, timestamp, heading = point
                                    db_handler.add_historical_data_point({
                                        "timestamp": datetime.fromtimestamp(timestamp, tz=timezone.utc),
                                        "latitude": lat,
                                        "longitude": lon,
                                        "speed": speed,
                                        "heading": heading
                                    })

            logger.info(f"Updated historical data with {len(all_trips)} new trips")
        except Exception as e:
            logger.error(f"Error updating historical data: {str(e)}")
            raise
    
    @staticmethod
    def _parse_timestamp(timestamp_iso):
        try:
            timestamp_dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
            return int(timestamp_dt.timestamp())
        except Exception as e:
            logger.error(f"Error converting timestamp: {e}")
            return None

    @staticmethod
    def _get_battery_state(bouncie_status):
        return (
            "full"
            if bouncie_status == "normal"
            else "unplugged"
            if bouncie_status == "low"
            else "unknown"
        )

    @staticmethod
    def _format_address(address):
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

    @staticmethod
    def _format_time(seconds):
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _format_datetime(timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S") if timestamp else "N/A"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
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
            raise
