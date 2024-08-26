import logging
import geopandas as gpd
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points
from tqdm import tqdm  # For progress bar
from rtree import index

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.001):
        logging.info("Initializing WacoStreetsAnalyzer...")
        self.streets_gdf = gpd.read_file(waco_streets_file)
        self.snap_distance = snap_distance
        self.traveled_segments = set()

        # Process streets into segments
        self._process_streets_into_segments()

        # Create spatial index for segments
        self.segments_index = index.Index()
        for idx, segment in self.segments_gdf.iterrows():
            self.segments_index.insert(idx, segment.geometry.bounds)

        logging.info(f"Processed {len(self.segments_gdf)} segments from {len(self.streets_gdf)} streets.")

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
        self.segments_gdf = gpd.GeoDataFrame(segments, crs=self.streets_gdf.crs)

    def _snap_point_to_segment(self, point):
        """Snaps a point to the nearest segment within snap_distance."""
        if not self.waco_box.contains(point):
            return None

        # Use the spatial index to efficiently find nearby segments
        possible_matches_index = list(self.segments_index.intersection(point.buffer(self.snap_distance).bounds))
        if not possible_matches_index:
            return None

        possible_matches = self.segments_gdf.iloc[possible_matches_index]
        nearest_segment = nearest_points(point, possible_matches.unary_union)[1]
        return nearest_segment

    def update_progress(self, new_routes, progress_callback=None):
        """Updates progress by snapping route points to segments."""
        logging.info(f"Updating progress with {len(new_routes)} new routes...")

        total_coords = sum(len(route['geometry']['coordinates'])
                          for route in new_routes
                          if isinstance(route, dict) and 'geometry' in route and 'coordinates' in route['geometry'])

        processed_coords = 0
        for route in tqdm(new_routes, desc="Processing Routes", unit="route"):  # Progress bar for routes
            if isinstance(route, dict) and 'geometry' in route and 'coordinates' in route['geometry']:
                for coord in tqdm(route['geometry']['coordinates'], desc="Snapping Points", unit="point",
                                 leave=False):  # Progress bar for points within a route
                    point = Point(coord[0], coord[1])
                    if not self.waco_box.contains(point):
                        continue
                    snapped_point = self._snap_point_to_segment(point)
                    if snapped_point:
                        # Correctly use snapped_point.bounds for intersection
                        possible_matches_index = list(self.segments_index.intersection(snapped_point.bounds))
                        if possible_matches_index:
                            for idx in possible_matches_index:
                                segment = self.segments_gdf.iloc[idx]
                                if segment.geometry.distance(snapped_point) < 1e-8:
                                    self.traveled_segments.add(segment['segment_id'])
                                    break
                    processed_coords += 1
                    if progress_callback:
                        progress_callback(processed_coords, total_coords)

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
                    "traveled": segment.segment_id in self.traveled_segments
                }
            }
            features.append(feature)
        logging.info("Progress GeoJSON generated.")
        return {"type": "FeatureCollection", "features": features}