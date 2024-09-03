import logging
import os
import pickle
import asyncio
import geopandas as gpd
from shapely.geometry import LineString

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WacoStreetsAnalyzer:
    def __init__(self, streets_geojson_path):
        logging.info("Initializing WacoStreetsAnalyzer...")
        self.streets_geojson_path = streets_geojson_path
        self.cache_file = 'waco_streets_cache.pkl'
        self.streets_gdf = None
        self.traveled_streets = set()
        self.snap_distance = 0.00000001
        self.sindex = None
        self.load_data()

    def load_data(self):
        if os.path.exists(self.cache_file):
            self._load_from_cache()
        else:
            self._process_and_cache_data()

    def _load_from_cache(self):
        with open(self.cache_file, 'rb') as f:
            cache_data = pickle.load(f)
        self.streets_gdf = cache_data['streets_gdf']
        self.traveled_streets = cache_data['traveled_streets']
        self.sindex = self.streets_gdf.sindex
        logging.info("Loaded data from cache.")

    def _process_and_cache_data(self):
        self.streets_gdf = gpd.read_file(self.streets_geojson_path)
        self.streets_gdf['street_id'] = self.streets_gdf.index
        self.streets_gdf = self.streets_gdf.to_crs(epsg=4326)
        self.streets_gdf = self.streets_gdf.set_index('street_id').sort_index()
        self.sindex = self.streets_gdf.sindex
        self._save_to_cache()
        logging.info("Processed and cached street data.")

    def _save_to_cache(self):
        with open(self.cache_file, 'wb') as f:
            pickle.dump({
                'streets_gdf': self.streets_gdf,
                'traveled_streets': self.traveled_streets
            }, f)

    async def update_progress(self, routes):  # Make the function asynchronous
        logging.info(f"Updating progress with {len(routes)} new routes...")
        if not routes:
            logging.warning("No routes provided for update_progress")
            return

        # Convert list of dictionaries to GeoDataFrame
        gdf = gpd.GeoDataFrame.from_features(routes)
        gdf.set_crs(epsg=4326, inplace=True)

        for _, route in gdf.iterrows():
            if isinstance(route.geometry, LineString):
                line = route.geometry
                logging.info(f"Processing route: {line.wkt[:100]}...")

                possible_matches_index = list(self.sindex.intersection(line.bounds))
                possible_matches = self.streets_gdf.iloc[possible_matches_index]

                mask = possible_matches.intersects(line.buffer(self.snap_distance))
                intersected_streets = possible_matches[mask]

                self.traveled_streets.update(intersected_streets.index)

                if len(intersected_streets) == 0:
                    logging.warning(f"Route did not intersect with any streets")
                else:
                    logging.info(f"Route intersected with {len(intersected_streets)} streets")

            await asyncio.sleep(0)  # Yield control to the event loop

        self._save_to_cache()
        logging.info(f"Total traveled streets: {len(self.traveled_streets)}")
        logging.info("Progress update completed.")

    def calculate_progress(self):
        logger.info("Calculating progress...")
        total_streets = len(self.streets_gdf)
        traveled_streets = len(self.traveled_streets)

        coverage_percentage = (traveled_streets / total_streets) * 100 if total_streets > 0 else 0

        return {
            'coverage_percentage': coverage_percentage,
            'total_streets': total_streets,
            'traveled_streets': traveled_streets
        }

    def reset_progress(self):
        logger.info("Resetting progress...")
        self.traveled_streets.clear()
        self._save_to_cache()

    def get_progress_geojson(self, waco_boundary='city_limits'):
        logger.info("Generating progress GeoJSON...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        self.streets_gdf['traveled'] = self.streets_gdf.index.isin(self.traveled_streets)

        if waco_limits is not None:
            filtered_streets = self.streets_gdf[self.streets_gdf.intersects(waco_limits)]
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
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        untraveled_streets = self.streets_gdf[~self.streets_gdf.index.isin(self.traveled_streets)]
        if waco_limits is not None:
            untraveled_streets = untraveled_streets[untraveled_streets.intersects(waco_limits)]

        return untraveled_streets

    def get_street_network(self, waco_boundary='city_limits'):
        logger.info("Retrieving street network...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        street_network = self.streets_gdf.copy()
        if waco_limits is not None:
            street_network = street_network[street_network.intersects(waco_limits)]

        street_network['traveled'] = street_network.index.isin(self.traveled_streets)

        return street_network
    
    def get_all_streets(self):
        logger.info(f"Retrieving all streets. Total streets: {len(self.streets_gdf)}")
        return self.streets_gdf