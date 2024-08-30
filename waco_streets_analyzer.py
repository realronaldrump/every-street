import logging
from geoalchemy2.shape import to_shape
from shapely.geometry import shape
from sqlalchemy import func
from models import WacoStreet, WacoBoundary
from sqlalchemy.orm import Session
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class WacoStreetsAnalyzer:
    def __init__(self, db_session: Session):
        self.db_session = db_session

    async def update_progress(self, historical_features: List[Dict[str, Any]]):
        try:
            # Get all streets
            streets = self.db_session.query(WacoStreet).all()
            
            # Create a spatial index for the streets
            street_index = {street.id: to_shape(street.geometry) for street in streets}

            for feature in historical_features:
                route = shape(feature['geometry'])
                for street_id, street_geom in street_index.items():
                    if route.intersects(street_geom):
                        street = self.db_session.query(WacoStreet).get(street_id)
                        street.traveled = True

            self.db_session.commit()
            logger.info("Street progress updated successfully")
        except Exception as e:
            self.db_session.rollback()
            logger.error(f"Error updating street progress: {str(e)}")
            raise

    def analyze_coverage(self) -> Dict[str, Any]:
        try:
            total_streets = self.db_session.query(func.count(WacoStreet.id)).scalar()
            traveled_streets = self.db_session.query(func.count(WacoStreet.id)).filter(WacoStreet.traveled == True).scalar()
            
            coverage_percentage = (traveled_streets / total_streets) * 100 if total_streets > 0 else 0

            return {
                "total_streets": total_streets,
                "traveled_streets": traveled_streets,
                "coverage_percentage": coverage_percentage
            }
        except Exception as e:
            logger.error(f"Error analyzing coverage: {str(e)}")
            raise

    def get_progress_geojson(self, waco_boundary: str = 'city_limits') -> Dict[str, Any]:
        try:
            boundary = self.db_session.query(WacoBoundary).filter_by(name=waco_boundary).first()
            if not boundary:
                raise ValueError(f"Waco boundary '{waco_boundary}' not found")

            boundary_shape = to_shape(boundary.geometry)
            streets = self.db_session.query(WacoStreet).all()
            
            features = []
            for street in streets:
                geom = to_shape(street.geometry)
                if geom.intersects(boundary_shape):
                    feature = {
                        "type": "Feature",
                        "geometry": geom.__geo_interface__,
                        "properties": {
                            "street_id": street.street_id,
                            "traveled": street.traveled,
                            "color": "#00ff00" if street.traveled else "#ff0000"
                        }
                    }
                    features.append(feature)

            return {"type": "FeatureCollection", "features": features}
        except Exception as e:
            logger.error(f"Error getting progress GeoJSON: {str(e)}")
            raise

    def get_untraveled_streets(self, waco_boundary: str = 'city_limits') -> List[WacoStreet]:
        try:
            boundary = self.db_session.query(WacoBoundary).filter_by(name=waco_boundary).first()
            if not boundary:
                raise ValueError(f"Waco boundary '{waco_boundary}' not found")

            boundary_shape = to_shape(boundary.geometry)
            untraveled_streets = self.db_session.query(WacoStreet).filter(
                WacoStreet.traveled == False,
                func.ST_Intersects(WacoStreet.geometry, boundary.geometry)
            ).all()
            return untraveled_streets
        except Exception as e:
            logger.error(f"Error getting untraveled streets: {str(e)}")
            raise

    def get_street_network(self, waco_boundary: str = 'city_limits') -> List[WacoStreet]:
        try:
            boundary = self.db_session.query(WacoBoundary).filter_by(name=waco_boundary).first()
            if not boundary:
                raise ValueError(f"Waco boundary '{waco_boundary}' not found")

            streets = self.db_session.query(WacoStreet).filter(
                func.ST_Intersects(WacoStreet.geometry, boundary.geometry)
            ).all()
            return streets
        except Exception as e:
            logger.error(f"Error getting street network: {str(e)}")
            raise