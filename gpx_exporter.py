import logging
from lxml import etree
from datetime import datetime, timezone
from geojson_handler import GeoJSONHandler


class GPXExporter:
    def __init__(self):
        self.geojson_handler = GeoJSONHandler()

    def export_to_gpx(self, start_date, end_date, filter_waco, waco_boundary):
        try:
            logging.info(f"Exporting GPX for date range: {start_date} to {end_date}")
            logging.info(f"Filter Waco: {filter_waco}, Waco Boundary: {waco_boundary}")

            waco_limits = None
            if filter_waco:
                waco_limits = self.geojson_handler.load_waco_boundary(waco_boundary)
                logging.info(f"Loaded Waco limits: {waco_limits is not None}")

            filtered_features = self.geojson_handler.filter_geojson_features(
                start_date, end_date, filter_waco, waco_limits
            )
            logging.info(f"Number of filtered features: {len(filtered_features)}")
            
            if not filtered_features:
                logging.warning("No features found after filtering")
                return None  # Return None if no features are found

            gpx = etree.Element("gpx", version="1.1", creator="EveryStreetApp")
            for feature in filtered_features:
                trk = etree.SubElement(gpx, "trk")
                name = etree.SubElement(trk, "name")
                name.text = f"Track {feature['properties'].get('id', 'Unknown')}"
                trkseg = etree.SubElement(trk, "trkseg")
                
                coordinates = feature["geometry"]["coordinates"]
                if not isinstance(coordinates[0], list):
                    coordinates = [coordinates]  # Ensure it's a list of coordinates
                
                for coord in coordinates:
                    trkpt = etree.SubElement(
                        trkseg, "trkpt", lat=str(coord[1]), lon=str(coord[0])
                    )
                    time = etree.SubElement(trkpt, "time")
                    time.text = (
                        datetime.utcfromtimestamp(
                            feature["properties"]["timestamp"]
                        ).replace(tzinfo=timezone.utc).isoformat()
                    )

            gpx_data = etree.tostring(
                gpx, pretty_print=True, xml_declaration=True, encoding="UTF-8"
            )
            logging.info(f"Successfully created GPX data of length: {len(gpx_data)}")
            return gpx_data
        except Exception as e:
            logging.error(f"Error in export_to_gpx: {str(e)}")
            raise
