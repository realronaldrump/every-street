import logging
import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, Point, box
from rtree import index
from tqdm import tqdm
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.001):
        logging.info("Initializing WacoStreetsAnalyzer...")
        self.streets_gdf = gpd.read_file(waco_streets_file)
        self.snap_distance = snap_distance
        self.traveled_segments = set()

        # Process streets into segments
        self._process_streets_into_segments()

        # Create spatial index using R-tree
        self._create_spatial_index()

        logging.info(f"Processed {len(self.segments_df)} segments from {len(self.streets_gdf)} streets.")

        # Calculate the bounding box of Waco streets
        self.waco_bbox = self.streets_gdf.total_bounds
        self.waco_box = box(*self.waco_bbox)

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
        self.segments_df = pd.DataFrame(segments)

    def _create_spatial_index(self):
        self.spatial_index = index.Index()
        for idx, segment in self.segments_df.iterrows():
            self.spatial_index.insert(idx, segment['geometry'].bounds)

    def _snap_point_to_segment(self, point):
        if not self.waco_box.contains(point):
            return None

        nearby_indices = list(self.spatial_index.intersection(point.buffer(self.snap_distance).bounds))
        if not nearby_indices:
            return None

        nearby_segments = self.segments_df.iloc[nearby_indices]
        distances = nearby_segments['geometry'].apply(lambda geom: geom.distance(point))
        nearest_index = distances.idxmin()
        return self.segments_df.loc[nearest_index, 'geometry']

    def _process_route(self, route):
        traveled_segments = set()
        if isinstance(route, dict) and 'geometry' in route and 'coordinates' in route['geometry']:
            for coord in route['geometry']['coordinates']:
                point = Point(coord[0], coord[1])
                if not self.waco_box.contains(point):
                    continue
                snapped_segment = self._snap_point_to_segment(point)
                if snapped_segment is not None:
                    matching_segments = self.segments_df[self.segments_df['geometry'] == snapped_segment]
                    if not matching_segments.empty:
                        traveled_segments.add(matching_segments.index[0])
        return traveled_segments

    def update_progress(self, new_routes, progress_callback=None):
        logging.info(f"Updating progress with {len(new_routes)} new routes...")

        with ProcessPoolExecutor(max_workers=multiprocessing.cpu_count() - 1) as executor:
            futures = [executor.submit(self._process_route, route) for route in new_routes]
            for i, future in enumerate(as_completed(futures)):
                self.traveled_segments.update(future.result())
                if progress_callback:
                    progress_callback(i + 1, len(new_routes))

        logging.info("Progress update complete.")

    def calculate_progress(self):
        logging.info("Calculating progress...")
        total_segments = len(self.segments_df)
        traveled_segments = len(self.traveled_segments)
        progress = (traveled_segments / total_segments) * 100
        logging.info(f"Progress: {progress:.2f}%")
        return progress

    def get_progress_geojson(self, waco_boundary='city_limits'):
        logging.info("Generating progress GeoJSON...")

        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry[0]

        features = []
        for idx, segment in self.segments_df.iterrows():
            if waco_limits is not None and not segment['geometry'].intersects(waco_limits):
                continue

            feature = {
                "type": "Feature",
                "geometry": segment['geometry'].__geo_interface__,
                "properties": {
                    "segment_id": segment['segment_id'],
                    "street_id": segment['street_id'],
                    "name": segment['name'],
                    "traveled": idx in self.traveled_segments
                }
            }
            features.append(feature)
        logging.info("Progress GeoJSON generated.")
        return {"type": "FeatureCollection", "features": features}

if __name__ == "__main__":
    multiprocessing.freeze_support()