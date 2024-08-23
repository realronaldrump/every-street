import os
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
from shapely.geometry import Polygon, LineString, MultiLineString
from rtree import index

from bouncie_api import BouncieAPI
from github_updater import GitHubUpdater

VEHICLE_ID = os.getenv("VEHICLE_ID")


class GeoJSONHandler:
    def __init__(self):
        self.bouncie_api = BouncieAPI()
        self.github_updater = GitHubUpdater()
        self.historical_geojson_features = []
        self.fetched_trip_timestamps = set()  # Store timestamps of fetched trips
        self.idx = index.Index()  # Initialize the R-tree index

    def _flatten_coordinates(self, coords):
        """Helper function to flatten a nested list of coordinates."""
        flat_coords = []
        for item in coords:
            if isinstance(item, list):
                # Check if the item is a coordinate pair (list of two numbers)
                if len(item) == 2 and all(isinstance(c, (float, int)) for c in item):
                    flat_coords.append(item)
                else:
                    # Recursively flatten nested lists
                    flat_coords.extend(self._flatten_coordinates(item))
            else:
                logging.warning(f"Unexpected item in coordinates: {item}")
        return flat_coords

    def _calculate_bounding_box(self, feature):
        """Helper function to calculate the bounding box of a feature."""
        coords = feature["geometry"]["coordinates"]

        # Flatten the coordinates
        coords = self._flatten_coordinates(coords)

        # Initialize min and max values with the first coordinate
        min_lon = coords[0][0]
        min_lat = coords[0][1]
        max_lon = coords[0][0]
        max_lat = coords[0][1]

        # Iterate through all coordinates to find the actual min and max values
        for lon, lat in coords:
            min_lon = min(min_lon, lon)
            max_lon = max(max_lon, lon)
            min_lat = min(min_lat, lat)
            max_lat = max(max_lat, lat)

        return (min_lon, min_lat, max_lon, max_lat)

    def load_waco_boundary(self, boundary_type):
        """Loads the specified Waco boundary from a GeoJSON file."""
        try:
            with open(f"static/{boundary_type}.geojson", "r") as f:
                data = json.load(f)
                features = data.get("features", [])
                if features:
                    # Assuming the first feature contains the boundary coordinates
                    boundary_coordinates = features[0]["geometry"]["coordinates"]
                    return boundary_coordinates
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
            # Check that waco_limits is a list of lists
            if not all(isinstance(sublist, list) for sublist in waco_limits):
                raise ValueError(
                    "waco_limits must be a list of lists representing coordinates."
                )

            # Flatten the waco_limits list to a list of tuples (handling extra nesting)
            flattened_waco_limits = [
                (coord[0], coord[1])
                for coord in self._flatten_coordinates(waco_limits)
            ]

            # Ensure that the flattened list has more than one point
            if len(flattened_waco_limits) <= 1:
                raise ValueError(f"waco_limits invalid: {flattened_waco_limits}")

            logging.debug(f"Flattened Waco Limits: {flattened_waco_limits}")

            # Create the Waco polygon, handling potential holes
            if len(waco_limits) > 1:
                # If there are multiple rings, the first is the outer boundary,
                # and the rest are holes
                exterior_coords = waco_limits[0][0]  # Extract the first ring
                holes = [ring[0] for ring in waco_limits[1:]]  # Extract other rings
                waco_polygon = Polygon(exterior_coords, holes=holes)
            else:
                # If there's only one ring, it's the outer boundary
                waco_polygon = Polygon(waco_limits[0][0])  # Extract the first ring

            # Apply a small buffer to resolve topology issues
            waco_polygon = waco_polygon.buffer(0)

            # Extract and validate the route coordinates
            route_geometry = feature["geometry"]
            route_type = route_geometry["type"]
            route_coords = route_geometry.get("coordinates", [])

            # Handle different geometry types
            if route_type == "LineString":
                # Ensure coordinates are in (longitude, latitude) order
                route_coords = [(coord[0], coord[1]) for coord in route_coords]

                # Handle single-point routes
                if len(route_coords) == 1:
                    logging.warning(
                        f"Single-point route encountered, skipping: {route_coords}"
                    )
                    return None  # Skip single-point routes

                route_line = LineString(route_coords)
                clipped_geometry = route_line.intersection(waco_polygon)

            elif route_type == "MultiLineString":
                clipped_lines = []
                for line_coords in route_coords:
                    # Ensure coordinates are in (longitude, latitude) order
                    line_coords = [(coord[0], coord[1]) for coord in line_coords]

                    route_line = LineString(line_coords)
                    clipped_line = route_line.intersection(waco_polygon)

                    # Only add valid, non-empty LineStrings
                    if (
                        not clipped_line.is_empty
                        and clipped_line.is_valid
                        and isinstance(clipped_line, LineString)
                    ):
                        clipped_lines.append(list(clipped_line.coords))

                if clipped_lines:
                    clipped_geometry = MultiLineString(clipped_lines)
                else:
                    return None  # No clipped lines

            else:
                logging.warning(f"Unsupported geometry type: {route_type}")
                return None

            # Handle empty or invalid geometries
            if clipped_geometry.is_empty or not clipped_geometry.is_valid:
                logging.debug(
                    "Clipped geometry is empty or invalid, skipping this feature."
                )
                return None

            # Create the clipped feature
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
            return  # Data already loaded

        try:
            logging.info("Attempting to load historical data from file.")
            with open("static/historical_data.geojson", "r") as f:
                data = json.load(f)
                self.historical_geojson_features = data.get("features", [])
                logging.info(
                    f"Loaded {len(self.historical_geojson_features)} features from historical_data.geojson"
                )

                if not self.historical_geojson_features:
                    logging.warning("Historical data file exists but contains no features.")
                    await self.update_historical_data(fetch_all=True)
                else:
                    # Create the spatial index
                    for i, feature in enumerate(self.historical_geojson_features):
                        bbox = self._calculate_bounding_box(feature)
                        self.idx.insert(i, bbox)

        except FileNotFoundError:
            logging.info(
                "No existing GeoJSON file found. Fetching historical data from Bouncie."
            )
            await self.update_historical_data(fetch_all=True)
        except json.JSONDecodeError:
            logging.error("Error decoding JSON from historical_data.geojson. File may be corrupted.")
            await self.update_historical_data(fetch_all=True)
        except Exception as e:
            logging.error(f"Unexpected error loading historical data: {str(e)}")
            raise

        logging.info(f"Total features after loading: {len(self.historical_geojson_features)}")

    def filter_geojson_features(self, start_date, end_date, filter_waco, waco_limits):
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        end_datetime += timedelta(days=1) - timedelta(seconds=1)

        filtered_features = []

        logging.info(
            f"Filtering features from {start_datetime} to {end_datetime}, filter_waco={filter_waco}"
        )
        logging.info(f"Total features before filtering: {len(self.historical_geojson_features)}")

        for i, feature in enumerate(self.historical_geojson_features):
            timestamp = feature["properties"].get("timestamp")
            if timestamp is not None:
                route_datetime = datetime.fromtimestamp(timestamp, timezone.utc)
                logging.debug(f"Feature {i} timestamp: {route_datetime}")
                if start_datetime <= route_datetime <= end_datetime:
                    logging.debug(f"Feature {i} within date range")
                    if filter_waco and waco_limits:
                        # Clip the route to the Waco boundary
                        clipped_route = self.clip_route_to_boundary(
                            feature, waco_limits
                        )
                        if clipped_route:
                            filtered_features.append(clipped_route)
                            logging.debug(f"Feature {i} clipped and added")
                        else:
                            logging.debug(f"Feature {i} clipped but resulted in empty geometry")
                    else:
                        # No Waco filter, add the entire route
                        filtered_features.append(feature)
                        logging.debug(f"Feature {i} added (no Waco filter)")
                else:
                    logging.debug(f"Feature {i} outside date range: {route_datetime}")
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
            if len(coord) >= 5:  # Ensure the coordinate has a timestamp
                timestamps.append(coord[4])
        return timestamps

    async def update_historical_data(self, fetch_all=False):
        try:
            logging.info("Starting update_historical_data")
            await self.bouncie_api.client.get_access_token()
            logging.info("Access token obtained")

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
                ) 
                logging.info(
                    f"Fetching historical data from Bouncie since {latest_date}."
                )
            else:
                latest_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
                logging.info(
                    "Fetching historical data from Bouncie since 2020-01-01."
                )

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

            logging.info(f"Fetched {len(all_trips)} trips")
            new_features = self.create_geojson_features_from_trips(all_trips)
            logging.info(f"Created {len(new_features)} new features from trips")
            if new_features:
                # Filter out features that already exist in historical_geojson_features
                existing_timestamps = set(feature["properties"]["timestamp"] for feature in self.historical_geojson_features)
                unique_new_features = [feature for feature in new_features if feature["properties"]["timestamp"] not in existing_timestamps]
                
                if unique_new_features:
                    self.historical_geojson_features.extend(unique_new_features)
                    logging.info(f"Added {len(unique_new_features)} new unique features.")

                    with open("static/historical_data.geojson", "w") as f:
                        json.dump(
                            {
                                "type": "FeatureCollection",
                                "features": self.historical_geojson_features,
                            },
                            f,
                        )

                    self.github_updater.push_changes()

                    # Update the spatial index after adding new features
                    for i, feature in enumerate(unique_new_features):
                        bbox = self._calculate_bounding_box(feature)
                        self.idx.insert(
                            len(self.historical_geojson_features)
                            - len(unique_new_features)
                            + i,
                            bbox,
                        )
                else:
                    logging.info("No new unique features to add.")
            else:
                logging.info("No new features created from fetched trips.")

        except Exception as e:
            logging.error(f"An error occurred during historical data update: {e}", exc_info=True)
            raise

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

            # Check if coordinates list has more than one point
            if len(coordinates) > 1 and timestamp is not None:
                feature = {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": {"timestamp": timestamp},
                }
                features.append(feature)

        logging.info(f"Created {len(features)} GeoJSON features from trip data")
        return features
