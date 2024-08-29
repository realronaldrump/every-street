import logging
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, box
from rtree import index
import numpy as np
from shapely.ops import unary_union
import asyncio

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file, snap_distance=0.0001):
        logger.info("Initializing WacoStreetsAnalyzer...")
        try:
            self.streets_gdf = gpd.read_file(waco_streets_file)
            self.snap_distance = snap_distance
            self.traveled_segments = set()

            self._process_streets_into_segments()
            self._create_spatial_index()

            logger.info(f"Processed {len(self.segments_df)} segments.")
        except Exception as e:
            logger.error(f"Error initializing WacoStreetsAnalyzer: {str(e)}")
            raise

    def _process_streets_into_segments(self):
        utm_crs = self._get_utm_crs(self.streets_gdf)
        self.streets_gdf = self.streets_gdf.to_crs(utm_crs)

        segments = []
        for idx, street in self.streets_gdf.iterrows():
            coords = list(street.geometry.coords)
            for i in range(len(coords) - 1):
                if isinstance(coords[i], tuple) and isinstance(coords[i + 1], tuple):
                    segment = LineString([coords[i], coords[i + 1]])
                    segments.append({
                        'geometry': segment,
                        'segment_id': f"{street['street_id']}_{i}",
                        'street_id': street['street_id'],
                        'length': segment.length
                    })
                else:
                    logger.error(f"Invalid coordinate data at index {i}: {coords[i]}, {coords[i + 1]}")

        self.segments_df = gpd.GeoDataFrame(segments, crs=utm_crs).to_crs('EPSG:4326')
        self.waco_bbox = self.streets_gdf.total_bounds
        self.waco_box = box(*self.waco_bbox)

    def _get_utm_crs(self, gdf):
        lon, lat = (gdf.total_bounds[::2].mean(), gdf.total_bounds[1::2].mean())
        utm_zone = int((lon + 180) / 6) + 1
        return f'+proj=utm +zone={utm_zone} +datum=WGS84 +units=m +no_defs'

    def _create_spatial_index(self):
        self.spatial_index = index.Index()
        for idx, segment in self.segments_df.iterrows():
            self.spatial_index.insert(idx, segment.geometry.bounds)

    async def update_progress(self, new_routes):
        logger.info(f"Updating progress with {len(new_routes)} new routes...")
        new_segments = await self._process_routes(new_routes)
        self.traveled_segments.update(new_segments)
        logger.info(f"Progress update complete. Overall progress: {self.calculate_progress():.2f}%")

    async def _process_routes(self, routes):
        loop = asyncio.get_running_loop()
        route_lines = await loop.run_in_executor(None, gpd.GeoSeries, [LineString(route['geometry']['coordinates']) for route in routes])
        route_buffers = await loop.run_in_executor(None, lambda: route_lines.buffer(self.snap_distance))
        unified_buffer = await loop.run_in_executor(None, unary_union, route_buffers)
        intersecting_segments = await loop.run_in_executor(None, self.segments_df.intersects, unified_buffer)
        return set(self.segments_df[intersecting_segments].index)

    def reset_progress(self):
        logger.info("Resetting progress...")
        self.traveled_segments.clear()

    def calculate_progress(self):
        logger.info("Calculating progress...")
        total_length = self.segments_df['length'].sum()
        traveled_length = self.segments_df.loc[list(self.traveled_segments), 'length'].sum()
        return (traveled_length / total_length) * 100 if total_length > 0 else 0

    def get_progress_geojson(self, waco_boundary='city_limits'):
        logger.info("Generating progress GeoJSON...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        features = [
            {
                "type": "Feature",
                "geometry": segment.geometry.__geo_interface__,
                "properties": {
                    "segment_id": segment.segment_id,
                    "traveled": segment.Index in self.traveled_segments,
                    "color": "#00ff00" if segment.Index in self.traveled_segments else "#ff0000"
                }
            }
            for segment in self.segments_df.itertuples() if waco_limits is None or segment.geometry.intersects(waco_limits)
        ]
        return {"type": "FeatureCollection", "features": features}

    def get_untraveled_streets(self, waco_boundary='city_limits'):
        logger.info("Generating untraveled streets...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        untraveled_segments = self.segments_df[~self.segments_df.index.isin(self.traveled_segments)]
        if waco_limits is not None:
            untraveled_segments = untraveled_segments[untraveled_segments.intersects(waco_limits)]

        return untraveled_segments.dissolve(by='street_id')

    def analyze_coverage(self, waco_boundary='city_limits'):
        logger.info("Analyzing street coverage...")
        waco_limits = None
        if waco_boundary != "none":
            waco_limits = gpd.read_file(f"static/{waco_boundary}.geojson").geometry.unary_union

        total_streets = self.streets_gdf.intersects(waco_limits).sum() if waco_limits else len(self.streets_gdf)
        traveled_streets = len(set(self.segments_df.loc[list(self.traveled_segments), 'street_id']))
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

        street_network['traveled'] = street_network['street_id'].isin(
            self.segments_df.loc[list(self.traveled_segments), 'street_id']
        )

        return street_network