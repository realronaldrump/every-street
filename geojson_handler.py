import os
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon, LineString, MultiLineString, box, shape
from rtree import index
import aiofiles
from bouncie_api import BouncieAPI
from date_utils import parse_date, format_date, get_start_of_day, get_end_of_day, days_ago

VEHICLE_ID = os.getenv("VEHICLE_ID")

logger = logging.getLogger(__name__)

class GeoJSONHandler:
    def __init__(self, waco_analyzer):
        self.bouncie_api = BouncieAPI()
        self.historical_geojson_features = []
        self.fetched_trip_timestamps = set()
        self.idx = index.Index()
        self.monthly_data = defaultdict(list)
        self.waco_analyzer = waco_analyzer
        self.lock = asyncio.Lock()

    def _flatten_coordinates(self, coords):
        flat_coords = []
        for item in coords:
            if isinstance(item, list):
                if len(item) == 2 and all(isinstance(c, (float, int)) for c in item):
                    flat_coords.append(item)
                else:
                    flat_coords.extend(self._flatten_coordinates(item))
            else:
                logger.warning(f"Unexpected item in coordinates: {item}")
        return flat_coords

    def _calculate_bounding_box(self, feature):
        coords = self._flatten_coordinates(feature["geometry"]["coordinates"])
        min_lon, min_lat = max_lon, max_lat = coords[0]
        for lon, lat in coords:
            min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
        return (min_lon, min_lat, max_lon, max_lat)

    def load_waco_boundary(self, boundary_type):
        try:
            gdf = gpd.read_file(f"static/{boundary_type}.geojson")
            if not gdf.empty:
                return gdf.geometry.unary_union
            logger.error(f"No features found in {boundary_type}.geojson")
            return None
        except FileNotFoundError:
            logger.error(f"File not found: static/{boundary_type}.geojson")
            return None
        except Exception as e:
            logger.error(f"Error loading Waco boundary: {e}")
            return None

    def filter_streets_by_boundary(self, streets_geojson, waco_limits):
        filtered_features = []
        for feature in streets_geojson['features']:
            street_geometry = shape(feature['geometry'])
            if street_geometry.intersects(waco_limits):
                filtered_features.append(feature)
        return {"type": "FeatureCollection", "features": filtered_features}

    def clip_route_to_boundary(self, feature, waco_limits):
        try:
            if isinstance(waco_limits, (Polygon, MultiPolygon)):
                waco_polygon = waco_limits
            else:
                raise ValueError("waco_limits must be a Polygon or MultiPolygon")

            route_geometry = feature["geometry"]
            route_type = route_geometry["type"]
            route_coords = route_geometry["coordinates"]

            if route_type == "LineString":
                route_line = LineString(route_coords)
                clipped_geometry = route_line.intersection(waco_polygon)
            elif route_type == "MultiLineString":
                route_multi_line = MultiLineString(route_coords)
                clipped_geometry = route_multi_line.intersection(waco_polygon)
            else:
                logger.warning(f"Unsupported geometry type: {route_type}")
                return None

            if clipped_geometry.is_empty:
                return None

            return {
                "type": "Feature",
                "geometry": {
                    "type": clipped_geometry.geom_type,
                    "coordinates": list(clipped_geometry.coords) if isinstance(clipped_geometry, LineString)
                    else [list(line.coords) for line in clipped_geometry.geoms] if isinstance(clipped_geometry, MultiLineString)
                    else []
                },
                "properties": feature["properties"],
            }
        except Exception as e:
            logger.error(f"Error clipping route to boundary: {e}")
            logger.debug(f"Feature: {feature}")
            return None

    async def load_historical_data(self):
        async with self.lock:
            if self.historical_geojson_features:
                logger.info("Historical data already loaded.")
                return

            try:
                logger.info("Loading historical data from monthly files.")
                monthly_files = [f for f in os.listdir('static') if f.startswith('historical_data_') and f.endswith('.geojson')]

                for file in monthly_files:
                    async with aiofiles.open(f"static/{file}", "r") as f:
                        data = json.loads(await f.read())
                        month_features = data.get("features", [])
                        month_year = file.split('_')[2].split('.')[0]
                        self.historical_geojson_features.extend(month_features)
                        self.monthly_data[month_year] = month_features

                logger.info(f"Loaded {len(self.historical_geojson_features)} features from {len(monthly_files)} monthly files")

                if not self.historical_geojson_features:
                    logger.warning("No historical data found in monthly files.")
                    await self.update_historical_data(fetch_all=True)
                else:
                    for i, feature in enumerate(self.historical_geojson_features):
                        bbox = self._calculate_bounding_box(feature)
                        self.idx.insert(i, bbox)

                await self.update_all_progress()

            except Exception as e:
                logger.error(f"Unexpected error loading historical data: {str(e)}", exc_info=True)
                raise Exception(f"Error loading historical data: {str(e)}")

    async def update_historical_data(self, fetch_all=False):
        async with self.lock:
            try:
                logger.info("Starting update_historical_data")

                if fetch_all:
                    latest_date = datetime(2020, 8, 1, tzinfo=timezone.utc)
                elif self.historical_geojson_features:
                    latest_timestamp = max(
                        feature["properties"]["timestamp"]
                        for feature in self.historical_geojson_features
                        if feature["properties"].get("timestamp") is not None
                    )
                    latest_date = datetime.fromtimestamp(latest_timestamp, tz=timezone.utc)
                else:
                    latest_date = datetime(2020, 8, 1, tzinfo=timezone.utc)

                today = datetime.now(tz=timezone.utc)
                all_trips = await self.bouncie_api.fetch_historical_data(latest_date, today)

                logger.info(f"Fetched {len(all_trips)} trips")
                new_features = await self._process_trips_in_batches(all_trips)
                logger.info(f"Created {len(new_features)} new features from trips")

                if new_features:
                    await self._update_monthly_files(new_features)
                    self.historical_geojson_features.extend(new_features)

                    for i, feature in enumerate(new_features):
                        bbox = self._calculate_bounding_box(feature)
                        self.idx.insert(len(self.historical_geojson_features) - len(new_features) + i, bbox)

                    await self.update_all_progress()

            except Exception as e:
                logger.error(f"An error occurred during historical data update: {e}", exc_info=True)
                raise

    async def _process_trips_in_batches(self, trips, batch_size=1000):
        new_features = []
        for i in range(0, len(trips), batch_size):
            batch = trips[i:i+batch_size]
            batch_features = await asyncio.to_thread(self.create_geojson_features_from_trips, batch)
            new_features.extend(batch_features)
            await asyncio.sleep(0)  # Allow other tasks to run
        return new_features

    def get_progress(self):
        return self.waco_analyzer.calculate_progress()

    def get_progress_geojson(self, waco_boundary='city_limits'):
        return self.waco_analyzer.get_progress_geojson(waco_boundary)

    async def get_recent_historical_data(self):
        try:
            yesterday = days_ago(1)
            filtered_features = await self.filter_geojson_features(
                format_date(yesterday), 
                format_date(datetime.now(timezone.utc)), 
                filter_waco=False, 
                waco_limits=None,
            )
            return filtered_features
        except Exception as e:
            logger.error(f"Error in get_recent_historical_data: {str(e)}", exc_info=True)
            return []

    async def _update_monthly_files(self, new_features):
        for feature in new_features:
            timestamp = feature["properties"]["timestamp"]
            date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            month_year = date.strftime("%Y-%m")

            self.monthly_data[month_year].append(feature)

        for month_year, features in self.monthly_data.items():
            filename = f"static/historical_data_{month_year}.geojson"
            async with aiofiles.open(filename, "w") as f:
                await f.write(json.dumps({
                    "type": "FeatureCollection",
                    "crs": { "type": "name", "properties": { "name": "EPSG:4326" } }, 
                    "features": features
                }, indent=4))

        logger.info(f"Updated monthly files with {len(new_features)} new features")

    async def filter_geojson_features(self, start_date, end_date, filter_waco, waco_limits, bounds=None):
        start_datetime = get_start_of_day(parse_date(start_date)).replace(tzinfo=timezone.utc)
        end_datetime = get_end_of_day(parse_date(end_date)).replace(tzinfo=timezone.utc)

        logger.info(f"Filtering features from {start_datetime} to {end_datetime}, filter_waco={filter_waco}")

        filtered_features = []

        if bounds:
            bounding_box = box(*bounds)

        for month_year, features in self.monthly_data.items():
            month_start = datetime.strptime(month_year, "%Y-%m").replace(tzinfo=timezone.utc)
            month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1, tzinfo=timezone.utc) - timedelta(seconds=1)

            if month_start <= end_datetime and month_end >= start_datetime:
                for feature in features:
                    timestamp = feature["properties"].get("timestamp")
                    if timestamp is not None:
                        try:
                            timestamp = int(float(timestamp))
                            route_datetime = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                            if start_datetime <= route_datetime <= end_datetime:
                                if bounds:
                                    feature_geom = shape(feature['geometry'])
                                    if not feature_geom.intersects(bounding_box):
                                        continue

                                if filter_waco and waco_limits:
                                    clipped_route = self.clip_route_to_boundary(feature, waco_limits)
                                    if clipped_route:
                                        filtered_features.append(clipped_route)
                                else:
                                    filtered_features.append(feature)
                        except ValueError:
                            logger.warning(f"Invalid timestamp for feature: {timestamp}")
                    else:
                        logger.warning(f"Feature has no timestamp")

        logger.info(f"Filtered {len(filtered_features)} features")
        return filtered_features

    async def update_all_progress(self):
        try:
            logger.info("Updating progress for all historical data...")
            await self.waco_analyzer.update_progress(self.historical_geojson_features)
            coverage = self.waco_analyzer.analyze_coverage()
            logger.info(f"Progress updated successfully. Coverage: {coverage['coverage_percentage']:.2f}%")
            return coverage
        except Exception as e:
            logger.error(f"Error updating progress: {str(e)}", exc_info=True)
            raise

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
                    logger.warning(f"Invalid timestamp format: {timestamp}")
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

        logger.info(f"Created {len(features)} GeoJSON features from trip data")
        return features

    async def initialize_data(self):
        try:
            logger.info("Starting to load historical data...")
            await self.load_historical_data()
            logger.info("Historical data loaded successfully.")
            logger.info("Updating progress...")
            await self.update_all_progress()
            logger.info("Progress updated successfully.")
        except Exception as e:
            logger.error(f"Error during data initialization: {str(e)}", exc_info=True)
            raise

    def get_waco_streets(self, waco_boundary, streets_filter='all'):
        try:
            logger.info(f"Getting Waco streets: boundary={waco_boundary}, filter={streets_filter}")
            street_network = self.waco_analyzer.get_street_network(waco_boundary)
            logger.info(f"Total streets before filtering: {len(street_network)}")

            if streets_filter == 'traveled':
                street_network = street_network[street_network['traveled']]
            elif streets_filter == 'untraveled':
                street_network = street_network[~street_network['traveled']]

            logger.info(f"Streets after filtering: {len(street_network)}")
            return street_network.to_json()
        except Exception as e:
            logger.error(f"Error in get_waco_streets: {str(e)}", exc_info=True)
            raise

    def get_untraveled_streets(self, waco_boundary):
        return self.waco_analyzer.get_untraveled_streets(waco_boundary).to_json()

    async def update_waco_streets_progress(self):
        try:
            coverage_analysis = self.waco_analyzer.analyze_coverage()
            logging.info(f"Raw coverage analysis: {coverage_analysis}")
            return coverage_analysis
        except Exception as e:
            logging.error(f"Error updating Waco streets progress: {str(e)}", exc_info=True)
            return None

    def get_all_routes(self):
        logger.info(f"Retrieving all routes. Total features: {len(self.historical_geojson_features)}")
        return self.historical_geojson_features
