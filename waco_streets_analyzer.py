import logging

import geopandas as gpd
from rtree import index
from shapely.geometry import LineString

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class WacoStreetsAnalyzer:
    def __init__(self, streets_geojson_path):
        logging.info("Initializing WacoStreetsAnalyzer...")
        self.streets_gdf = gpd.read_file(streets_geojson_path)
        self.streets_gdf['street_id'] = self.streets_gdf.index
        self.streets_gdf = self.streets_gdf.to_crs(epsg=4326)

        self.traveled_streets = set()
        self.snap_distance = 0.00000001
        self.spatial_index = index.Index()
        for idx, geometry in enumerate(self.streets_gdf.geometry):
            self.spatial_index.insert(idx, geometry.bounds)
        logging.info(f"Processed {len(self.streets_gdf)} streets.")

    async def update_progress(self, routes):
        logging.info(f"Updating progress with {len(routes)} new routes...")
        for route_index, route in enumerate(routes):
            if route['geometry']['type'] == 'LineString':
                coords = route['geometry']['coordinates']
                line = LineString(coords)
                logging.info(
                    f"Processing route {route_index}: {line.wkt[:100]}...")
                intersected_streets = 0
                for idx, street in self.streets_gdf.iterrows():
                    try:
                        if line.intersects(street.geometry.buffer(self.snap_distance)):
                            intersected_streets += 1
                            self.traveled_streets.add(street['street_id'])
                    except Exception as e:
                        logging.error(
                            f"Error processing street {idx}: {str(e)}")

                if intersected_streets == 0:
                    logging.warning(
                        f"Route {route_index} did not intersect with any streets")
                else:
                    logging.info(
                        f"Route {route_index} intersected with {intersected_streets} streets")

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
