import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime, timedelta, timezone
import functools
from quart import Quart, render_template, jsonify, request, Response, redirect, url_for, session
from quart_cors import cors
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig
from dotenv import load_dotenv
from redis import Redis
from bouncie_api import BouncieAPI
from geojson_handler import GeoJSONHandler
from gpx_exporter import GPXExporter
from geopy.geocoders import Nominatim
import redis
import gzip
from typing import List
from date_utils import parse_date, format_date, get_start_of_day, get_end_of_day, date_range, days_ago
from shapely.geometry import shape, box, LineString, Polygon
from waco_streets_analyzer import WacoStreetsAnalyzer

# Logging Setup
log_directory = "logs"
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

log_file = os.path.join(log_directory, "app.log")
file_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

logging.getLogger().setLevel(logging.DEBUG)  # Changed to DEBUG for more detailed logs
logging.getLogger().addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)  # Changed to DEBUG for more detailed logs
console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized")

# Load environment variables
load_dotenv()

# Login Decorator
def login_required(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return await func(*args, **kwargs)
    return wrapper


# Initialize Flask App
app = Quart(__name__)
app = cors(app)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
app.config["PIN"] = os.getenv("PIN")

# Initialize app attributes
app.historical_data_loaded = False
app.historical_data_loading = False
app.is_processing = False


# Initialize API Clients and Handlers
geojson_handler = GeoJSONHandler()
geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)
bouncie_api = BouncieAPI()
gpx_exporter = GPXExporter(geojson_handler)

# Initialize WacoStreetsAnalyzer
waco_analyzer = WacoStreetsAnalyzer('static/Waco-Streets.geojson')

# Asynchronous Locks
historical_data_lock = asyncio.Lock()
processing_lock = asyncio.Lock()
live_route_lock = asyncio.Lock()

# Live Route Data File
LIVE_ROUTE_DATA_FILE = "live_route_data.geojson"

# Task Manager
class TaskManager:
    def __init__(self):
        self.tasks = set()

    def add_task(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def cancel_all(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

app.task_manager = TaskManager()

# Load live route data from file
def load_live_route_data():
    try:
        with open(LIVE_ROUTE_DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.warning(f"File not found: {LIVE_ROUTE_DATA_FILE}. Creating an empty GeoJSON.")
        empty_geojson = {"type": "FeatureCollection", "features": []}
        save_live_route_data(empty_geojson)
        return empty_geojson
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {LIVE_ROUTE_DATA_FILE}. File may be corrupted.")
        return {"type": "FeatureCollection", "features": []}

# Save live route data to file
def save_live_route_data(data):
    with open(LIVE_ROUTE_DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ------------------------------ ROUTES ------------------------------ #

@app.route('/progress')
async def get_progress():
    progress = geojson_handler.waco_analyzer.calculate_progress()
    return jsonify({'progress': progress})

@app.route("/update_progress", methods=["POST"])
async def update_progress():
    try:
        await geojson_handler.update_progress()
        progress = geojson_handler.get_progress()
        return jsonify({"progress": progress}), 200
    except Exception as e:
        logging.error(f"Error updating progress: {str(e)}", exc_info=True)
        return jsonify({"error": f"Error updating progress: {str(e)}"}), 500
    
@app.route('/untraveled_streets')
async def get_untraveled_streets():
    waco_boundary = request.args.get("wacoBoundary", "city_limits")
    progress_geojson = geojson_handler.get_progress_geojson(waco_boundary)
    return jsonify(progress_geojson)

# Bouncie API Routes
@app.route("/latest_bouncie_data")
async def get_latest_bouncie_data():
    async with live_route_lock:
        return jsonify(getattr(app, 'latest_bouncie_data', {}))

@app.route("/live_route", methods=["GET"])
async def live_route():
    async with live_route_lock:
        live_route_data = getattr(app, 'live_route_data', {})
        if not live_route_data or 'features' not in live_route_data or not live_route_data['features']:
            return jsonify({"type": "FeatureCollection", "features": []})
        return jsonify(live_route_data)

# Data Routes
@app.route("/historical_data_status")
async def historical_data_status():
    async with historical_data_lock:
        return jsonify({
            "loaded": app.historical_data_loaded,
            "loading": app.historical_data_loading
        })

@app.route("/historical_data")
async def get_historical_data():
    try:
        start_date = request.args.get("startDate", "2020-01-01")
        end_date = request.args.get("endDate", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        filter_waco = request.args.get("filterWaco", "false").lower() == "true"
        waco_boundary = request.args.get("wacoBoundary", "city_limits")
        bounds_str = request.args.get("bounds", None)

        logging.info(f"Received request for historical data: start_date={start_date}, end_date={end_date}, filter_waco={filter_waco}, waco_boundary={waco_boundary}")

        bounds = None
        if bounds_str:
            try:
                bounds = [float(x) for x in bounds_str.split(",")]
            except ValueError:
                return jsonify({"error": "Invalid bounds format"}), 400

        waco_limits = None
        if filter_waco and waco_boundary != "none":
            waco_limits = geojson_handler.load_waco_boundary(waco_boundary)

        filtered_features = geojson_handler.filter_geojson_features(
            start_date,
            end_date,
            filter_waco,
            waco_limits,
            bounds=bounds
        )

        result = {"type": "FeatureCollection", "features": filtered_features, "total_features": len(filtered_features)}
        
        return jsonify(result)

    except ValueError as e:
        logging.error(f"Error parsing date: {str(e)}")
        return jsonify({"error": f"Invalid date format: {str(e)}"}), 400
    except Exception as e:
        logging.error(f"Error filtering historical data: {str(e)}", exc_info=True)
        return jsonify({"error": f"Error filtering historical data: {str(e)}"}), 500

@app.route("/live_data")
async def get_live_data():
    try:
        bouncie_data = await bouncie_api.get_latest_bouncie_data()
        if bouncie_data:
            new_point = {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [bouncie_data["longitude"], bouncie_data["latitude"]],
                },
                "properties": {"timestamp": bouncie_data["timestamp"]},
            }
            async with live_route_lock:
                app.live_route_data["features"].append(new_point)  
                save_live_route_data(app.live_route_data) 

            return jsonify(bouncie_data)
        return jsonify({"error": "No live data available"})
    except Exception as e:
        logging.error(f"An error occurred while fetching live data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/trip_metrics")
async def get_trip_metrics():
    formatted_metrics = await bouncie_api.get_trip_metrics()
    return jsonify(formatted_metrics)

@app.route("/export_gpx")
async def export_gpx():
    start_date = parse_date(request.args.get("startDate", "2020-01-01"))
    end_date = parse_date(request.args.get("endDate", datetime.now(timezone.utc).strftime("%Y-%m-%d")))
    filter_waco = request.args.get("filterWaco", "false").lower() == "true"
    waco_boundary = request.args.get("wacoBoundary", "city_limits")

    try:
        gpx_data = await gpx_exporter.export_to_gpx(
            format_date(start_date), format_date(end_date), filter_waco, waco_boundary
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
async def search_location():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    try:
        location = await asyncio.to_thread(geolocator.geocode, query)
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
async def search_suggestions():
    query = request.args.get("query")
    if not query:
        return jsonify({"error": "No search query provided"}), 400

    try:
        locations = await asyncio.to_thread(geolocator.geocode, query, exactly_one=False, limit=5)
        if locations:
            suggestions = [{"address": location.address} for location in locations]
            return jsonify(suggestions)
        else:
            return jsonify([])
    except Exception as e:
        logging.error(f"Error during location search: {e}")
        return jsonify({"error": "An error occurred during the search"}), 500

@app.route("/update_historical_data", methods=["POST"])
async def update_historical_data():
    async with processing_lock:
        if app.is_processing:
            return jsonify({"error": "Another process is already running"}), 429

        try:
            app.is_processing = True
            logging.info("Starting historical data update process")
            await geojson_handler.update_historical_data(fetch_all=True)  # Added fetch_all=True
            logging.info("Historical data update process completed")
            return jsonify({"message": "Historical data updated successfully!"}), 200
        except Exception as e:
            logging.error(f"An error occurred during the update process: {e}")
            return jsonify({"error": f"An error occurred: {str(e)}"}), 500
        finally:
            app.is_processing = False

@app.route("/progress_geojson")
async def get_progress_geojson():
    try:
        waco_boundary = request.args.get("wacoBoundary", "city_limits")
        progress_geojson = geojson_handler.get_progress_geojson(waco_boundary)
        return jsonify(progress_geojson)
    except Exception as e:
        logging.error(f"Error getting progress GeoJSON: {str(e)}", exc_info=True)
        return jsonify({"error": f"Error getting progress GeoJSON: {str(e)}"}), 500

@app.route('/processing_status')
async def processing_status():
    async with processing_lock:
        return jsonify({'isProcessing': app.is_processing})

# Authentication Routes
@app.route("/login", methods=["GET", "POST"])
async def login():
    if request.method == "POST":
        form = await request.form
        pin = form.get("pin")
        if pin == app.config["PIN"]:
            session["authenticated"] = True
            return redirect(url_for("index"))
        else:
            return await render_template("login.html", error="Invalid PIN. Please try again.")
    return await render_template("login.html")

@app.route("/logout", methods=["GET", "POST"])
async def logout():
    session.pop("authenticated", None)
    return redirect(url_for("login"))

# Main Route
@app.route("/")
@login_required
async def index():
    today = datetime.now().strftime("%Y-%m-%d")
    async with historical_data_lock:
        return await render_template("index.html", today=today, historical_data_loaded=app.historical_data_loaded)

# ------------------------------ ASYNC TASKS ------------------------------ #

async def poll_bouncie_api():
    while True:
        try:
            bouncie_data = await bouncie_api.get_latest_bouncie_data()
            if bouncie_data:
                async with live_route_lock:
                    app.live_route_data = load_live_route_data()

                    if "features" not in app.live_route_data:
                        app.live_route_data["features"] = [{
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": []
                            },
                            "properties": {}
                        }]
                    live_route_feature = app.live_route_data["features"][0]

                    new_coord = [bouncie_data["longitude"], bouncie_data["latitude"]]

                    if not live_route_feature["geometry"]["coordinates"] or new_coord != live_route_feature["geometry"]["coordinates"][-1]:
                        live_route_feature["geometry"]["coordinates"].append(new_coord)
                        save_live_route_data(app.live_route_data)
                        app.latest_bouncie_data = bouncie_data
                    else:
                        logging.info("Duplicate point detected, not adding to live route.")

            await asyncio.sleep(1)

        except Exception as e:
            logging.error(f"An error occurred while fetching live data: {e}")
            await asyncio.sleep(5)

async def load_historical_data_background():
    app.historical_data_loading = True
    try:
        await geojson_handler.load_historical_data()
        app.historical_data_loaded = True
        logging.info("Historical data loaded successfully")
    except Exception as e:
        logging.error(f"Error loading historical data: {str(e)}", exc_info=True)
    finally:
        app.historical_data_loading = False


# ------------------------------ APP LIFECYCLE EVENTS ------------------------------ #

@app.before_serving
async def startup():
    app.historical_data_loaded = False
    app.historical_data_loading = False
    asyncio.create_task(load_historical_data_background())
    logging.info("Starting application initialization...")
    try:
        app.live_route_data = load_live_route_data()
        logging.info("Live route data loaded.")
        
        logging.info("Initializing historical data...")
        await geojson_handler.initialize_data()
        logging.info("Historical data initialized.")
        
        app.task_manager.add_task(poll_bouncie_api())
        logging.info("Bouncie API polling task added.")
        
        logging.info(f"Available routes: {app.url_map}")
        logging.info("Application initialization complete.")
    except Exception as e:
        logging.error(f"Error during startup: {str(e)}", exc_info=True)
        raise

@app.after_serving
async def shutdown():
    await app.task_manager.cancel_all()

# ------------------------------ RUN APP ------------------------------ #

def handle_exception(loop, context):
    # context["message"] will always be there; but context["exception"] may not
    msg = context.get("exception", context["message"])
    logging.error(f"Caught exception: {msg}")
    logging.info("Shutting down...")
    asyncio.create_task(shutdown())

if __name__ == "__main__":
    async def run_app():
        logging.info("Setting up Hypercorn configuration...")
        hyper_config = HyperConfig()
        hyper_config.bind = ["0.0.0.0:8080"]
        hyper_config.workers = 1
        hyper_config.startup_timeout = 36000
        logging.info("Starting Hypercorn server...")
        try:
            await serve(app, hyper_config)
        except Exception as e:
            logging.error(f"Error starting Hypercorn server: {str(e)}", exc_info=True)
            raise

    logging.info("Starting application...")
    asyncio.run(run_app())
    logging.info("Application has shut down.")