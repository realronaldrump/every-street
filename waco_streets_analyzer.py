import logging
import geopandas as gpd
from shapely.geometry import LineString
from rtree import index

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.0001):
        logger.info("Initializing WacoStreetsAnalyzer...")
        try:
            self.streets_gdf = gpd.read_file(waco_streets_file)
            self.streets_gdf['street_id'] = self.streets_gdf['street_id'].astype(str)
            self.snap_distance = snap_distance
            self.traveled_streets = set()
            self.spatial_index = index.Index()
            self._create_spatial_index()

            logger.info(f"Processed {len(self.streets_gdf)} streets.")
        except Exception as e:
            logger.error(f"Error initializing WacoStreetsAnalyzer: {str(e)}")
            raise

    def _create_spatial_index(self):
        for idx, street in self.streets_gdf.iterrows():
            self.spatial_index.insert(idx, street.geometry.bounds)

    async def update_progress(self, new_routes):
        logger.info(f"Updating progress with {len(new_routes)} new routes...")
        new_streets = await self._process_routes(new_routes)
        self.traveled_streets.update(new_streets)
        logger.info(f"Progress update complete. Overall progress: {self.calculate_progress():.2f}%")

    async def _process_routes(self, routes):
        new_streets = set()
        for route in routes:
            route_line = LineString(route['geometry']['coordinates'])
            possible_matches_idx = list(self.spatial_index.intersection(route_line.bounds))
            possible_matches = self.streets_gdf.iloc[possible_matches_idx]
            precise_matches = possible_matches[possible_matches.intersects(route_line.buffer(self.snap_distance))]
            new_streets.update(precise_matches['street_id'].tolist())
        return new_streets

    def reset_progress(self):
        logger.info("Resetting progress...")
        self.traveled_streets.clear()

    def calculate_progress(self):
        logger.info("Calculating progress...")
        total_streets = len(self.streets_gdf)
        return (len(self.traveled_streets) / total_streets) * 100 if total_streets > 0 else 0

    def get_progress_geojson(self, waco_boundary='city_limits'):
        logger.info("Generating progress GeoJSON...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        self.streets_gdf['traveled'] = self.streets_gdf['street_id'].isin(self.traveled_streets)

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
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        untraveled_streets = self.streets_gdf[~self.streets_gdf['street_id'].isin(self.traveled_streets)]
        if waco_limits is not None:
            untraveled_streets = untraveled_streets[untraveled_streets.intersects(waco_limits)]

        return untraveled_streets

    def analyze_coverage(self, waco_boundary='city_limits'):
        logger.info("Analyzing street coverage...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        total_streets = self.streets_gdf.intersects(waco_limits).sum() if waco_limits else len(self.streets_gdf)
        traveled_streets = len(self.traveled_streets)
        coverage_percentage = (traveled_streets / total_streets) * 100 if total_streets > 0 else 0

        return {"total_streets": total_streets, "traveled_streets": traveled_streets, "coverage_percentage": coverage_percentage}

    def get_street_network(self, waco_boundary='city_limits'):
        logger.info("Retrieving street network...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        street_network = self.streets_gdf.copy()
        if waco_limits:
            street_network = street_network[street_network.intersects(waco_limits)]

        street_network['traveled'] = street_network['street_id'].isin(self.traveled_streets)

        return street_network