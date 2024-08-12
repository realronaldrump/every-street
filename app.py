import asyncio
import json
import datetime
from datetime import datetime, timedelta, timezone
import os
import io

from geopy.distance import geodesic

import aiohttp
from flask import Flask, render_template, jsonify, request, send_from_directory, Response
from geopy.geocoders import Nominatim
from aiohttp import ClientTimeout

from bounciepy import AsyncRESTAPIClient

# ----- Bouncie Credentials and GitHub Settings -----
from dotenv import load_dotenv
from git import Repo

load_dotenv()  # Load environment variables from .env file

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
AUTH_CODE = os.getenv("AUTH_CODE")
VEHICLE_ID = os.getenv("VEHICLE_ID")
DEVICE_IMEI = os.getenv("DEVICE_IMEI")

GITHUB_USER = os.getenv("GITHUB_USER")
GITHUB_PAT = os.getenv("GITHUB_PAT")

# ----- Enable/Disable Geocoding -----
ENABLE_GEOCODING = True

app = Flask(__name__)

# Global variable to store historical GeoJSON data (now a list)
historical_geojson_features = []

# Global variable to store live trip data with timestamp of last update
live_trip_data = {
    'last_updated': datetime.now(timezone.utc),
    'data': []
}

# Initialize geocoder
geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)


async def reverse_geocode(lat, lon, retries=3):
    """Reverse geocode with retries and formatted output."""
    for attempt in range(retries):
        try:
            location = await asyncio.get_event_loop().run_in_executor(
                None, lambda: geolocator.reverse((lat, lon), addressdetails=True)
            )
            if location:
                address = location.raw['address']
                
                # Select the desired address components
                place = address.get('place', '')
                building = address.get('building', '') 
                house_number = address.get('house_number', '')
                road = address.get('road', '')
                city = address.get('city', '')
                state = address.get('state', '')
                postcode = address.get('postcode', '')

                # Construct the formatted address string
                formatted_address = f"{place}<br>" if place else ''  # Include place if it exists
                formatted_address += f"{building}<br>" if building else '' # Include building if it exists
                formatted_address += f"{house_number} {road}<br>{city}, {state} {postcode}"

                return formatted_address
            else:
                return "N/A"
        except Exception as e:
            print(f"Attempt {attempt + 1} failed with error: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(1)
    return "N/A"

async def fetch_trip_data(session, vehicle_id, date, headers):
    """Fetches trip summary data for a specific date."""
    start_time = f"{date}T00:00:00-05:00"
    end_time = f"{date}T23:59:59-05:00"
    summary_url = f"https://www.bouncie.app/api/vehicles/{vehicle_id}/triplegs/details/summary?bands=true&defaultColor=%2355AEE9&overspeedColor=%23CC0000&startDate={start_time}&endDate={end_time}"

    async with session.get(summary_url, headers=headers) as response:
        if response.status == 200:
            return await response.json()
        else:
            print(f"Error fetching data for {date}. Status: {response.status}")
            return None


def create_geojson_features_from_trips(data):
    """Creates a list of GeoJSON features from Bouncie trip summary data."""
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
                "properties": {"timestamp": timestamp},  # Add timestamp to properties
            }
            features.append(feature)

    return features


async def get_latest_bouncie_data(client):
    """Fetches the latest location data from Bouncie."""
    vehicle_data = await client.get_vehicle_by_imei(imei=DEVICE_IMEI)
    if not vehicle_data or "stats" not in vehicle_data:
        return None

    stats = vehicle_data["stats"]
    location = stats.get("location")

    if not location:
        return None

    # Reverse geocode the location (conditionally)
    location_address = (
        await reverse_geocode(location["lat"], location["lon"])
        if ENABLE_GEOCODING
        else "N/A"
    )

    # Convert the ISO 8601 timestamp to a UNIX timestamp
    try:
        timestamp_iso = stats["lastUpdated"]
        timestamp_dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
        timestamp_unix = int(timestamp_dt.timestamp())
    except Exception as e:
        print(f"Error converting timestamp: {e}")
        return None

    # Map Bouncie battery status
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

            # Check if the GeoJSON file already exists
            if os.path.exists("static/historical_data.geojson"):
                print("GeoJSON file already exists. Skipping historical data fetch.")
                with open("static/historical_data.geojson", "r") as f:
                    data = json.load(f)
                    historical_geojson_features = data.get("features", [])
                return

            # --- Historical Trip Data Download ---
            today = datetime.now(tz=timezone.utc)
            start_date = datetime(2024, 7, 1, tzinfo=timezone.utc)  # Changed to 2020
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

            # Create a combined GeoJSON file
            print("Creating combined GeoJSON file...")
            historical_geojson_features = create_geojson_features_from_trips(all_trips)

            # Save the GeoJSON data to a file
            with open("static/historical_data.geojson", "w") as f:
                json.dump({"type": "FeatureCollection", "features": historical_geojson_features}, f)

        except Exception as e:
            print(f"An error occurred: {e}")

        finally:
            await client.client_session.close()


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

            # --- Get the latest timestamp from existing data ---
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
                latest_date = datetime(2020, 1, 1, tzinfo=timezone.utc)  # Default start date

            # --- Fetch new trip data from latest_date to today ---
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

            # --- Update historical_geojson_features with new data ---
            new_features = create_geojson_features_from_trips(all_trips)
            historical_geojson_features.extend(new_features)

            # --- Save the updated GeoJSON data to the file ---
            with open("static/historical_data.geojson", "w") as f:
                json.dump(
                    {"type": "FeatureCollection", "features": historical_geojson_features},
                    f,
                )

            # --- Commit and push changes to GitHub ---
            repo = Repo(".")  # Assumes the script is run from the repository root
            repo.git.add(".")  # Stage all changes
            repo.git.commit('-m', "Updated historical data")

            # Use the username and PAT to create the authentication string
            auth_string = f'{GITHUB_USER}:{GITHUB_PAT}'

            # Push to GitHub using the authentication string
            repo.git.push('https://' + auth_string + '@github.com/realronaldrump/every-street.git', 'main')

            return jsonify({"message": "Historical data updated successfully!"}), 200

        except Exception as e:
            print(f"An error occurred: {e}")
            return jsonify({"error": str(e)}), 500

        finally:
            await client.client_session.close()


async def periodic_data_update():
    while True:
        await update_historical_data()
        await asyncio.sleep(3600)  # Update every hour (adjust as needed)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/historical_data")
def get_historical_data():
    # Stream the GeoJSON data
    def generate():
        yield '{"type": "FeatureCollection", "features": ['
        for feature in historical_geojson_features:
            yield json.dumps(feature) + ","
        yield "]}"

    return Response(generate(), mimetype="application/json")


@app.route("/live_data")
async def get_live_data():
    global live_trip_data  # Access the global variable
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
                # Update last_updated timestamp
                live_trip_data["last_updated"] = datetime.now(timezone.utc)
                live_trip_data["data"].append(bouncie_data)
                return jsonify(bouncie_data)
            return jsonify({"error": "No live data available"})

        except Exception as e:
            return jsonify({"error": str(e)})

        finally:
            await client.client_session.close()


@app.route("/trip_metrics")
def get_trip_metrics():
    global live_trip_data  # Access the global variable

    # Check for timeout
    time_since_update = datetime.now(timezone.utc) - live_trip_data["last_updated"]
    if time_since_update.total_seconds() > 45:
        live_trip_data["data"] = []  # Reset the data array

    # Calculate trip metrics based on live_trip_data['data']
    total_distance = 0
    total_time = 0
    max_speed = 0
    start_time = None
    end_time = None

    for i in range(1, len(live_trip_data["data"])):
        prev_point = live_trip_data["data"][i - 1]
        curr_point = live_trip_data["data"][i]

        # Distance
        distance = geodesic(
            (prev_point["latitude"], prev_point["longitude"]),
            (curr_point["latitude"], curr_point["longitude"]),
        ).miles
        total_distance += distance

        # Time
        time_diff = curr_point["timestamp"] - prev_point["timestamp"]
        total_time += time_diff

        # Max Speed
        max_speed = max(max_speed, curr_point["speed"])

        # Start and End Times
        if start_time is None:
            start_time = prev_point["timestamp"]
        end_time = curr_point["timestamp"]

    # Format metrics
    formatted_metrics = {
        "total_distance": round(total_distance, 2),
        "total_time": format_time(total_time),
        "max_speed": max_speed,
        "start_time": datetime.fromtimestamp(start_time).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        if start_time
        else "N/A",
        "end_time": datetime.fromtimestamp(end_time).strftime("%Y-%m-%d %H:%M:%S")
        if end_time
        else "N/A",
    }

    return jsonify(formatted_metrics)


def format_time(seconds):
    """Formats seconds into hours, minutes, and seconds."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(load_historical_data())

    # Start the periodic data update task
    loop.create_task(periodic_data_update())

    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))