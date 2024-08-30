import json
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from models import Base, User, WacoBoundary, WacoStreet, HistoricalData, LiveRoute
from geoalchemy2.shape import from_shape
from shapely.geometry import shape
from datetime import datetime
import logging
from sqlalchemy import func

logger = logging.getLogger(__name__)

class DatabaseHandler:
    def __init__(self, db_url='sqlite:///every_street.db'):
        self.engine = create_engine(db_url)

        @event.listens_for(self.engine, "connect")
        def load_spatialite(dbapi_conn, connection_record):
            dbapi_conn.enable_load_extension(True)
            dbapi_conn.load_extension('/opt/homebrew/lib/mod_spatialite.dylib')
            dbapi_conn.enable_load_extension(False)

            # Initialize SpatiaLite metadata
            dbapi_conn.execute("SELECT InitSpatialMetaData(1)")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def get_session(self):
        return self.Session()

    def init_db(self):
        Base.metadata.create_all(self.engine)

    def add_user(self, username, password, client_id, client_secret, auth_code, device_imei, vehicle_id, google_maps_api):
        session = self.get_session()
        user = User(username=username, password=password, client_id=client_id, client_secret=client_secret,
                    auth_code=auth_code, device_imei=device_imei, vehicle_id=vehicle_id, google_maps_api=google_maps_api)
        session.add(user)
        session.commit()
        session.close()

    def get_user(self, username):
        session = self.get_session()
        user = session.query(User).filter_by(username=username).first()
        session.close()
        return user

    def update_user(self, username, **kwargs):
        session = self.get_session()
        user = session.query(User).filter_by(username=username).first()
        if user:
            for key, value in kwargs.items():
                setattr(user, key, value)
            session.commit()
        session.close()

    def add_waco_boundary(self, name, geojson_file):
        session = self.get_session()
        with open(geojson_file, 'r') as f:
            data = json.load(f)
        geometry = shape(data['features'][0]['geometry'])
        waco_boundary = WacoBoundary(name=name, geometry=from_shape(geometry, srid=4326))
        session.add(waco_boundary)
        session.commit()
        session.close()

    def get_waco_boundary(self, name):
        session = self.get_session()
        boundary = session.query(WacoBoundary).filter_by(name=name).first()
        session.close()
        return boundary

    def add_waco_streets(self, geojson_file):
        session = self.get_session()
        with open(geojson_file, 'r') as f:
            data = json.load(f)
        for feature in data['features']:
            street_id = feature['properties']['street_id']
            name = feature['properties'].get('name', '')
            geometry = shape(feature['geometry'])
            street = WacoStreet(street_id=street_id, name=name, geometry=from_shape(geometry, srid=4326))
            session.add(street)
        session.commit()
        session.close()

    def get_waco_streets(self, traveled=None):
        session = self.get_session()
        query = session.query(WacoStreet)
        if traveled is not None:
            query = query.filter_by(traveled=traveled)
        streets = query.all()
        session.close()
        return streets

    def add_historical_data(self, geojson_file):
        session = self.get_session()
        with open(geojson_file, 'r') as f:
            data = json.load(f)
        for feature in data['features']:
            timestamp = datetime.fromtimestamp(feature['properties']['timestamp'])
            geometry = shape(feature['geometry'])
            speed = feature['properties'].get('speed')
            heading = feature['properties'].get('heading')
            historical_data = HistoricalData(timestamp=timestamp, geometry=from_shape(geometry, srid=4326),
                                             speed=speed, heading=heading)
            session.add(historical_data)
        session.commit()
        session.close()

    def get_historical_data(self, start_date, end_date):
        session = self.get_session()
        query = session.query(HistoricalData)
        if start_date:
            query = query.filter(HistoricalData.timestamp >= start_date)
        if end_date:
            query = query.filter(HistoricalData.timestamp <= end_date)
        data = query.all()
        session.close()
        return data

    def add_live_route_point(self, timestamp, latitude, longitude, speed, address):
        session = self.get_session()
        live_route = LiveRoute(timestamp=timestamp, latitude=latitude, longitude=longitude, speed=speed, address=address)
        session.add(live_route)
        session.commit()
        session.close()

    def get_live_route(self, limit=100):
        session = self.get_session()
        route = session.query(LiveRoute).order_by(LiveRoute.timestamp.desc()).limit(limit).all()
        session.close()
        return route

    def clear_live_route(self):
        session = self.get_session()
        session.query(LiveRoute).delete()
        session.commit()
        session.close()

    def get_stats(self):
        session = self.get_session()
        stats = {
            "waco_boundaries": session.query(WacoBoundary).count(),
            "waco_streets": session.query(WacoStreet).count(),
            "historical_data": session.query(HistoricalData).count(),
            "live_route": session.query(LiveRoute).count()
        }
        session.close()
        return stats

    def get_latest_historical_timestamp(self):
        session = self.get_session()
        latest = session.query(func.max(HistoricalData.timestamp)).scalar()
        session.close()
        return latest

    def add_historical_data_point(self, point_data):
        session = self.get_session()
        new_point = HistoricalData(
            timestamp=point_data['timestamp'],
            geometry=f"POINT({point_data['longitude']} {point_data['latitude']})",
            speed=point_data['speed'],
            heading=point_data['heading']
        )
        session.add(new_point)
        session.commit()
        session.close()

    def reset_street_progress(self):
        session = self.get_session()
        try:
            session.query(WacoStreet).update({WacoStreet.traveled: False})
            session.commit()
            logger.info("Street progress reset successfully")
        except Exception as e:
            session.rollback()
            logger.error(f"Error resetting street progress: {str(e)}")
            raise
        finally:
            session.close()

    def update_historical_data_schema(self):
        try:
            with self.engine.connect() as connection:
                connection.execute(text("ALTER TABLE historical_data ADD COLUMN IF NOT EXISTS speed FLOAT"))
                connection.execute(text("ALTER TABLE historical_data ADD COLUMN IF NOT EXISTS heading INTEGER"))
            logger.info("Updated historical_data schema successfully")
        except Exception as e:
            logger.error(f"Error updating historical_data schema: {e}")
            raise