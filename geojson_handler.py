import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import aiofiles
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box, shape
from tqdm import tqdm

from bouncie_api import BouncieAPI
from date_utils import (days_ago, format_date, get_end_of_day,
                        get_start_of_day, parse_date)

VEHICLE_ID = os.getenv("VEHICLE_ID")

logger = logging.getLogger(__name__)

class GeoJSONHandler:
    def __init__(self, waco_analyzer):
        self.bouncie_api = BouncieAPI()
        self.historical_geojson_features = []
        self.fetched_trip_timestamps = set()
        self.monthly_data = defaultdict(list)
        self.waco_analyzer = waco_analyzer
        self.lock = asyncio.Lock()
        self.waco_boundaries = {}

    @staticmethod
    def _flatten_coordinates(coords):
        return np.array(coords).reshape(-1, 2)

    @staticmethod
    def _calculate_bounding_box(feature):
        coords = np.array(feature["geometry"]["coordinates"]).reshape(-1, 2)
        return coords.min(axis=0).tolist() + coords.max(axis=0).tolist()

    def load_waco_boundary(self, boundary_type):
        if boundary_type not in self.waco_boundaries:
            try:
                gdf = gpd.read_file(f"static/{boundary_type}.geojson")
                if not gdf.empty:
                    self.waco_boundaries[boundary_type] = gdf.geometry.unary_union
                    return self.waco_boundaries[boundary_type]
                logger.error(f"No features found in {boundary_type}.geojson")
                return None
            except FileNotFoundError:
                logger.error(f"File not found: static/{boundary_type}.geojson")
                return None
            except Exception as e:
                logger.error(f"Error loading Waco boundary: {e}")
                return None
        return self.waco_boundaries[boundary_type]

    @staticmethod
    def filter_streets_by_boundary(streets_geojson, waco_limits):
        streets_gdf = gpd.GeoDataFrame.from_features(streets_geojson['features'])
        filtered_gdf = streets_gdf[streets_gdf.intersects(waco_limits)]
        return filtered_gdf.__geo_interface__

    @staticmethod
    def clip_route_to_boundary(feature, waco_limits):
        try:
            route_geometry = shape(feature["geometry"])
            clipped_geometry = route_geometry.intersection(waco_limits)

            if clipped_geometry.is_empty:
                return None

            return {
                "type": "Feature",
                "geometry": gpd.GeoSeries([clipped_geometry]).__geo_interface__['features'][0]['geometry'],
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

                total_features = 0
                if monthly_files:
                    with tqdm(total=len(monthly_files), desc="Loading and processing historical data", unit="file") as pbar:
                        for file in monthly_files:
                            async with aiofiles.open(f"static/{file}", "r") as f:
                                data = json.loads(await f.read())
                                month_features = data.get("features", [])

                                # Only add features if their timestamp is not already in fetched_trip_timestamps
                                for feature in month_features:
                                    timestamp = feature["properties"]["timestamp"]
                                    if timestamp not in self.fetched_trip_timestamps:
                                        self.historical_geojson_features.append(feature)
                                        self.fetched_trip_timestamps.add(timestamp)

                                month_year = file.split('_')[2].split('.')[0]
                                self.monthly_data[month_year] = month_features
                                total_features += len(month_features)

                            pbar.update(1)
                            pbar.set_postfix({"Total Features": total_features, "Current Month": month_year})

                    logger.info(f"Loaded {total_features} features from {len(monthly_files)} monthly files")


                await self.update_all_progress()

            except Exception as e:
                logger.error(f"Unexpected error loading historical data: {str(e)}", exc_info=True)
                raise Exception(f"Error loading historical data: {str(e)}")

    async def update_historical_data(self, fetch_all=False):
        async with self.lock:
            try:
                logger.info("Starting update_historical_data")

                if fetch_all:
                    start_date = datetime(2020, 8, 1, tzinfo=timezone.utc)
                    logger.info(f"Fetching all data starting from {start_date}")
                elif self.historical_geojson_features:
                    latest_timestamp = max(
                        feature["properties"]["timestamp"]
                        for feature in self.historical_geojson_features
                        if feature["properties"].get("timestamp") is not None
                    )
                    start_date = datetime.fromtimestamp(latest_timestamp, tz=timezone.utc) + timedelta(days=1)
                    logger.info(f"Fetching data starting from the latest timestamp: {start_date}")
                else:
                    start_date = await self.find_first_data_date()
                    logger.info(f"No historical features loaded, fetching data starting from first data date: {start_date}")

                end_date = datetime.now(tz=timezone.utc)
                logger.info(f"Fetching data until {end_date}")

                current_date = start_date
                with tqdm(total=(end_date - start_date).days, desc="Fetching historical data", unit="day") as pbar:
                    while current_date <= end_date:
                        try:
                            logger.info(f"Fetching trips for {current_date.strftime('%Y-%m-%d')}")
                            trips = await self.bouncie_api.fetch_trip_data(current_date, current_date)
                            logger.info(f"Fetched {len(trips)} trips for {current_date.strftime('%Y-%m-%d')}")

                            if trips:
                                new_features = await self._process_trips_in_batches(trips)
                                logger.info(f"Created {len(new_features)} new features from trips on {current_date}")

                                if new_features:
                                    # Filter new features based on timestamp
                                    filtered_new_features = [
                                        feature for feature in new_features
                                        if feature["properties"]["timestamp"] not in self.fetched_trip_timestamps
                                    ]

                                    if filtered_new_features:
                                        await self._update_monthly_files(filtered_new_features)
                                        self.historical_geojson_features.extend(filtered_new_features)
                                        self.fetched_trip_timestamps.update(
                                            feature["properties"]["timestamp"] for feature in filtered_new_features
                                        )
                                        logger.info(f"Added {len(filtered_new_features)} new features to historical_geojson_features")

                                        await self.update_all_progress()
                            else:
                                logger.info(f"No trips found for {current_date.strftime('%Y-%m-%d')}")

                        except Exception as e:
                            logger.error(f"Error processing data for {current_date}: {str(e)}", exc_info=True)

                        current_date += timedelta(days=1)
                        pbar.update(1)

                logger.info("Finished update_historical_data")
            except Exception as e:
                logger.error(f"An error occurred during historical data update: {str(e)}", exc_info=True)
                raise

    async def find_first_data_date(self):
        start_date = datetime(2020, 8, 1, tzinfo=timezone.utc) 
        end_date = datetime.now(tz=timezone.utc)
        step = timedelta(days=1)  

        with tqdm(total=(end_date - start_date).days, desc="Finding first data date", unit="day") as pbar:
            while start_date < end_date:
                trips = await self.bouncie_api.fetch_trip_data(start_date, start_date)  
                
                if trips:
                    logger.info(f"Found first data date: {start_date}")
                    return start_date
                
                start_date += step
                pbar.update(1)

        raise Exception("No data found in the specified date range")

    async def _process_trips_in_batches(self, trips, batch_size=1000):
        new_features = []
        for i in range(0, len(trips), batch_size):
            batch = trips[i:i+batch_size]
            batch_features = await asyncio.to_thread(self.bouncie_api.create_geojson_features_from_trips, batch)
            new_features.extend(batch_features)
            await asyncio.sleep(0)
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
        logger.info(f"Starting _update_monthly_files with {len(new_features)} new features")
        for feature in new_features:
            # Ensure that the coordinates are JSON serializable
            coordinates = feature["geometry"]["coordinates"]
            if isinstance(coordinates, np.ndarray):
                feature["geometry"]["coordinates"] = coordinates.tolist()
            else:
                # In case it's a nested list with ndarrays
                feature["geometry"]["coordinates"] = self._convert_ndarray_to_list(coordinates)

            # Add the feature to the appropriate month in the monthly data
            timestamp = feature["properties"]["timestamp"]
            date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            month_year = date.strftime("%Y-%m")

            self.monthly_data[month_year].append(feature)

        # Write the updated features to the corresponding monthly files
        for month_year, features in self.monthly_data.items():
            filename = f"static/historical_data_{month_year}.geojson"
            logger.info(f"Updating file: {filename}")

            try:
                # Check if the file exists
                if os.path.exists(filename):
                    # Load existing data
                    async with aiofiles.open(filename, "r") as f:
                        existing_data = json.loads(await f.read())
                        existing_features = existing_data.get("features", [])
                else:
                    existing_features = []

                # Append new features to existing features
                all_features = existing_features + features

                # Write all features to the file
                async with aiofiles.open(filename, "w") as f:
                    await f.write(json.dumps({
                        "type": "FeatureCollection",
                        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                        "features": all_features
                    }, indent=4))

                logger.info(f"Successfully wrote {len(all_features)} features to {filename}")
            except Exception as e:
                logger.error(f"Error writing to file {filename}: {str(e)}", exc_info=True)

        logger.info(f"Updated monthly files with {len(new_features)} new features")

    # Helper function to recursively convert any ndarray in a nested list structure
    def _convert_ndarray_to_list(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, list):
            return [self._convert_ndarray_to_list(item) for item in obj]
        else:
            return obj
        
    async def filter_geojson_features(self, start_date, end_date, filter_waco, waco_limits, bounds=None):
        start_datetime = get_start_of_day(parse_date(start_date)).replace(tzinfo=timezone.utc)
        end_datetime = get_end_of_day(parse_date(end_date)).replace(tzinfo=timezone.utc)

        logger.info(f"Filtering features from {start_datetime} to {end_datetime}, filter_waco={filter_waco}")

        filtered_features = []

        if bounds:
            bounding_box = box(*bounds)

        # Check if monthly_data is empty (no historical data loaded)
        if not self.monthly_data:
            logger.warning("No historical data loaded yet. Returning empty features.")
            return filtered_features

        for month_year, features in self.monthly_data.items():
            month_start = datetime.strptime(month_year, "%Y-%m").replace(tzinfo=timezone.utc)
            month_end = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1, tzinfo=timezone.utc) - timedelta(seconds=1)

            if month_start <= end_datetime and month_end >= start_datetime:
                month_features = gpd.GeoDataFrame.from_features(features)

                # Convert timestamp to datetime
                month_features['timestamp'] = pd.to_datetime(month_features['timestamp'], unit='s', utc=True)

                mask = (month_features['timestamp'] >= start_datetime) & (month_features['timestamp'] <= end_datetime)

                if bounds:
                    mask &= month_features.intersects(bounding_box)

                if filter_waco and waco_limits:
                    mask &= month_features.intersects(waco_limits)
                    clipped_features = month_features[mask].intersection(waco_limits)
                else:
                    clipped_features = month_features[mask]

                filtered_features.extend(clipped_features.__geo_interface__['features'])

        logger.info(f"Filtered {len(filtered_features)} features")
        return filtered_features

    async def update_all_progress(self):
        try:
            logger.info("Updating progress for all historical data...")
            total_features = len(self.historical_geojson_features)
            logger.info(f"Total features to process: {total_features}")

            # Pass the list of features directly to update_progress
            await self.waco_analyzer.update_progress(self.historical_geojson_features)

            final_coverage = self.waco_analyzer.calculate_progress()
            logger.info(f"Progress updated successfully. Coverage: {final_coverage['coverage_percentage']:.2f}%")
            return {
                'coverage_percentage': final_coverage['coverage_percentage'],
                'total_streets': final_coverage['total_streets'],
                'traveled_streets': final_coverage['traveled_streets']
            }
        except Exception as e:
            logger.error(f"Error updating progress: {str(e)}", exc_info=True)
            raise

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
            coverage_analysis = self.waco_analyzer.calculate_progress()
            logging.info(f"Raw coverage analysis: {coverage_analysis}")
            return coverage_analysis
        except Exception as e:
            logging.error(f"Error updating Waco streets progress: {str(e)}", exc_info=True)
            return None

    def get_all_routes(self):
        logger.info(f"Retrieving all routes. Total features: {len(self.historical_geojson_features)}")
        return self.historical_geojson_features

# End of GeoJSONHandler class

# You might want to add any additional utility functions or classes here if needed

# For example, you could add a function to ensure the 'static' directory exists:

def ensure_static_directory():
    static_dir = 'static'
    if not os.path.exists(static_dir):
        logger.info(f"Creating 'static' directory at {os.path.abspath(static_dir)}")
        os.makedirs(static_dir)
    else:
        logger.info(f"'static' directory already exists at {os.path.abspath(static_dir)}")

# Call this function when initializing your application
ensure_static_directory()

# End of file