import asyncio
import json
import datetime
from datetime import datetime, timedelta, timezone
import os
import io

from geopy.distance import geodesic

import aiohttp
from flask import Flask, render_template, jsonify, request, Response, send_file
from geopy.geocoders import Nominatim
from aiohttp import ClientTimeout

from bounciepy import AsyncRESTAPIClient
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
from git import Repo

from lxml import etree  # For GPX export

load_dotenv()  # Load environment variables from .env file

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
AUTH_CODE = os.getenv("AUTH_CODE")
VEHICLE_ID = os.getenv("VEHICLE_ID")
DEVICE_IMEI = os.getenv("DEVICE_IMEI")

GITHUB_USER = os.getenv("GITHUB_USER")
GITHUB_PAT = os.getenv("GITHUB_PAT")

# Enable/Disable Geocoding
ENABLE_GEOCODING = True

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
socketio = SocketIO(app)

# Global variable to store historical GeoJSON data (now a list)
historical_geojson_features = []

# Global variable to store live trip data with timestamp of last update
live_trip_data = {
    'last_updated': datetime.now(timezone.utc),
    'data': []
}

# Initialize geocoder
geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)

# Filter GeoJSON features based on date and Waco filter
def filter_geojson_features(features, start_date, end_date, filter_waco):
    # Convert the start and end date strings to datetime objects
    start_datetime = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_datetime = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    
    # Adjust the end_datetime to include the entire day of end_date
    end_datetime += timedelta(days=1) - timedelta(seconds=1)

    filtered_features = []
    waco_limits = None

    # Load Waco city limits if the filter is enabled
    if filter_waco:
        with open("static/waco_city_limits.geojson") as f:
            waco_limits = json.load(f)["features"][0]["geometry"]["coordinates"][0]

    for feature in features:
        timestamp = feature["properties"].get("timestamp")

        if timestamp is not None:
            # Convert the timestamp to a datetime object
            route_datetime = datetime.fromtimestamp(timestamp, timezone.utc)

            # Check if the route falls within the filtered date range
            if start_datetime <= route_datetime <= end_datetime:
                if filter_waco:
                    if is_route_in_waco(feature, waco_limits):
                        filtered_features.append(feature)
                else:
                    filtered_features.append(feature)
    
    return filtered_features

# Check if a route is within Waco limits
def is_route_in_waco(feature, waco_limits):
    from shapely.geometry import Point, Polygon
    
    waco_polygon = Polygon(waco_limits)
    for coord in feature["geometry"]["coordinates"]:
        point = Point(coord[0], coord[1])
        if not waco_polygon.contains(point):
            return False
    return True

# Reverse geocode with retries and formatted output
async def reverse_geocode(lat, lon, retries=3):
    for attempt in range(retries):
        try:
            location = await asyncio.get_event_loop().run_in_executor(
                None, lambda: geolocator.reverse((lat, lon), addressdetails=True)
            )
            if location:
                address = location.raw['address']
                place = address.get('place', '')
                building = address.get('building', '') 
                house_number = address.get('house_number', '')
                road = address.get('road', '')
                city = address.get('city', '')
                state = address.get('state', '')
                postcode = address.get('postcode', '')

                formatted_address = f"{place}<br>" if place else ''  
                formatted_address += f"{building}<br>" if building else '' 
                formatted_address += f"{house_number} {road}<br>{city}, {state} {postcode}"

                return formatted_address
            else:
                return "N/A"
        except Exception as e:
            print(f"Attempt {attempt + 1} failed with error: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(1)
    return "N/A"

# Fetch trip summary data for a specific date
async def fetch_trip_data(session, vehicle_id, date, headers):
    start_time = f"{date}T00:00:00-05:00"
    end_time = f"{date}T23:59:59-05:00"
    summary_url = f"https://www.bouncie.app/api/vehicles/{vehicle_id}/triplegs/details/summary?bands=true&defaultColor=%2355AEE9&overspeedColor=%23CC0000&startDate={start_time}&endDate={end_time}"

    async with session.get(summary_url, headers=headers) as response:
        if response.status == 200:
            return await response.json()
        else:
            print(f"Error fetching data for {date}. Status: {response.status}")
            return None

# Create a list of GeoJSON features from Bouncie trip summary data
def create_geojson_features_from_trips(data):
    features = []

    for trip in data:
        if not isinstance(trip, dict):
            continue

        coordinates = []
        for band in trip.get("bands", []):
            for path in band.get("paths", []):
                for point in path:
                    lat, lon, _, _, timestamp, _ = point
                    coordinates.append([lon, lat])

        if coordinates:
            feature = {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {"timestamp": timestamp},  
            }
            features.append(feature)

    return features

# Fetch the latest location data from Bouncie
async def get_latest_bouncie_data(client):
    vehicle_data = await client.get_vehicle_by_imei(imei=DEVICE_IMEI)
    if not vehicle_data or "stats" not in vehicle_data:
        return None

    stats = vehicle_data["stats"]
    location = stats.get("location")

    if not location:
        return None

    location_address = (
        await reverse_geocode(location["lat"], location["lon"])
        if ENABLE_GEOCODING
        else "N/A"
    )

    try:
        timestamp_iso = stats["lastUpdated"]
        timestamp_dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
        timestamp_unix = int(timestamp_dt.timestamp())
    except Exception as e:
        print(f"Error converting timestamp: {e}")
        return None

    bouncie_status = stats["battery"]["status"]
    battery_state = (
        "full"
        if bouncie_status == "normal"
        else "unplugged"
        if bouncie_status == "low"
        else "unknown"
    )

    return {
        "latitude": location["lat"],
        "longitude": location["lon"],
        "timestamp": timestamp_unix,
        "battery_state": battery_state,
        "speed": stats["speed"],
        "device_id": DEVICE_IMEI,
        "address": location_address,
    }

# Load historical data from Bouncie and create a combined GeoJSON file
async def load_historical_data():
    global historical_geojson_features
    client = AsyncRESTAPIClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_url=REDIRECT_URI,
        auth_code=AUTH_CODE,
    )

    async with aiohttp.ClientSession() as session:
        try:
            success = await client.get_access_token()
            if not success:
                print("Failed to obtain Bouncie access token.")
                return

            headers = {
                "Accept": "application/json",
                "Authorization": client.access_token,
            }

            if os.path.exists("static/historical_data.geojson"):
                print("GeoJSON file already exists. Skipping historical data fetch.")
                with open("static/historical_data.geojson", "r") as f:
                    data = json.load(f)
                    historical_geojson_features = data.get("features", [])
                return

            today = datetime.now(tz=timezone.utc)
            start_date = datetime(2020, 1, 1, tzinfo=timezone.utc)
            end_date = today

            all_trips = []

            current_date = start_date
            while current_date < end_date:
                date_str = current_date.strftime("%Y-%m-%d")
                print(f"Fetching trips for: {date_str}")

                trips_data = await fetch_trip_data(
                    session, VEHICLE_ID, date_str, headers
                )
                if trips_data:
                    all_trips.extend(trips_data)

                current_date += timedelta(days=1)

            print("Creating combined GeoJSON file...")
            historical_geojson_features = create_geojson_features_from_trips(all_trips)

            with open("static/historical_data.geojson", "w") as f:
                json.dump({"type": "FeatureCollection", "features": historical_geojson_features}, f)

        except Exception as e:
            print(f"An error occurred: {e}")

        finally:
            await client.client_session.close()

# Update historical data and push changes to GitHub
@app.route('/update_historical_data')
async def update_historical_data():
    global historical_geojson_features
    client = AsyncRESTAPIClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_url=REDIRECT_URI,
        auth_code=AUTH_CODE,
    )

    async with aiohttp.ClientSession() as session:
        try:
            success = await client.get_access_token()
            if not success:
                print("Failed to obtain Bouncie access token.")
                return jsonify({'error': 'Failed to obtain Bouncie access token.'}), 500

            headers = {
                "Accept": "application/json",
                "Authorization": client.access_token,
            }

            if historical_geojson_features:
                latest_timestamp = max(
                    feature["properties"]["timestamp"]
                    for feature in historical_geojson_features
                    if feature["properties"].get("timestamp") is not None
                )
                latest_date = datetime.fromtimestamp(
                    latest_timestamp, tz=timezone.utc
                ) + timedelta(days=1)
            else:
                latest_date = datetime(2020, 1, 1, tzinfo=timezone.utc)

            today = datetime.now(tz=timezone.utc)
            all_trips = []

            current_date = latest_date
            while current_date < today:
                date_str = current_date.strftime("%Y-%m-%d")
                print(f"Fetching trips for: {date_str}")

                trips_data = await fetch_trip_data(
                    session, VEHICLE_ID, date_str, headers
                )
                if trips_data:
                    all_trips.extend(trips_data)

                current_date += timedelta(days=1)

            new_features = create_geojson_features_from_trips(all_trips)
            if new_features:
                historical_geojson_features.extend(new_features)

                with open("static/historical_data.geojson", "w") as f:
                    json.dump(
                        {"type": "FeatureCollection", "features": historical_geojson_features},
                        f,
                    )

                repo = Repo(".")
                repo.git.add("static/historical_data.geojson")

                if repo.is_dirty(untracked_files=True):
                    repo.git.commit('-m', "Updated historical data")
                    auth_string = f'{GITHUB_USER}:{GITHUB_PAT}'
                    repo.git.push('https://' + auth_string + '@github.com/realronaldrump/every-street.git', 'main')
                else:
                    print("No changes to commit.")

            return jsonify({"message": "Historical data updated successfully!"}), 200

        except Exception as e:
            print(f"An error occurred: {e}")
            return jsonify({"error": str(e)}), 500

        finally:
            await client.client_session.close()

# Route to serve the index page
@app.route("/")
def index():
    return render_template("index.html")

# Route to get filtered historical data
@app.route("/historical_data")
def get_historical_data():
    start_date = request.args.get("startDate", "2020-01-01")
    end_date = request.args.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    filter_waco = request.args.get("filterWaco", "false").lower() == "true"

    try:
        start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_timestamp = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    filtered_features = filter_geojson_features(historical_geojson_features, start_timestamp, end_timestamp, filter_waco)

    return jsonify({"type": "FeatureCollection", "features": filtered_features})

# Route to get live data
@app.route("/live_data")
async def get_live_data():
    global live_trip_data
    client = AsyncRESTAPIClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_url=REDIRECT_URI,
        auth_code=AUTH_CODE,
    )

    async with aiohttp.ClientSession() as session:
        try:
            success = await client.get_access_token()
            if not success:
                return jsonify({"error": "Failed to obtain Bouncie access token."})

            bouncie_data = await get_latest_bouncie_data(client)
            if bouncie_data:
                live_trip_data["last_updated"] = datetime.now(timezone.utc)
                live_trip_data["data"].append(bouncie_data)

                socketio.emit('live_update', bouncie_data)  # Emit real-time updates via WebSocket

                return jsonify(bouncie_data)
            return jsonify({"error": "No live data available"})

        except Exception as e:
            return jsonify({"error": str(e)})

        finally:
            await client.client_session.close()

# Route to get trip metrics
@app.route("/trip_metrics")
def get_trip_metrics():
    global live_trip_data

    time_since_update = datetime.now(timezone.utc) - live_trip_data["last_updated"]
    if time_since_update.total_seconds() > 45:
        live_trip_data["data"] = []

    total_distance = 0
    total_time = 0
    max_speed = 0
    start_time = None
    end_time = None

    for i in range(1, len(live_trip_data["data"])):
        prev_point = live_trip_data["data"][i - 1]
        curr_point = live_trip_data["data"][i]

        distance = geodesic(
            (prev_point["latitude"], prev_point["longitude"]),
            (curr_point["latitude"], curr_point["longitude"]),
        ).miles
        total_distance += distance

        time_diff = curr_point["timestamp"] - prev_point["timestamp"]
        total_time += time_diff

        max_speed = max(max_speed, curr_point["speed"])

        if start_time is None:
            start_time = prev_point["timestamp"]
        end_time = curr_point["timestamp"]

    formatted_metrics = {
        "total_distance": round(total_distance, 2),
        "total_time": format_time(total_time),
        "max_speed": max_speed,
        "start_time": datetime.fromtimestamp(start_time).strftime(
            "%Y-%m-%d %H:%M:%S"
        ) if start_time else "N/A",
        "end_time": datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S"
        ) if end_time else "N/A",
    }

    return jsonify(formatted_metrics)

# Helper function to format time
def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

# Route to export data to GPX format
@app.route("/export_gpx")
def export_gpx():
    start_date = request.args.get("startDate", "2020-01-01")
    end_date = request.args.get("endDate", None)
    filter_waco = request.args.get("filterWaco", "false").lower() == "true"

    # Handle missing end_date by setting it to today's date
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    try:
        start_timestamp = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp())
        end_timestamp = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp())
    except ValueError:
        return jsonify({"error": "Invalid date format"}), 400

    filtered_features = filter_geojson_features(historical_geojson_features, start_timestamp, end_timestamp, filter_waco)

    gpx = etree.Element("gpx", version="1.1", creator="EveryStreetApp")
    for feature in filtered_features:
        trk = etree.SubElement(gpx, "trk")
        trkseg = etree.SubElement(trk, "trkseg")
        for coord in feature["geometry"]["coordinates"]:
            trkpt = etree.SubElement(trkseg, "trkpt", lat=str(coord[1]), lon=str(coord[0]))
            time = etree.SubElement(trkpt, "time")
            time.text = datetime.utcfromtimestamp(feature["properties"]["timestamp"]).isoformat() + "Z"

    gpx_data = etree.tostring(gpx, pretty_print=True, xml_declaration=True, encoding="UTF-8")
    return Response(gpx_data, mimetype='application/gpx+xml', headers={"Content-Disposition": "attachment;filename=export.gpx"})
# Periodic update for historical data
async def periodic_data_update():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('http://localhost:8080/update_historical_data') as response:
                    if response.status == 200:
                        print("Historical data updated successfully")
                    else:
                        print(f"Failed to update historical data: {response.status}")
        except Exception as e:
            print(f"An error occurred during periodic update: {e}")

        await asyncio.sleep(3600)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_historical_data())

    loop.create_task(periodic_data_update())

    socketio.run(app, debug=False, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)), use_reloader=False)