import logging

import geopandas as gpd
import numpy as np
from rtree import index
from shapely.geometry import LineString

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.00001):
        logger.info("Initializing WacoStreetsAnalyzer...")
        try:
            self.streets_gdf = gpd.read_file(waco_streets_file)
            self.streets_gdf['street_id'] = self.streets_gdf['street_id'].astype(
                str)
            self.snap_distance = snap_distance
            self.traveled_streets = set()
            self.spatial_index = index.Index()
            self._create_spatial_index()

            # Initialize 'traveled_length' column if it doesn't exist
            if 'traveled_length' not in self.streets_gdf.columns:
                self.streets_gdf['traveled_length'] = 0.0

            logger.info(f"Processed {len(self.streets_gdf)} streets.")
        except Exception as e:
            logger.error(f"Error initializing WacoStreetsAnalyzer: {str(e)}")
            raise

    def _create_spatial_index(self):
        for idx, street in self.streets_gdf.iterrows():
            self.spatial_index.insert(idx, street.geometry.bounds)

    async def update_progress(self, routes):
        logging.info(f"Updating progress with {len(routes)} new routes...")
        for route in routes:
            if route['geometry']['type'] == 'LineString':
                coords = route['geometry']['coordinates']
                line = LineString(coords)
                for idx, street in self.streets_gdf.iterrows():
                    try:
                        if line.intersects(street.geometry):
                            distance = line.intersection(
                                street.geometry).length
                            if not np.isnan(distance) and not np.isinf(distance):
                                if 'traveled_length' not in self.streets_gdf.columns:
                                    self.streets_gdf['traveled_length'] = 0.0
                                self.streets_gdf.at[idx,
                                                    'traveled_length'] += distance
                            else:
                                logging.warning(
                                    f"Invalid distance calculated for street {idx}")
                    except Exception as e:
                        logging.error(
                            f"Error processing street {idx}: {str(e)}")

        self.streets_gdf['traveled_percentage'] = (
            self.streets_gdf['traveled_length'] / self.streets_gdf['length']) * 100
        self.streets_gdf['traveled_percentage'] = self.streets_gdf['traveled_percentage'].clip(
            0, 100)
        logging.info("Progress update completed.")

    async def _process_routes(self, routes):
        new_streets = set()
        for route in routes:
            route_line = LineString(route['geometry']['coordinates'])
            possible_matches_idx = list(
                self.spatial_index.intersection(route_line.bounds))
            possible_matches = self.streets_gdf.iloc[possible_matches_idx]

            for _, street in possible_matches.iterrows():
                if route_line.distance(street.geometry) <= self.snap_distance:
                    intersection = route_line.intersection(
                        street.geometry.buffer(self.snap_distance))
                    if not intersection.is_empty:
                        coverage_ratio = intersection.length / street.geometry.length
                        if coverage_ratio >= 0.5:  # Consider a street traveled if at least 50% is covered
                            new_streets.add(street['street_id'])

        return new_streets

    def reset_progress(self):
        logger.info("Resetting progress...")
        self.traveled_streets.clear()

    def calculate_progress(self):
        logger.info("Calculating progress...")
        total_streets = len(self.streets_gdf)
        traveled_streets = len(self.traveled_streets)

        # Project to a suitable UTM zone for Waco, Texas (UTM zone 14N)
        projected_gdf = self.streets_gdf.to_crs(epsg=32614)

        total_length = projected_gdf.geometry.length.sum()
        traveled_length = projected_gdf[projected_gdf['street_id'].isin(
            self.traveled_streets)].geometry.length.sum()

        street_count_percentage = (
            traveled_streets / total_streets) * 100 if total_streets > 0 else 0
        length_percentage = (traveled_length / total_length) * \
            100 if total_length > 0 else 0

        return {
            'street_count_percentage': street_count_percentage,
            'length_percentage': length_percentage,
            'total_streets': total_streets,
            'traveled_streets': traveled_streets,
            'total_length': total_length,
            'traveled_length': traveled_length
        }

    def get_progress_geojson(self, waco_boundary='city_limits'):
        logger.info("Generating progress GeoJSON...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(
                f"static/{waco_boundary}.geojson").geometry.unary_union

        self.streets_gdf['traveled'] = self.streets_gdf['street_id'].isin(
            self.traveled_streets)

        features = [
            {
                "type": "Feature",
                "geometry": street.geometry.__geo_interface__,
                "properties": {
                    "street_id": street.street_id,
                    "traveled": street.traveled,
                    "color": "#00ff00" if street.traveled else "#ff0000"
                }
            }
            for street in self.streets_gdf.itertuples() if waco_limits is None or street.geometry.intersects(waco_limits)
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_untraveled_streets(self, waco_boundary='city_limits'):
        logger.info("Generating untraveled streets...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(
                f"static/{waco_boundary}.geojson").geometry.unary_union

        untraveled_streets = self.streets_gdf[~self.streets_gdf['street_id'].isin(
            self.traveled_streets)]
        if waco_limits is not None:
            untraveled_streets = untraveled_streets[untraveled_streets.intersects(
                waco_limits)]

        return untraveled_streets

    def analyze_coverage(self, waco_boundary='city_limits'):
        logger.info("Analyzing street coverage...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(
                f"static/{waco_boundary}.geojson").geometry.unary_union

        total_streets = self.streets_gdf.intersects(
            waco_limits).sum() if waco_limits else len(self.streets_gdf)
        traveled_streets = len(self.traveled_streets)
        coverage_percentage = (
            traveled_streets / total_streets) * 100 if total_streets > 0 else 0

        return {"total_streets": total_streets, "traveled_streets": traveled_streets, "coverage_percentage": coverage_percentage}

    def get_street_network(self, waco_boundary='city_limits'):
        logger.info("Retrieving street network...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(
                f"static/{waco_boundary}.geojson").geometry.unary_union

        street_network = self.streets_gdf.copy()
        if waco_limits:
            street_network = street_network[street_network.intersects(
                waco_limits)]

        street_network['traveled'] = street_network['street_id'].isin(
            self.traveled_streets)

        return street_network
