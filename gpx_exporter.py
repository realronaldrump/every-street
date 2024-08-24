import logging
from lxml import etree
from datetime import datetime, timezone, timedelta

class GPXExporter:
    def __init__(self, geojson_handler):
        self.geojson_handler = geojson_handler

    async def export_to_gpx(self, start_date, end_date, filter_waco, waco_boundary):
        try:
            logging.info(f"Exporting GPX for date range: {start_date} to {end_date}")
            logging.info(f"Filter Waco: {filter_waco}, Waco Boundary: {waco_boundary}")

            waco_limits = None
            if filter_waco:
                waco_limits = self.geojson_handler.load_waco_boundary(waco_boundary)
                logging.info(f"Loaded Waco limits: {waco_limits is not None}")

            filtered_features = []
            start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
            end_datetime = datetime.strptime(end_date, "%Y-%m-%d")
            
            current_month = start_datetime.replace(day=1)
            while current_month <= end_datetime:
                month_year = current_month.strftime("%Y-%m")
                if month_year in self.geojson_handler.monthly_data:
                    month_features = self.geojson_handler.filter_geojson_features(
                        start_date, end_date, filter_waco, waco_limits, 
                        self.geojson_handler.monthly_data[month_year]
                    )
                    filtered_features.extend(month_features)
                current_month += timedelta(days=32)
                current_month = current_month.replace(day=1)

            logging.info(f"Number of filtered features: {len(filtered_features)}")

            if not filtered_features:
                logging.warning("No features found after filtering")
                return None

            gpx = etree.Element("gpx", version="1.1", creator="EveryStreetApp")

            # Add metadata
            metadata = etree.SubElement(gpx, "metadata")
            name = etree.SubElement(metadata, "name")
            name.text = f"GPX Export {start_date} to {end_date}"
            time = etree.SubElement(metadata, "time")
            time.text = datetime.now(timezone.utc).isoformat()

            for i, feature in enumerate(filtered_features):
                logging.info(f"Processing feature {i+1}/{len(filtered_features)}")
                if 'geometry' not in feature or 'coordinates' not in feature['geometry']:
                    logging.warning(f"Invalid feature structure: {feature}")
                    continue

                trk = etree.SubElement(gpx, "trk")
                name = etree.SubElement(trk, "name")
                name.text = f"Track {feature['properties'].get('id', f'Unknown_{i+1}')}"
                trkseg = etree.SubElement(trk, "trkseg")

                coordinates = feature["geometry"]["coordinates"]
                timestamps = self.geojson_handler.get_feature_timestamps(feature)

                logging.info(f"Number of coordinates in feature: {len(coordinates)}")
                for j, coord in enumerate(coordinates):
                    if not isinstance(coord, (list, tuple)) or len(coord) < 2:
                        logging.warning(f"Invalid coordinate: {coord}")
                        continue
                    trkpt = etree.SubElement(
                        trkseg, "trkpt", lat=str(coord[1]), lon=str(coord[0])
                    )
                    if j < len(timestamps):
                        time = etree.SubElement(trkpt, "time")
                        timestamp = timestamps[j]
                        if isinstance(timestamp, (int, float)):
                            time.text = (
                                datetime.utcfromtimestamp(timestamp)
                                .replace(tzinfo=timezone.utc)
                                .isoformat()
                            )
                        elif isinstance(timestamp, tuple) and len(timestamp) >= 1:
                            # Assuming the first element of the tuple is the timestamp
                            time.text = (
                                datetime.utcfromtimestamp(timestamp[0])
                                .replace(tzinfo=timezone.utc)
                                .isoformat()
                            )
                        else:
                            logging.warning(f"Invalid timestamp format for coordinate {j} in feature {i+1}: {timestamp}")
                    else:
                        logging.warning(f"No timestamp for coordinate {j} in feature {i+1}")

            gpx_data = etree.tostring(
                gpx, pretty_print=True, xml_declaration=True, encoding="UTF-8"
            )
            logging.info(f"Successfully created GPX data of length: {len(gpx_data)}")
            return gpx_data
        except Exception as e:
            logging.error(f"Error in export_to_gpx: {str(e)}", exc_info=True)
            raise