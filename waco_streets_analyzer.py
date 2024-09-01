import logging
import os
import pickle

import geopandas as gpd
from shapely.geometry import LineString

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class WacoStreetsAnalyzer:
    def __init__(self, streets_geojson_path):
        logging.info("Initializing WacoStreetsAnalyzer...")
        cache_file = 'waco_streets_cache.pkl'

        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as f:
                cache_data = pickle.load(f)
            self.streets_gdf = cache_data['streets_gdf']
            self.traveled_streets = cache_data['traveled_streets']
            logging.info("Loaded data from cache.")
        else:
            self.streets_gdf = gpd.read_file(streets_geojson_path)
            self.streets_gdf['street_id'] = self.streets_gdf.index
            self.streets_gdf = self.streets_gdf.to_crs(epsg=4326)
            self.traveled_streets = set()

            # Create spatial index using GeoPandas
            self.streets_gdf = self.streets_gdf.set_index('street_id')
            self.streets_gdf = self.streets_gdf.sort_index()

            with open(cache_file, 'wb') as f:
                pickle.dump({
                    'streets_gdf': self.streets_gdf,
                    'traveled_streets': self.traveled_streets
                }, f)
            logging.info("Processed and cached street data.")

        self.snap_distance = 0.00001  # Increased for better performance
        self.sindex = self.streets_gdf.sindex
        logging.info(f"Processed {len(self.streets_gdf)} streets.")

    async def update_progress(self, routes):
        logging.info(f"Updating progress with {len(routes)} new routes...")
        for route_index, route in enumerate(routes):
            if isinstance(route, dict) and 'geometry' in route:
                geometry = route['geometry']
            else:
                geometry = route

            if geometry['type'] == 'LineString':
                coords = geometry['coordinates']
                line = LineString(coords)
                logging.info(
                    f"Processing route {route_index}: {line.wkt[:100]}...")

                # Use spatial index for efficient querying
                possible_matches_index = list(
                    self.sindex.intersection(line.bounds))
                possible_matches = self.streets_gdf.iloc[possible_matches_index]

                # Vectorized operation
                mask = possible_matches.intersects(
                    line.buffer(self.snap_distance))
                intersected_streets = possible_matches[mask]

                self.traveled_streets.update(intersected_streets.index)

                if len(intersected_streets) == 0:
                    logging.warning(
                        f"Route {route_index} did not intersect with any streets")
                else:
                    logging.info(
                        f"Route {route_index} intersected with {len(intersected_streets)} streets")

        logging.info(f"Total traveled streets: {len(self.traveled_streets)}")
        logging.info("Progress update completed.")

    def calculate_progress(self):
        logger.info("Calculating progress...")
        total_streets = len(self.streets_gdf)
        traveled_streets = len(self.traveled_streets)

        street_count_percentage = (
            traveled_streets / total_streets) * 100 if total_streets > 0 else 0

        return {
            'street_count_percentage': street_count_percentage,
            # Using street count as length percentage
            'length_percentage': street_count_percentage,
            'total_streets': total_streets,
            'traveled_streets': traveled_streets
        }

    def reset_progress(self):
        logger.info("Resetting progress...")
        self.traveled_streets.clear()

    def get_progress_geojson(self, waco_boundary='city_limits'):
        logger.info("Generating progress GeoJSON...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(
                f"static/{waco_boundary}.geojson").geometry.unary_union

        # Vectorized operation
        self.streets_gdf['traveled'] = self.streets_gdf.index.isin(
            self.traveled_streets)

        if waco_limits is not None:
            filtered_streets = self.streets_gdf[self.streets_gdf.intersects(
                waco_limits)]
        else:
            filtered_streets = self.streets_gdf

        features = filtered_streets.apply(lambda row: {
            "type": "Feature",
            "geometry": row.geometry.__geo_interface__,
            "properties": {
                "street_id": row.name,
                "traveled": row.traveled,
                "color": "#00ff00" if row.traveled else "#ff0000"
            }
        }, axis=1).tolist()

        return {"type": "FeatureCollection", "features": features}

    def get_untraveled_streets(self, waco_boundary='city_limits'):
        logger.info("Generating untraveled streets...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(
                f"static/{waco_boundary}.geojson").geometry.unary_union

        untraveled_streets = self.streets_gdf[~self.streets_gdf.index.isin(
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

        if waco_limits:
            total_streets = self.streets_gdf.intersects(waco_limits).sum()
        else:
            total_streets = len(self.streets_gdf)

        traveled_streets = len(self.traveled_streets)
        coverage_percentage = (
            traveled_streets / total_streets) * 100 if total_streets > 0 else 0

        return {
            "total_streets": total_streets,
            "traveled_streets": traveled_streets,
            "coverage_percentage": coverage_percentage
        }

    def get_street_network(self, waco_boundary='city_limits'):
        logger.info("Retrieving street network...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(
                f"static/{waco_boundary}.geojson").geometry.unary_union

        street_network = self.streets_gdf.copy()
        if waco_limits is not None:
            street_network = street_network[street_network.intersects(
                waco_limits)]

        street_network['traveled'] = street_network.index.isin(
            self.traveled_streets)

        return street_network
