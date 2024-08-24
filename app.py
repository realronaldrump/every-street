import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import json
from datetime import datetime, timezone, timedelta
from quart import Quart, render_template, jsonify, request, Response, redirect, url_for, session, websocket
from quart_cors import cors
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig
from dotenv import load_dotenv
from bouncie_api import BouncieAPI
from geojson_handler import GeoJSONHandler
from gpx_exporter import GPXExporter
from shapely.geometry import Polygon, LineString
from geopy.geocoders import Nominatim
import redis
import gzip

# Set up logging
log_directory = "logs"
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

log_file = os.path.join(log_directory, "app.log")
file_handler = RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5)
file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

logging.getLogger().setLevel(logging.INFO)
logging.getLogger().addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.WARNING)
console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logging.getLogger().addHandler(console_handler)

logging.info("Logging initialized")

load_dotenv()

def login_required(func):
    async def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return await func(*args, **kwargs)
    return wrapper

app = Quart(__name__)
app = cors(app)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
app.config["PIN"] = os.getenv("PIN", "1234")

# Redis configuration
redis_client = redis.Redis(host='localhost', port=6379, db=0)

geojson_handler = GeoJSONHandler()
geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)
bouncie_api = BouncieAPI()
gpx_exporter = GPXExporter(geojson_handler)

historical_data_loaded = False
is_processing = False
historical_data_loading = False

LIVE_ROUTE_DATA_FILE = "live_route_data.geojson"

background_tasks = set()

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
last_feature = live_route_data["features"][-1] if live_route_data["features"] else None

async def poll_bouncie_api():
    global last_feature
    while True:
        try:
            bouncie_data = await bouncie_api.get_latest_bouncie_data()
            if bouncie_data:
                if last_feature:
                    last_coordinates = last_feature["geometry"]["coordinates"]
                    last_timestamp = last_feature["properties"]["timestamp"]

                    if (
                        (bouncie_data["longitude"], bouncie_data["latitude"]) == tuple(last_coordinates) and 
                        bouncie_data["timestamp"] == last_timestamp
                    ):
                        logging.info("Duplicate point detected, not adding to live route.")
                        await asyncio.sleep(1)
                        continue

                new_point = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [bouncie_data["longitude"], bouncie_data["latitude"]],
                    },
                    "properties": {"timestamp": bouncie_data["timestamp"]},
                }
                
                last_feature = new_point
                
                logging.info("Appending new point to live route data.")
                live_route_data["features"].append(new_point)
                logging.info("Saving updated live route data to file.")
                save_live_route_data(live_route_data)
                
                # Instead of broadcasting, we'll update a global variable
                app.latest_bouncie_data = bouncie_data
            await asyncio.sleep(1)
        except Exception as e:
            logging.error(f"An error occurred while fetching live data: {e}")
            await asyncio.sleep(5)

@app.route("/latest_bouncie_data")
async def get_latest_bouncie_data():
    return jsonify(getattr(app, 'latest_bouncie_data', {}))

@app.before_serving
async def startup():
    task = asyncio.create_task(load_historical_data_background())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)

async def load_historical_data_background():
    global historical_data_loaded, historical_data_loading
    if not historical_data_loaded and not historical_data_loading:
        historical_data_loading = True
        await geojson_handler.load_historical_data()
        historical_data_loaded = True
        historical_data_loading = False

@app.route("/live_route", methods=["GET"])
async def live_route():
    return jsonify(live_route_data)

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

@app.route("/")
@login_required
async def index():
    today = datetime.now().strftime("%Y-%m-%d")
    return await render_template("index.html", today=today, historical_data_loaded=historical_data_loaded)

@app.route("/historical_data_status")
async def historical_data_status():
    return jsonify({
        "loaded": historical_data_loaded,
        "loading": historical_data_loading
    })

@app.route("/historical_data")
async def get_historical_data():
    start_date = request.args.get("startDate", "2020-01-01")
    end_date = request.args.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    filter_waco = request.args.get("filterWaco", "false").lower() == "true"
    waco_boundary = request.args.get("wacoBoundary", "city_limits")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 8000))
    bounds = json.loads(request.args.get("bounds", "null"))

    cache_key = f"historical_data:{start_date}:{end_date}:{filter_waco}:{waco_boundary}:{page}:{per_page}:{bounds}"
    cached_data = redis_client.get(cache_key)

    if cached_data:
        return Response(cached_data, mimetype='application/json')

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
                    geojson_handler.monthly_data[month_year], bounds
                )
                filtered_features.extend(month_features)
            current_month += timedelta(days=32)
            current_month = current_month.replace(day=1)

        # Paginate the results
        start_index = (page - 1) * per_page
        end_index = start_index + per_page
        paginated_features = filtered_features[start_index:end_index]

        result = {"type": "FeatureCollection", "features": paginated_features, "total_features": len(filtered_features)}
        
        # Compress and cache the result
        compressed_data = gzip.compress(json.dumps(result).encode('utf-8'))
        redis_client.setex(cache_key, 3600, compressed_data)  # Cache for 1 hour

        return Response(compressed_data, mimetype='application/json', headers={'Content-Encoding': 'gzip'})

    except Exception as e:
        logging.error(f"Error filtering historical data: {e}")
        return jsonify({"error": "Error filtering historical data", "details": str(e)}), 500

@app.route("/live_data")
async def get_live_data():
    try:
        bouncie_data = await bouncie_api.get_latest_bouncie_data()
        if bouncie_data:
            # await websocket.broadcast(json.dumps({"type": "live_update", "data": bouncie_data}))

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
async def get_trip_metrics():
    formatted_metrics = await bouncie_api.get_trip_metrics()
    return jsonify(formatted_metrics)

@app.route("/export_gpx")
async def export_gpx():
    start_date = request.args.get("startDate", "2020-01-01")
    end_date = request.args.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    filter_waco = request.args.get("filterWaco", "false").lower() == "true"
    waco_boundary = request.args.get("wacoBoundary", "city_limits")

    try:
        gpx_data = await gpx_exporter.export_to_gpx(
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
    global is_processing
    if is_processing:
        return jsonify({"error": "Another process is already running"}), 429

    try:
        is_processing = True
        # await websocket.broadcast(json.dumps({"type": "processing_start"}))
        logging.info("Starting historical data update process")
        await geojson_handler.update_historical_data()
        logging.info("Historical data update process completed")
        return jsonify({"message": "Historical data updated successfully!"}), 200
    except Exception as e:
        logging.error(f"An error occurred during the update process: {e}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    finally:
        is_processing = False
        # await websocket.broadcast(json.dumps({"type": "processing_end"}))

@app.route('/processing_status')
async def processing_status():
    return jsonify({'isProcessing': is_processing})


if __name__ == "__main__":
    config = HyperConfig()
    config.bind = [f"0.0.0.0:{int(os.environ.get('PORT', 8080))}"]
    config.use_reloader = False

    loop = asyncio.get_event_loop()
    loop.create_task(poll_bouncie_api())
    loop.run_until_complete(serve(app, config))