import logging
import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

# Configure logging 
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.001):
        logging.info("Initializing WacoStreetsAnalyzer...") 
        self.streets_gdf = gpd.read_file(waco_streets_file)
        self.snap_distance = snap_distance
        self.streets_index = self.streets_gdf.sindex 
        self.traveled_segments = set()

        self.streets_gdf['street_id'] = self.streets_gdf.apply(
            lambda row: row.get('osm_id', row.get('id', f"Street_{row.name}")), axis=1
        )
        logging.info(f"Loaded {len(self.streets_gdf)} streets from GeoJSON.")

    def _snap_point_to_street(self, point):
        """Snaps a point to the nearest street within snap_distance."""
        logging.debug(f"Snapping point {point} to nearest street...")
        possible_matches_index = list(self.streets_index.intersection(point.buffer(self.snap_distance).bounds))
        possible_matches = self.streets_gdf.iloc[possible_matches_index]

        if len(possible_matches) == 0: 
            logging.debug(f"No streets found near point {point} within snap distance.")
            return point

        nearest_street = nearest_points(point, possible_matches.unary_union)[1]
        logging.debug(f"Point {point} snapped to {nearest_street}.")
        return nearest_street

    def update_progress(self, new_routes):
        """Updates progress by snapping route points to streets."""
        logging.info(f"Updating progress with {len(new_routes)} new routes...")
        for i, route in enumerate(new_routes):
            logging.debug(f"Processing route {i+1}/{len(new_routes)}...")
            if isinstance(route, dict) and 'geometry' in route and 'coordinates' in route['geometry']:
                coordinates = route['geometry']['coordinates']
                for coord in coordinates:
                    point = Point(coord[0], coord[1])
                    snapped_point = self._snap_point_to_street(point)

                    for idx, street in self.streets_gdf.iterrows():
                        if street.geometry.distance(snapped_point) < 1e-8:
                            self.traveled_segments.add(street['street_id'])
                            logging.debug(f"Marked street segment {street['street_id']} as traveled.")
                            break
        logging.info("Progress update complete.")

    def calculate_progress(self):
        """Calculates the overall progress."""
        logging.info("Calculating progress...")
        total_streets = len(self.streets_gdf)
        traveled_streets = len(self.traveled_segments)
        progress = (traveled_streets / total_streets) * 100
        logging.info(f"Progress: {progress:.2f}%") 
        return progress

    def get_progress_geojson(self, waco_boundary='city_limits'):
        """Generates GeoJSON for visualizing progress, optionally filtered by Waco boundary."""
        logging.info("Generating progress GeoJSON...")

        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry[0]  # Load Waco boundary

        features = []
        for street in self.streets_gdf.itertuples():
            # Filter streets based on Waco boundary if provided
            if waco_limits is not None and not street.geometry.intersects(waco_limits):
                continue  # Skip this street if it's not within the boundary

            feature = {
                "type": "Feature",
                "geometry": street.geometry.__geo_interface__,
                "properties": {
                    "street_id": street.street_id,
                    "traveled": street.street_id in self.traveled_segments
                }
            }
            features.append(feature)
        logging.info("Progress GeoJSON generated.")
        return {"type": "FeatureCollection", "features": features}