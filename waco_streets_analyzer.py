import logging
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, box
from rtree import index
from multiprocessing import Pool, cpu_count
from functools import partial
import numpy as np
from shapely.ops import unary_union
import json
import asyncio
from logging_config import setup_logging

setup_logging()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def process_chunk(chunk_info, analyzer, progress_callback, new_routes):
    chunk_index, chunk = chunk_info
    traveled_segments = set()

    for i, route in enumerate(chunk):
        traveled_segments.update(analyzer._process_route(route))
        if progress_callback and (i + 1) % 10 == 0:  # Log progress every 10 routes
            progress = ((chunk_index * len(chunk) + i + 1) / len(new_routes)) * 100
            progress_callback(chunk_index * len(chunk) + i + 1, len(new_routes))

    return traveled_segments

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.0001):
        logger.info("Initializing WacoStreetsAnalyzer...")
        try:
            self.streets_gdf = gpd.read_file(waco_streets_file)
            self.snap_distance = snap_distance
            self.traveled_segments = set()
            self.segments_df = None
            self.spatial_index = None
            self.waco_bbox = None
            self.waco_box = None

            self._process_streets_into_segments()
            self._create_spatial_index()

            logger.info(f"Processed {len(self.segments_df)} segments from {len(self.streets_gdf)} streets.")
        except Exception as e:
            logger.error(f"Error initializing WacoStreetsAnalyzer: {str(e)}")
            raise

    def _process_streets_into_segments(self):
        # Project to a local UTM zone for accurate length calculations
        utm_crs = self._get_utm_crs(self.streets_gdf)
        self.streets_gdf = self.streets_gdf.to_crs(utm_crs)

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
        self.segments_df = gpd.GeoDataFrame(segments, crs=utm_crs)
        self.segments_df['length'] = self.segments_df.geometry.length

        # Project back to WGS84 for compatibility with other data
        self.segments_df = self.segments_df.to_crs('EPSG:4326')

        self.waco_bbox = self.streets_gdf.total_bounds
        self.waco_box = box(*self.waco_bbox)

    def _get_utm_crs(self, gdf):
        # Determine the appropriate UTM zone based on the centroid of the data
        bounds = gdf.total_bounds
        lon = (bounds[0] + bounds[2]) / 2  # average of min and max longitude
        lat = (bounds[1] + bounds[3]) / 2  # average of min and max latitude
        utm_zone = int((lon + 180) / 6) + 1
        hemisphere = 'north' if lat >= 0 else 'south'
        return f'+proj=utm +zone={utm_zone} +{hemisphere} +datum=WGS84 +units=m +no_defs'

    def _create_spatial_index(self):
        self.spatial_index = index.Index()
        for idx, segment in self.segments_df.iterrows():
            self.spatial_index.insert(idx, segment.geometry.bounds)

    def _snap_point_to_segment(self, point):
        if not self.waco_box.contains(point):
            return None

        nearby_indices = list(self.spatial_index.intersection(point.buffer(self.snap_distance).bounds))
        if not nearby_indices:
            return None

        nearby_segments = self.segments_df.iloc[nearby_indices]
        if nearby_segments.empty:
            return None

        distances = nearby_segments.geometry.distance(point)
        nearest_index = distances.idxmin()
        return self.segments_df.loc[nearest_index, 'geometry']

    def _process_route(self, route):
        traveled_segments = set()
        if isinstance(route, dict) and 'geometry' in route and 'coordinates' in route['geometry']:
            coords = route['geometry']['coordinates']
            for i in range(len(coords) - 1):
                start_point = Point(coords[i][0], coords[i][1])
                end_point = Point(coords[i+1][0], coords[i+1][1])
                if self.waco_box.contains(start_point) or self.waco_box.contains(end_point):
                    line = LineString([start_point, end_point])
                    intersecting_segments = self.segments_df[self.segments_df.intersects(line)]
                    traveled_segments.update(intersecting_segments.index)
        return traveled_segments

    async def update_progress(self, new_routes, progress_callback=None):
        logger.info(f"Updating progress with {len(new_routes)} new routes...")

        chunk_size = max(1, len(new_routes) // (cpu_count() - 1))
        chunks = [(i, new_routes[i:i + chunk_size]) for i in range(0, len(new_routes), chunk_size)]

        loop = asyncio.get_running_loop()
        try:
            with Pool(processes=cpu_count() - 1) as pool:
                results = await loop.run_in_executor(None, pool.map, partial(process_chunk, analyzer=self, progress_callback=progress_callback, new_routes=new_routes), chunks)

            for result in results:
                self.traveled_segments.update(result)

            progress = self.calculate_progress()
            logger.info(f"Progress update complete. Overall progress: {progress:.2f}%")
            return progress
        except Exception as e:
            logger.error(f"Error during progress update: {e}")
            raise

    def reset_progress(self):
        """Resets the progress by clearing all traveled segments."""
        logger.info("Resetting progress...")
        self.traveled_segments.clear()
        logger.info("Progress has been reset.")

    def calculate_progress(self):
        """Calculates the overall progress."""
        logger.info("Calculating progress...")
        total_length = self.segments_df['length'].sum()
        traveled_length = self.segments_df.loc[list(self.traveled_segments), 'length'].sum()
        logger.info(f"Total length: {total_length}, Traveled length: {traveled_length}")
        if total_length == 0:
            logger.warning("Total length is 0, cannot calculate progress")
            return 0
        progress = (traveled_length / total_length) * 100
        logger.info(f"Progress: {progress:.2f}%")
        return progress

    def get_progress_geojson(self, waco_boundary='city_limits'):
        """Generates GeoJSON for visualizing progress."""
        logger.info("Generating progress GeoJSON...")

        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

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
                    "traveled": segment.Index in self.traveled_segments
                }
            }
            features.append(feature)
        logger.info("Progress GeoJSON generated.")
        return {"type": "FeatureCollection", "features": features}

    def get_untraveled_streets(self, waco_boundary='city_limits'):
        """Returns a GeoDataFrame of untraveled streets."""
        logger.info("Generating untraveled streets...")

        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        untraveled_segments = self.segments_df[~self.segments_df.index.isin(self.traveled_segments)]

        if waco_limits is not None:
            untraveled_segments = untraveled_segments[untraveled_segments.intersects(waco_limits)]

        untraveled_streets = untraveled_segments.dissolve(by='street_id')

        logger.info(f"Found {len(untraveled_streets)} untraveled streets.")
        return untraveled_streets

    def analyze_coverage(self, waco_boundary='city_limits'):
        logger.info("Analyzing street coverage...")
        logger.debug(f"Total traveled segments: {len(self.traveled_segments)}")
        logger.debug(f"Total streets: {len(self.streets_gdf)}")

        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        total_streets = len(self.streets_gdf)
        traveled_streets = len(set(self.segments_df.loc[list(self.traveled_segments), 'street_id']))

        if waco_limits is not None:
            total_streets = self.streets_gdf[self.streets_gdf.intersects(waco_limits)].shape[0]
            traveled_streets = len(set(self.segments_df[
                (self.segments_df.index.isin(self.traveled_segments)) & 
                (self.segments_df.intersects(waco_limits))
            ]['street_id']))

        coverage_percentage = (traveled_streets / total_streets) * 100 if total_streets > 0 else 0

        logger.debug(f"Traveled streets: {traveled_streets}")
        logger.debug(f"Coverage percentage: {coverage_percentage}")

        return {
            "total_streets": total_streets,
            "traveled_streets": traveled_streets,
            "coverage_percentage": coverage_percentage
        }

    def get_street_network(self, waco_boundary='city_limits'):
        """Returns the entire street network as a GeoDataFrame."""
        logger.info("Retrieving street network...")

        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        street_network = self.streets_gdf.copy()

        if waco_limits is not None:
            street_network = street_network[street_network.intersects(waco_limits)]

        street_network['traveled'] = street_network['street_id'].isin(
            self.segments_df.loc[list(self.traveled_segments), 'street_id']
        )

        logger.info(f"Retrieved street network with {len(street_network)} streets.")
        return street_network