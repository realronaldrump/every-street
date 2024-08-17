from lxml import etree
from datetime import datetime, timezone
from geojson_handler import GeoJSONHandler

class GPXExporter:
    def __init__(self):
        self.geojson_handler = GeoJSONHandler()

    def export_to_gpx(self, start_date, end_date, filter_waco):
        filtered_features = self.geojson_handler.filter_geojson_features(start_date, end_date, filter_waco)
        print("Number of filtered features:", len(filtered_features))  # Add this line for debugging
        print("First feature (if any):", filtered_features[0] if filtered_features else None)  # Add this line
        gpx = etree.Element("gpx", version="1.1", creator="EveryStreetApp")
        for feature in filtered_features:
            trk = etree.SubElement(gpx, "trk")
            trkseg = etree.SubElement(trk, "trkseg")
            for coord in feature["geometry"]["coordinates"]:
                trkpt = etree.SubElement(trkseg, "trkpt", lat=str(coord[1]), lon=str(coord[0]))
                time = etree.SubElement(trkpt, "time")
                time.text = datetime.utcfromtimestamp(feature["properties"]["timestamp"]).isoformat() + "Z"

        gpx_data = etree.tostring(gpx, pretty_print=True, xml_declaration=True, encoding="UTF-8")
        return gpx_data