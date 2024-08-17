import os
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from shapely.geometry import Point, Polygon

from bouncie_api import BouncieAPI
from github_updater import GitHubUpdater

VEHICLE_ID = os.getenv("VEHICLE_ID")  # Correctly imports VEHICLE_ID from environment variables

class GeoJSONHandler:
    def __init__(self):
        self.bouncie_api = BouncieAPI()
        self.github_updater = GitHubUpdater()
        self.historical_geojson_features = []

    async def load_historical_data(self):
        if self.historical_geojson_features:
            return  # Data already loaded

        try:
            with open("static/historical_data.geojson", "r") as f:
                data = json.load(f)
                self.historical_geojson_features = data.get("features", [])
                logging.info(f"Loaded {len(self.historical_geojson_features)} features from historical_data.geojson")
        except FileNotFoundError:
            logging.info("No existing GeoJSON file found. Fetching historical data from Bouncie.")
            await self.update_historical_data(fetch_all=True)

    def filter_geojson_features(self, start_date, end_date, filter_waco):
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_datetime += timedelta(days=1) - timedelta(seconds=1)

        filtered_features = []
        waco_limits = None

        if filter_waco:
            with open("static/waco_city_limits.geojson") as f:
                waco_limits = json.load(f)["features"][0]["geometry"]["coordinates"][0]

        logging.info(f"Filtering features from {start_datetime} to {end_datetime}, filter_waco={filter_waco}")

        for feature in self.historical_geojson_features:
            timestamp = feature["properties"].get("timestamp")
            if timestamp is not None:
                route_datetime = datetime.fromtimestamp(timestamp, timezone.utc)
                print(f"Feature Timestamp: {route_datetime}, Start: {start_datetime}, End: {end_datetime}")
                if start_datetime <= route_datetime <= end_datetime:
                    if filter_waco:
                        if self.is_route_in_waco(feature, waco_limits):
                            filtered_features.append(feature)
                            logging.debug("Feature included (within Waco)")  # Debugging inclusion
                        else:
                            logging.debug("Feature excluded (outside Waco)")  # Debugging exclusion
                    else:
                        filtered_features.append(feature)
                        logging.debug("Feature included (Waco filter disabled)")  # Debugging inclusion

        logging.info(f"Filtered {len(filtered_features)} features")
        return filtered_features

    def is_route_in_waco(self, feature, waco_limits):
        waco_polygon = Polygon(waco_limits)
        for coord in feature["geometry"]["coordinates"]:
            point = Point(coord[0], coord[1])
            if not waco_polygon.contains(point):
                return False
        return True

    async def update_historical_data(self, fetch_all=False):
        try:
            await self.bouncie_api.client.get_access_token()

            if fetch_all:
                latest_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
                logging.info("Fetching all historical data from Bouncie.")
            elif self.historical_geojson_features:
                latest_timestamp = max(
                    feature["properties"]["timestamp"]
                    for feature in self.historical_geojson_features
                    if feature["properties"].get("timestamp") is not None
                )
                latest_date = datetime.fromtimestamp(
                    latest_timestamp, tz=timezone.utc
                ) + timedelta(days=1)
                logging.info(f"Fetching historical data from Bouncie since {latest_date}.")
            else:
                latest_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
                logging.info("Fetching historical data from Bouncie since 2020-01-01.")

            today = datetime.now(tz=timezone.utc)
            all_trips = []

            async with aiohttp.ClientSession() as session:
                headers = {
                    "Accept": "application/json",
                    "Authorization": self.bouncie_api.client.access_token,
                }
                current_date = latest_date
                while current_date < today:
                    date_str = current_date.strftime("%Y-%m-%d")
                    trips_data = await self.bouncie_api.fetch_trip_data(
                        session, VEHICLE_ID, date_str, headers
                    )
                    if trips_data:
                        all_trips.extend(trips_data)
                        logging.info(f"Fetched trips data for {date_str}")
                    else:
                        logging.info(f"No trips data found for {date_str}")
                    current_date += timedelta(days=1)

            new_features = self.create_geojson_features_from_trips(all_trips)
            if new_features:
                self.historical_geojson_features.extend(new_features)

                with open("static/historical_data.geojson", "w") as f:
                    json.dump(
                        {"type": "FeatureCollection", "features": self.historical_geojson_features},
                        f,
                    )

                self.github_updater.push_changes()

        except Exception as e:
            logging.error(f"An error occurred during historical data update: {e}")

    def create_geojson_features_from_trips(self, data):
        features = []

        for trip in data:
            if not isinstance(trip, dict):
                continue

            coordinates = []
            for band in trip.get("bands", []):
                for path in band.get("paths", []):
                    for point in path:
                        lat, lon, _, _, timestamp, _ = point
                        coordinates.append([lon, lat])

            if coordinates:
                feature = {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": {"timestamp": timestamp},
                }
                features.append(feature)

        logging.info(f"Created {len(features)} GeoJSON features from trip data")
        return features