import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, jsonify, request, Response, redirect, url_for, session, flash
from flask_socketio import SocketIO
from dotenv import load_dotenv
from bouncie_api import BouncieAPI
from geojson_handler import GeoJSONHandler
from gpx_exporter import GPXExporter
from shapely.geometry import Polygon, LineString
from geopy.geocoders import Nominatim

# Set up logging
log_directory = "logs"
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

log_file = os.path.join(log_directory, "app.log")
file_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5)  # 10MB per file, keep 5 backups
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

# Set up root logger
logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(file_handler)

# Optionally, keep a streamhandler for console output, but set it to a higher level
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized")

load_dotenv()

from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
app.config["PIN"] = os.getenv("PIN", "1234")
socketio = SocketIO(app)

# Create a global instance of GeoJSONHandler
geojson_handler = GeoJSONHandler()

# Initialize geolocator for search functionality
geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)
bouncie_api = BouncieAPI()
gpx_exporter = GPXExporter(geojson_handler)  # Pass the geojson_handler instance

# Flag to check if historical data has been loaded
historical_data_loaded = False

# Add a global variable to track processing state
is_processing = False

@app.before_request
def load_historical_data():
    global historical_data_loaded
    if not historical_data_loaded:
        asyncio.run(geojson_handler.load_historical_data())
        historical_data_loaded = True

LIVE_ROUTE_DATA_FILE = "live_route_data.geojson"

def load_live_route_data():
    try:
        with open(LIVE_ROUTE_DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.warning(f"File not found: {LIVE_ROUTE_DATA_FILE}. Creating an empty GeoJSON.")
        return {"type": "FeatureCollection", "features": []}
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {LIVE_ROUTE_DATA_FILE}. File may be corrupted.")
        return {"type": "FeatureCollection", "features": []}

def save_live_route_data(data):
    with open(LIVE_ROUTE_DATA_FILE, "w") as f:
        json.dump(data, f)

live_route_data = load_live_route_data()

# Store the last point from the file in memory
if live_route_data["features"]:
    last_feature = live_route_data["features"][-1]
else:
    last_feature = None

async def poll_bouncie_api():
    global last_feature
    while True:
        try:
            bouncie_data = await bouncie_api.get_latest_bouncie_data()
            if bouncie_data:
                # Check if the new point is the same as the last one
                if last_feature:
                    last_coordinates = last_feature["geometry"]["coordinates"]
                    last_timestamp = last_feature["properties"]["timestamp"]

                    # Check if both the coordinates and timestamp are identical
                    if (
                        (bouncie_data["longitude"], bouncie_data["latitude"]) == tuple(last_coordinates) and 
                        bouncie_data["timestamp"] == last_timestamp
                    ):
                        logging.info("Duplicate point detected, not adding to live route.")
                        await asyncio.sleep(1)
                        continue

                # If not a duplicate, update the geojson with the latest point
                new_point = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [bouncie_data["longitude"], bouncie_data["latitude"]],
                    },
                    "properties": {"timestamp": bouncie_data["timestamp"]},
                }
                
                # Update the last feature in memory
                last_feature = new_point
                
                logging.info("Appending new point to live route data.")
                live_route_data["features"].append(new_point)
                logging.info("Saving updated live route data to file.")
                save_live_route_data(live_route_data)
                
                # Emit the update to any connected clients
                socketio.emit("live_update", bouncie_data)
            await asyncio.sleep(1)  # Poll every second
        except Exception as e:
            logging.error(f"An error occurred while fetching live data: {e}")
            await asyncio.sleep(5)  # Delay retry in case of error

@app.route("/live_route", methods=["GET"])
def live_route():
    return jsonify(live_route_data)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pin = request.form.get("pin")
        if pin == app.config["PIN"]:
            session["authenticated"] = True
            return redirect(url_for("index"))
        else:
            flash("Invalid PIN. Please try again.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("index.html", today=today)

@app.route("/historical_data")
def get_historical_data():
    start_date = request.args.get("startDate", "2020-01-01")
    end_date = request.args.get("endDate")
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    filter_waco = request.args.get("filterWaco", "false").lower() == "true"
    waco_boundary = request.args.get("wacoBoundary", "city_limits")

    try:
        waco_limits = None
        if filter_waco and waco_boundary != "none":
            waco_limits = geojson_handler.load_waco_boundary(waco_boundary)

        filtered_features = []
        start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
        end_datetime = datetime.strptime(end_date, "%Y-%m-%d")
        
        current_month = start_datetime.replace(day=1)
        while current_month <= end_datetime:
            month_year = current_month.strftime("%Y-%m")
            if month_year in geojson_handler.monthly_data:
                month_features = geojson_handler.filter_geojson_features(
                    start_date, end_date, filter_waco, waco_limits, 
                    geojson_handler.monthly_data[month_year]
                )
                filtered_features.extend(month_features)
            current_month += timedelta(days=32)
            current_month = current_month.replace(day=1)

        return jsonify({"type": "FeatureCollection", "features": filtered_features})

    except Exception as e:
        logging.error(f"Error filtering historical data: {e}")
        return (
            jsonify({"error": "Error filtering historical data", "details": str(e)}),
            500,
        )

@app.route("/live_data")
def get_live_data():
    try:
        bouncie_data = asyncio.run(bouncie_api.get_latest_bouncie_data())
        if bouncie_data:
            socketio.emit("live_update", bouncie_data)

            # Update live_route_data
            new_point = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [bouncie_data["longitude"], bouncie_data["latitude"]],
                },
                "properties": {"timestamp": bouncie_data["timestamp"]},
            }
            live_route_data["features"].append(new_point)
            save_live_route_data(live_route_data)

            return jsonify(bouncie_data)
        return jsonify({"error": "No live data available"})
    except Exception as e:
        logging.error(f"An error occurred while fetching live data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/trip_metrics")
def get_trip_metrics():
    formatted_metrics = bouncie_api.get_trip_metrics()
    return jsonify(formatted_metrics)

@app.route("/export_gpx")
def export_gpx():
    start_date = request.args.get("startDate", "2020-01-01")
    end_date = request.args.get("endDate")
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")  # Default to current date
    filter_waco = request.args.get("filterWaco", "false").lower() == "true"
    waco_boundary = request.args.get("wacoBoundary", "city_limits")

    try:
        gpx_data = gpx_exporter.export_to_gpx(
            start_date, end_date, filter_waco, waco_boundary
        )
        
        if gpx_data is None:
            logging.warning("No data found for GPX export")
            return jsonify({"error": "No data found for the specified date range"}), 404
        
        return Response(
            gpx_data,
            mimetype="application/gpx+xml",
            headers={"Content-Disposition": "attachment;filename=export.gpx"},
        )
    except Exception as e:
        logging.error(f"Error in export_gpx: {str(e)}", exc_info=True)
        return jsonify({"error": f"An error occurred while exporting GPX: {str(e)}"}), 500

@app.route("/search_location")
def search_location():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    try:
        location = geolocator.geocode(query)
        if location:
            return jsonify({
                "latitude": location.latitude,
                "longitude": location.longitude,
                "address": location.address
            })
        else:
            return jsonify({"error": "Location not found"}), 404
    except Exception as e:
        logging.error(f"Error during location search: {e}")
        return jsonify({"error": "An error occurred during the search"}), 500
    
@app.route("/search_suggestions")
def search_suggestions():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    try:
        locations = geolocator.geocode(query, exactly_one=False, limit=5)
        if locations:
            suggestions = [{"address": location.address} for location in locations]
            return jsonify(suggestions)
        else:
            return jsonify([])  # Return an empty list if no locations found
    except Exception as e:
        logging.error(f"Error during location search: {e}")
        return jsonify({"error": "An error occurred during the search"}), 500

@app.route("/update_historical_data", methods=["POST"])
def update_historical_data():
    global is_processing
    if is_processing:
        return jsonify({"error": "Another process is already running"}), 429

    try:
        is_processing = True
        socketio.emit('processing_start', broadcast=True)
        logging.info("Starting historical data update process")
        asyncio.run(geojson_handler.update_historical_data())
        logging.info("Historical data update process completed")
        return jsonify({"message": "Historical data updated successfully!"}), 200
    except Exception as e:
        logging.error(f"An error occurred during the update process: {e}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    finally:
        is_processing = False
        socketio.emit('processing_end', broadcast=True)

@app.route('/processing_status')
def processing_status():
    return jsonify({'isProcessing': is_processing})

@socketio.on('connect')
def handle_connect():
    if is_processing:
        socketio.emit('processing_start', room=request.sid)

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(poll_bouncie_api())
    try:
        socketio.run(app, debug=os.environ.get("DEBUG"), host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), use_reloader=False)
    finally:
        loop.close()