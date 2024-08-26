import logging
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, box
from rtree import index
from multiprocessing import Pool, cpu_count
from functools import partial

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.001):
        logger.info("Initializing WacoStreetsAnalyzer...")
        self.streets_gdf = gpd.read_file(waco_streets_file)
        self.snap_distance = snap_distance
        self.traveled_segments = set()

        # Process streets into segments
        self._process_streets_into_segments()

        # Create spatial index using R-tree
        self._create_spatial_index()

        logger.info(f"Processed {len(self.segments_df)} segments from {len(self.streets_gdf)} streets.")

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
        if nearby_segments.empty:
            return None

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
        logger.info(f"Updating progress with {len(new_routes)} new routes...")

        chunk_size = max(1, len(new_routes) // (cpu_count() - 1))
        chunks = [new_routes[i:i + chunk_size] for i in range(0, len(new_routes), chunk_size)]

        process_chunk_partial = partial(self._process_chunk, total_routes=len(new_routes), progress_callback=progress_callback)
        results = self.pool.map(process_chunk_partial, enumerate(chunks))

        for result in results:
            self.traveled_segments.update(result)

        progress = self.calculate_progress()
        logger.info(f"Progress update complete. Overall progress: {progress:.2f}%")

    def _process_chunk(self, chunk_info, total_routes, progress_callback=None):
        chunk_index, chunk = chunk_info
        traveled_segments = set()
        
        for i, route in enumerate(chunk):
            traveled_segments.update(self._process_route(route))
            if progress_callback and (i + 1) % 10 == 0:  # Log progress every 10 routes
                progress = ((chunk_index * len(chunk) + i + 1) / total_routes) * 100
                progress_callback(chunk_index * len(chunk) + i + 1, total_routes)
        
        return traveled_segments

    def calculate_progress(self):
        """Calculates the overall progress."""
        logging.info("Calculating progress...")
        total_segments = len(self.segments_df)
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
        for segment in self.segments_df.itertuples():
            if waco_limits is not None and not segment.geometry.intersects(waco_limits):
                continue 

            feature = {
                "type": "Feature",
                "geometry": segment.geometry.__geo_interface__,
                "properties": {
                    "segment_id": segment.segment_id,
                    "street_id": segment.street_id,
                    "name": segment.name,
                    "traveled": segment.segment_id in self.traveled_segments
                }
            }
            features.append(feature)
        logging.info("Progress GeoJSON generated.")
        return {"type": "FeatureCollection", "features": features}