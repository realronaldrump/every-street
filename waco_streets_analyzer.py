import geopandas as gpd
from rtree import index
import shapely
from shapely.ops import linemerge, unary_union
import multiprocessing

class WacoStreetsAnalyzer:
    def __init__(self, waco_streets_file):
        self.streets_gdf = gpd.read_file(waco_streets_file)
        self.streets_index = index.Index()
        self.traveled_segments = set()
        
        for idx, street in self.streets_gdf.iterrows():
            self.streets_index.insert(idx, street.geometry.bounds)
            if 'name' not in street:
                street['name'] = f"Street_{idx}"  # Fallback name if not provided

    def get_nearby_streets(self, point, distance):
        bounds = point.buffer(distance).bounds
        return [self.streets_gdf.iloc[i] for i in self.streets_index.intersection(bounds)]

    def match_route(self, route, buffer_distance=10):
        route_buffer = route.buffer(buffer_distance)
        nearby_streets = self.get_nearby_streets(route.centroid, buffer_distance * 2)
        
        matched_streets = []
        for street in nearby_streets:
            intersection = street.geometry.intersection(route_buffer)
            if not intersection.is_empty:
                matched_streets.append((street, intersection))
        
        return matched_streets

    def update_traveled_segments(self, matched_streets):
        for street, intersection in matched_streets:
            if isinstance(intersection, (shapely.geometry.LineString, shapely.geometry.MultiLineString)):
                self.traveled_segments.add(street['name'])

    def calculate_progress(self):
        total_length = sum(street.geometry.length for street in self.streets_gdf.itertuples())
        traveled_length = sum(street.geometry.length for street in self.streets_gdf.itertuples() if street['name'] in self.traveled_segments)
        return (traveled_length / total_length) * 100

    def get_untraveled_streets(self):
        return [street for street in self.streets_gdf.itertuples() if street['name'] not in self.traveled_segments]

    def bulk_update_progress(self, routes):
        with multiprocessing.Pool() as pool:
            matched_streets = pool.map(self.match_route, routes)
        
        for matched in matched_streets:
            self.update_traveled_segments(matched)

    def incremental_update(self, new_routes):
        for route in new_routes:
            matched_streets = self.match_route(route)
            self.update_traveled_segments(matched_streets)

    def get_progress_geojson(self):
        features = []
        for street in self.streets_gdf.itertuples():
            feature = {
                "type": "Feature",
                "geometry": street.geometry.__geo_interface__,
                "properties": {
                    "name": street['name'],
                    "traveled": street['name'] in self.traveled_segments
                }
            }
            features.append(feature)
        return {"type": "FeatureCollection", "features": features}