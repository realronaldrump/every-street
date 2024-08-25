import os
import asyncio
import json
import logging
import aiofiles
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import aiohttp
from shapely.geometry import Polygon, LineString, MultiLineString, box, shape
from rtree import index

from bouncie_api import BouncieAPI
from date_utils import parse_date, format_date, get_start_of_day, get_end_of_day, date_range, days_ago

VEHICLE_ID = os.getenv("VEHICLE_ID")

class GeoJSONHandler:
    def __init__(self):
        self.bouncie_api = BouncieAPI()
        self.historical_geojson_features = []
        self.fetched_trip_timestamps = set()
        self.idx = index.Index()
        self.monthly_data = defaultdict(list)

    def _flatten_coordinates(self, coords):
        """Helper function to flatten a nested list of coordinates."""
        flat_coords = []
        for item in coords:
            if isinstance(item, list):
                if len(item) == 2 and all(isinstance(c, (float, int)) for c in item):
                    flat_coords.append(item)
                else:
                    flat_coords.extend(self._flatten_coordinates(item))
            else:
                logging.warning(f"Unexpected item in coordinates: {item}")
        return flat_coords

    def _calculate_bounding_box(self, feature):
        """Helper function to calculate the bounding box of a feature."""
        coords = feature["geometry"]["coordinates"]
        coords = self._flatten_coordinates(coords)
        min_lon, min_lat = max_lon, max_lat = coords[0]
        for lon, lat in coords:
            min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
        return (min_lon, min_lat, max_lon, max_lat)

    def load_waco_boundary(self, boundary_type):
        """Loads the specified Waco boundary from a GeoJSON file."""
        try:
            with open(f"static/{boundary_type}.geojson", "r") as f:
                data = json.load(f)
                features = data.get("features", [])
                if features:
                    return features[0]["geometry"]["coordinates"]
                else:
                    logging.error(f"No features found in {boundary_type}.geojson")
                    return None
        except FileNotFoundError:
            logging.error(f"File not found: static/{boundary_type}.geojson")
            return None
        except Exception as e:
            logging.error(f"Error loading Waco boundary: {e}")
            return None

    def clip_route_to_boundary(self, feature, waco_limits):
        try:
            if not all(isinstance(sublist, list) for sublist in waco_limits):
                raise ValueError("waco_limits must be a list of lists representing coordinates.")

            flattened_waco_limits = [
                (coord[0], coord[1])
                for coord in self._flatten_coordinates(waco_limits)
            ]

            if len(flattened_waco_limits) <= 1:
                raise ValueError(f"waco_limits invalid: {flattened_waco_limits}")

            logging.debug(f"Flattened Waco Limits: {flattened_waco_limits}")

            if len(waco_limits) > 1:
                exterior_coords = waco_limits[0][0]
                holes = [ring[0] for ring in waco_limits[1:]]
                waco_polygon = Polygon(exterior_coords, holes=holes)
            else:
                waco_polygon = Polygon(waco_limits[0][0])

            waco_polygon = waco_polygon.buffer(0)

            route_geometry = feature["geometry"]
            route_type = route_geometry["type"]
            route_coords = route_geometry.get("coordinates", [])

            if route_type == "LineString":
                route_coords = [(coord[0], coord[1]) for coord in route_coords]
                if len(route_coords) == 1:
                    logging.warning(f"Single-point route encountered, skipping: {route_coords}")
                    return None
                route_line = LineString(route_coords)
                clipped_geometry = route_line.intersection(waco_polygon)
            elif route_type == "MultiLineString":
                clipped_lines = []
                for line_coords in route_coords:
                    line_coords = [(coord[0], coord[1]) for coord in line_coords]
                    route_line = LineString(line_coords)
                    clipped_line = route_line.intersection(waco_polygon)
                    if not clipped_line.is_empty and clipped_line.is_valid and isinstance(clipped_line, LineString):
                        clipped_lines.append(list(clipped_line.coords))
                if clipped_lines:
                    clipped_geometry = MultiLineString(clipped_lines)
                else:
                    return None
            else:
                logging.warning(f"Unsupported geometry type: {route_type}")
                return None

            if clipped_geometry.is_empty or not clipped_geometry.is_valid:
                logging.debug("Clipped geometry is empty or invalid, skipping this feature.")
                return None

            return {
                "type": "Feature",
                "geometry": {
                    "type": clipped_geometry.geom_type,
                    "coordinates": list(clipped_geometry.coords)
                    if isinstance(clipped_geometry, LineString)
                    else [list(line.coords) for line in clipped_geometry.geoms],
                },
                "properties": feature["properties"],
            }
        except Exception as e:
            logging.error(f"Error clipping route to boundary: {e}")
            logging.debug(f"Feature: {feature}")
            return None

    async def load_historical_data(self):
        if self.historical_geojson_features:
            logging.info("Historical data already loaded.")
            return

        try:
            logging.info("Loading historical data from monthly files.")
            monthly_files = [f for f in os.listdir('static') if f.startswith('historical_data_') and f.endswith('.geojson')]
            
            for file in monthly_files:
                with open(f"static/{file}", "r") as f:
                    data = json.load(f)
                    month_features = data.get("features", [])
                    self.historical_geojson_features.extend(month_features)
                    
                    month_year = file.split('_')[2].split('.')[0]
                    self.monthly_data[month_year] = month_features

            logging.info(f"Loaded {len(self.historical_geojson_features)} features from {len(monthly_files)} monthly files")

            if not self.historical_geojson_features:
                logging.warning("No historical data found in monthly files.")
                await self.update_historical_data(fetch_all=True)
            else:
                for i, feature in enumerate(self.historical_geojson_features):
                    bbox = self._calculate_bounding_box(feature)
                    self.idx.insert(i, bbox)

        except Exception as e:
            logging.error(f"Unexpected error loading historical data: {str(e)}")
            raise

    async def update_historical_data(self, fetch_all=False):
        try:
            logging.info("Starting update_historical_data")
            await self.bouncie_api.client.get_access_token()
            logging.info("Access token obtained")

            if fetch_all:
                latest_date = datetime(2020, 8, 1, tzinfo=timezone.utc)  # Adjust start date if needed
            elif self.historical_geojson_features:
                latest_timestamp = max(
                    feature["properties"]["timestamp"]
                    for feature in self.historical_geojson_features
                    if feature["properties"].get("timestamp") is not None
                )
                latest_date = datetime.fromtimestamp(latest_timestamp, timezone.utc)
            else:
                latest_date = datetime(2020, 8, 1, tzinfo=timezone.utc)

            today = datetime.now(tz=timezone.utc)
            all_trips = []

            async with aiohttp.ClientSession() as session:
                headers = {
                    "Accept": "application/json",
                    "Authorization": self.bouncie_api.client.access_token,
                }
                for current_date in date_range(latest_date, today):
                    date_str = format_date(current_date)
                    trips_data = await self.bouncie_api.fetch_trip_data(
                        session, VEHICLE_ID, date_str, headers
                    )
                    if trips_data:
                        all_trips.extend(trips_data)
                        logging.info(f"Fetched trips data for {date_str}")
                    else:
                        logging.info(f"No trips data found for {date_str}")

            logging.info(f"Fetched {len(all_trips)} trips")
            new_features = self.create_geojson_features_from_trips(all_trips)
            logging.info(f"Created {len(new_features)} new features from trips")

            if new_features:
                await self._update_monthly_files(new_features)
                self.historical_geojson_features.extend(new_features)
                
                for i, feature in enumerate(new_features):
                    bbox = self._calculate_bounding_box(feature)
                    self.idx.insert(len(self.historical_geojson_features) - len(new_features) + i, bbox)

        except Exception as e:
            logging.error(f"An error occurred during historical data update: {e}", exc_info=True)
            raise

    async def _update_monthly_files(self, new_features):
        for feature in new_features:
            timestamp = feature["properties"]["timestamp"]
            date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            month_year = date.strftime("%Y-%m")
            
            self.monthly_data[month_year].append(feature)

        for month_year, features in self.monthly_data.items():
            filename = f"static/historical_data_{month_year}.geojson"
            async with aiofiles.open(filename, "w") as f:
                await f.write(json.dumps({"type": "FeatureCollection", "features": features}))

        logging.info(f"Updated monthly files with {len(new_features)} new features")

    def filter_geojson_features(self, start_date, end_date, filter_waco, waco_limits, features=None, bounds=None):
        start_datetime = get_start_of_day(parse_date(start_date))
        end_datetime = get_end_of_day(parse_date(end_date))

        filtered_features = []

        logging.info(f"Filtering features from {start_datetime} to {end_datetime}, filter_waco={filter_waco}")
        
        features_to_filter = features if features is not None else self.historical_geojson_features
        logging.info(f"Total features before filtering: {len(features_to_filter)}")

        if bounds:
            bounding_box = box(bounds[0], bounds[1], bounds[2], bounds[3])

        for i, feature in enumerate(features_to_filter):
            timestamp = feature["properties"].get("timestamp")
            if timestamp is not None:
                try:
                    # Ensure timestamp is an integer
                    timestamp = int(float(timestamp))
                    route_datetime = datetime.fromtimestamp(timestamp, timezone.utc)
                    logging.debug(f"Feature {i} timestamp: {route_datetime}")
                    if start_datetime <= route_datetime <= end_datetime:
                        logging.debug(f"Feature {i} within date range")
                        
                        # Apply bounding box filter if provided
                        if bounds:
                            feature_geom = shape(feature['geometry'])
                            if not feature_geom.intersects(bounding_box):
                                continue

                        if filter_waco and waco_limits:
                            clipped_route = self.clip_route_to_boundary(feature, waco_limits)
                            if clipped_route:
                                filtered_features.append(clipped_route)
                                logging.debug(f"Feature {i} clipped and added")
                            else:
                                logging.debug(f"Feature {i} clipped but resulted in empty geometry")
                        else:
                            filtered_features.append(feature)
                            logging.debug(f"Feature {i} added (no Waco filter)")
                    else:
                        logging.debug(f"Feature {i} outside date range: {route_datetime}")
                except ValueError:
                    logging.warning(f"Invalid timestamp for feature {i}: {timestamp}")
            else:
                logging.warning(f"Feature {i} has no timestamp")

        logging.info(f"Filtered {len(filtered_features)} features")
        if not filtered_features:
            logging.warning("No features found after filtering")
        return filtered_features

    def get_feature_timestamps(self, feature):
        coordinates = feature["geometry"]["coordinates"]
        timestamps = []
        for coord in coordinates:
            if len(coord) >= 5:
                timestamp = coord[4]
                if isinstance(timestamp, (int, float)):
                    timestamps.append(timestamp)
                elif isinstance(timestamp, tuple) and len(timestamp) >= 1:
                    timestamps.append(timestamp[0])
                else:
                    logging.warning(f"Invalid timestamp format: {timestamp}")
        return timestamps

    def create_geojson_features_from_trips(self, data):
        features = []

        for trip in data:
            if not isinstance(trip, dict):
                continue

            coordinates = []
            timestamp = None
            for band in trip.get("bands", []):
                for path in band.get("paths", []):
                    for point in path:
                        lat, lon, _, _, timestamp, _ = point
                        coordinates.append([lon, lat])

            if len(coordinates) > 1 and timestamp is not None:
                feature = {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": {"timestamp": timestamp},
                }
                features.append(feature)

        logging.info(f"Created {len(features)} GeoJSON features from trip data")
        return features
        
    async def get_recent_historical_data(self):
        """Gets historical data from the last 24 hours."""
        try:
            yesterday = days_ago(1)
            filtered_features = self.filter_geojson_features(
                format_date(yesterday), 
                format_date(datetime.now(timezone.utc)), 
                filter_waco=False, 
                waco_limits=None,
            )
            return filtered_features
        except Exception as e:
            logging.error(f"Error in get_recent_historical_data: {str(e)}", exc_info=True)
            return []  # Return an empty list if there's an error