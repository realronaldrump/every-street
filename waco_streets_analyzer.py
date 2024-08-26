import logging
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree
from tqdm import tqdm
import multiprocessing
from multiprocessing import Manager, Pool

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.001):
        logging.info("Initializing WacoStreetsAnalyzer...")
        self.streets_gdf = gpd.read_file(waco_streets_file)
        self.snap_distance = snap_distance
        self._manager = None
        self._traveled_segments = None

        # Process streets into segments
        self._process_streets_into_segments()

        # Create spatial index using STRtree
        self.streets_tree = STRtree(self.segments_gdf.geometry)

        logging.info(f"Processed {len(self.segments_gdf)} segments from {len(self.streets_gdf)} streets.")

        # Calculate the bounding box of Waco streets
        self.waco_bbox = self.streets_gdf.total_bounds
        self.waco_box = box(*self.waco_bbox)

    @property
    def manager(self):
        if self._manager is None:
            self._manager = Manager()
        return self._manager

    @property
    def traveled_segments(self):
        if self._traveled_segments is None:
            self._traveled_segments = self.manager.set()
        return self._traveled_segments

    def _process_streets_into_segments(self):
        segments = []
        for idx, street in self.streets_gdf.iterrows():
            coords = list(street.geometry.coords)
            for i in range(len(coords) - 1):
                segment = LineString([coords[i], coords[i + 1]])
                segments.append({
                    'geometry': segment,
                    'street_id': street['street_id'],
                    'name': street['name'],
                    'segment_id': f"{street['street_id']}_{i}"
                })
        self.segments_gdf = gpd.GeoDataFrame(segments, crs=self.streets_gdf.crs)

    def _snap_point_to_segment(self, point):
        """Snaps a point to the nearest segment within snap_distance using STRtree."""
        if not self.waco_box.contains(point):
            return None

        nearby_streets = self.streets_tree.query(point.buffer(self.snap_distance))
        if len(nearby_streets) == 0:
            return None

        # Filter to ensure all items are geometries
        nearby_streets = [street for street in nearby_streets if isinstance(street, LineString)]
        if not nearby_streets:
            logging.warning(f"No valid streets found near point {point}.")
            return None

        # Calculate distances only for valid geometries
        distances = np.array([street.distance(point) for street in nearby_streets])
        nearest_index = np.argmin(distances)
        return nearby_streets[nearest_index]

    def _process_route(self, route):
        """Process a single route and return traveled segments."""
        traveled_segments = set()
        if isinstance(route, dict) and 'geometry' in route and 'coordinates' in route['geometry']:
            for coord in route['geometry']['coordinates']:
                point = Point(coord[0], coord[1])
                if not self.waco_box.contains(point):
                    continue
                snapped_segment = self._snap_point_to_segment(point)
                if snapped_segment is not None:
                    segment_idx = self.segments_gdf.index[self.segments_gdf.geometry == snapped_segment]
                    if not segment_idx.empty:
                        traveled_segments.add(segment_idx[0])
                    else:
                        logging.warning(f"Segment not found for snapped segment: {snapped_segment}")
        return traveled_segments

    def update_progress(self, new_routes, progress_callback=None):
        """Updates progress by snapping route points to segments using multiprocessing."""
        logging.info(f"Updating progress with {len(new_routes)} new routes...")

        with Pool() as pool:
            results = list(tqdm(pool.imap(self._process_route, new_routes), 
                                total=len(new_routes), desc="Processing Routes"))

        for traveled_segments in results:
            self.traveled_segments.update(traveled_segments)

        logging.info("Progress update complete.")

    def calculate_progress(self):
        """Calculates the overall progress."""
        logging.info("Calculating progress...")
        total_segments = len(self.segments_gdf)
        traveled_segments = len(self.traveled_segments)
        progress = (traveled_segments / total_segments) * 100
        logging.info(f"Progress: {progress:.2f}%")
        return progress

    def get_progress_geojson(self, waco_boundary='city_limits'):
        """Generates GeoJSON for visualizing progress."""
        logging.info("Generating progress GeoJSON...")

        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry[0] 

        features = []
        for segment in self.segments_gdf.itertuples():
            if waco_limits is not None and not segment.geometry.intersects(waco_limits):
                continue 

            feature = {
                "type": "Feature",
                "geometry": segment.geometry.__geo_interface__,
                "properties": {
                    "segment_id": segment.segment_id,
                    "street_id": segment.street_id,
                    "name": segment.name,
                    "traveled": segment.Index in self.traveled_segments
                }
            }
            features.append(feature)
        logging.info("Progress GeoJSON generated.")
        return {"type": "FeatureCollection", "features": features}

if __name__ == "__main__":
    multiprocessing.freeze_support()