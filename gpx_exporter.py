import logging
from lxml import etree
from datetime import datetime, timezone
from date_utils import parse_date, format_date, get_start_of_day, get_end_of_day, date_range
from logging_config import setup_logging
setup_logging()

logger = logging.getLogger(__name__)

class GPXExporter:
    def __init__(self, geojson_handler):
        self.geojson_handler = geojson_handler

    async def export_to_gpx(self, start_date, end_date, filter_waco, waco_boundary):
        try:
            logger.info(f"Exporting GPX for date range: {start_date} to {end_date}")
            logger.info(f"Filter Waco: {filter_waco}, Waco Boundary: {waco_boundary}")

            start_date = parse_date(start_date)
            end_date = parse_date(end_date)

            waco_limits = None
            if filter_waco:
                waco_limits = self.geojson_handler.load_waco_boundary(waco_boundary)
                logger.info(f"Loaded Waco limits: {waco_limits is not None}")

            filtered_features = []

            for current_date in date_range(start_date, end_date):
                month_year = current_date.strftime("%Y-%m")
                if month_year in self.geojson_handler.monthly_data:
                    month_features = self.geojson_handler.filter_geojson_features(
                        format_date(get_start_of_day(current_date)),
                        format_date(get_end_of_day(current_date)),
                        filter_waco,
                        waco_limits, 
                        self.geojson_handler.monthly_data[month_year]
                    )
                    filtered_features.extend(month_features)

            logger.info(f"Number of filtered features: {len(filtered_features)}")

            if not filtered_features:
                logger.warning("No features found after filtering")
                return None

            gpx = etree.Element("gpx", version="1.1", creator="EveryStreetApp")

            # Add metadata
            metadata = etree.SubElement(gpx, "metadata")
            name = etree.SubElement(metadata, "name")
            name.text = f"GPX Export {format_date(start_date)} to {format_date(end_date)}"
            time = etree.SubElement(metadata, "time")
            time.text = format_date(datetime.now(timezone.utc))

            for i, feature in enumerate(filtered_features):
                logger.info(f"Processing feature {i+1}/{len(filtered_features)}")
                if 'geometry' not in feature or 'coordinates' not in feature['geometry']:
                    logger.warning(f"Invalid feature structure: {feature}")
                    continue

                trk = etree.SubElement(gpx, "trk")
                name = etree.SubElement(trk, "name")
                name.text = f"Track {feature['properties'].get('id', f'Unknown_{i+1}')}"
                trkseg = etree.SubElement(trk, "trkseg")

                coordinates = feature["geometry"]["coordinates"]
                timestamps = self.geojson_handler.get_feature_timestamps(feature)

                logger.info(f"Number of coordinates in feature: {len(coordinates)}")
                for j, coord in enumerate(coordinates):
                    if not isinstance(coord, (list, tuple)) or len(coord) < 2:
                        logger.warning(f"Invalid coordinate: {coord}")
                        continue
                    trkpt = etree.SubElement(
                        trkseg, "trkpt", lat=str(coord[1]), lon=str(coord[0])
                    )
                    if j < len(timestamps):
                        time = etree.SubElement(trkpt, "time")
                        timestamp = timestamps[j]
                        if isinstance(timestamp, (int, float)):
                            time.text = format_date(datetime.fromtimestamp(timestamp, timezone.utc))
                        elif isinstance(timestamp, tuple) and len(timestamp) >= 1:
                            time.text = format_date(datetime.fromtimestamp(timestamp[0], timezone.utc))
                        else:
                            logger.warning(f"Invalid timestamp format for coordinate {j} in feature {i+1}: {timestamp}")
                    else:
                        logger.warning(f"No timestamp for coordinate {j} in feature {i+1}")

            gpx_data = etree.tostring(
                gpx, pretty_print=True, xml_declaration=True, encoding="UTF-8"
            )
            logger.info(f"Successfully created GPX data of length: {len(gpx_data)}")
            return gpx_data
        except Exception as e:
            logger.error(f"Error in export_to_gpx: {str(e)}", exc_info=True)
            raise