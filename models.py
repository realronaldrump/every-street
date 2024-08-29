from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry

Base = declarative_base()

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)
    client_id = Column(String)
    client_secret = Column(String)
    auth_code = Column(String)
    device_imei = Column(String)
    vehicle_id = Column(String)
    google_maps_api = Column(String)

class WacoBoundary(Base):
    __tablename__ = 'waco_boundaries'

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    geometry = Column(Geometry('MULTIPOLYGON'))

class WacoStreet(Base):
    __tablename__ = 'waco_streets'

    id = Column(Integer, primary_key=True)
    street_id = Column(String, unique=True, nullable=False)
    name = Column(String)
    geometry = Column(Geometry('LINESTRING'))
    traveled = Column(Boolean, default=False)

class HistoricalData(Base):
    __tablename__ = 'historical_data'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
    geometry = Column(Geometry('POINT'), nullable=False)
    speed = Column(Float)
    heading = Column(Integer)

    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'latitude': self.geometry.y,
            'longitude': self.geometry.x,
            'speed': self.speed,
            'heading': self.heading
        }

class LiveRoute(Base):
    __tablename__ = 'live_routes'

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    speed = Column(Float)
    address = Column(String)

    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'latitude': self.latitude,
            'longitude': self.longitude,
            'speed': self.speed,
            'address': self.address
        }