import asyncio
import functools
import json
import logging
import multiprocessing
import os
import sys
from datetime import date, datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Optional

import geopandas as gpd
from geopy.geocoders import Nominatim
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings
from quart import (Quart, Response, jsonify, redirect, render_template,
                   request, session, url_for)
from quart_cors import cors

from bouncie_api import BouncieAPI
from date_utils import date_range, format_date, timedelta
from geojson_handler import GeoJSONHandler
from gpx_exporter import GPXExporter
from waco_streets_analyzer import WacoStreetsAnalyzer

# Set up logging
LOG_DIRECTORY = "logs"
os.makedirs(LOG_DIRECTORY, exist_ok=True)
log_file = os.path.join(LOG_DIRECTORY, "app.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(log_file, maxBytes=10485760, backupCount=5),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def debug_log(message):
    if config.DEBUG:
        logger.debug(message)

def login_required(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return await func(*args, **kwargs)
    return wrapper

class DateRange(BaseModel):
    start_date: date = Field(..., description="Start date of the range")
    end_date: date = Field(..., description="End date of the range")

    @field_validator('end_date')
    def end_date_must_be_after_start_date(cls, v: date, info: ValidationInfo) -> date:
        start_date = info.data.get('start_date')
        if start_date and v < start_date:
            raise ValueError('end_date must be after start_date')
        return v

class HistoricalDataParams(BaseModel):
    date_range: DateRange
    filter_waco: bool = Field(False, description="Whether to filter data to Waco area")
    waco_boundary: str = Field("city_limits", description="Type of Waco boundary to use")
    bounds: Optional[list] = Field(None, description="Bounding box for filtering data")

    @field_validator('bounds')
    def validate_bounds(cls, v: Optional[list], info: ValidationInfo) -> Optional[list]:
        if v is not None:
            if len(v) != 4:
                raise ValueError('bounds must be a list of 4 float values')
            if not all(isinstance(x, (int, float)) for x in v):
                raise ValueError('all values in bounds must be numbers')
        return v

class Config(BaseSettings):
    PIN: str
    CLIENT_ID: str
    CLIENT_SECRET: str
    REDIRECT_URI: str
    AUTH_CODE: str
    VEHICLE_ID: str
    DEVICE_IMEI: str
    ENABLE_GEOCODING: bool = False
    GOOGLE_MAPS_API: str
    REDIS_URL: str
    DEBUG: bool = False
    ANTHROPIC_API_KEY: str
    OPENAI_API_KEY: str
    USERNAME: str
    PASSWORD: str
    SECRET_KEY: str  

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        case_sensitive = False
        DEBUG: bool = False

config = Config()

# Global instances initialized in create_app
waco_analyzer = WacoStreetsAnalyzer('static/Waco-Streets.geojson')
geojson_handler = GeoJSONHandler(waco_analyzer)
geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)
bouncie_api = BouncieAPI()
gpx_exporter = GPXExporter(geojson_handler)

logger.info(f"Initialized WacoStreetsAnalyzer with {len(waco_analyzer.streets_gdf)} streets")

# Live Route Data File
LIVE_ROUTE_DATA_FILE = "live_route_data.geojson"

# Helper functions
def load_live_route_data():
    try:
        with open(LIVE_ROUTE_DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"File not found: {LIVE_ROUTE_DATA_FILE}. Creating an empty GeoJSON.")
        empty_geojson = {"type": "FeatureCollection", "features": []}
        save_live_route_data(empty_geojson)
        return empty_geojson
    except json.JSONDecodeError:
        logger.error(f"Error decoding JSON from {LIVE_ROUTE_DATA_FILE}. File may be corrupted.")
        return {"type": "FeatureCollection", "features": []}

def save_live_route_data(data):
    with open(LIVE_ROUTE_DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

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
        self.tasks.clear()

def create_app():
    app = cors(Quart(__name__))
    app.config.from_mapping({k: v for k, v in config.dict().items() if k not in ['Config']})
    app.secret_key = config.SECRET_KEY 
    app.config['SESSION_TYPE'] = 'filesystem'
    debug_log(f"App configuration: {app.config}")

    # Initialize app attributes
    app.historical_data_loaded = False
    app.historical_data_loading = False
    app.is_processing = False
    app.task_manager = TaskManager()
    app.live_route_data = load_live_route_data()

    # Asynchronous Locks
    app.historical_data_lock = asyncio.Lock()
    app.processing_lock = asyncio.Lock()
    app.live_route_lock = asyncio.Lock()
    app.progress_lock = asyncio.Lock()

    # Routes
    @app.route('/progress')
    async def get_progress():
        async with app.progress_lock:
            try:
                coverage_analysis = await geojson_handler.update_waco_streets_progress()
                if coverage_analysis is None:
                    raise ValueError("Failed to update Waco streets progress")
                logging.info(f"Progress update: {coverage_analysis}")
                return jsonify({
                    "total_streets": int(coverage_analysis["total_streets"]),
                    "traveled_streets": int(coverage_analysis["traveled_streets"]),
                    "coverage_percentage": float(coverage_analysis["coverage_percentage"])
                })
            except Exception as e:
                logging.error(f"Error in get_progress: {str(e)}", exc_info=True)
                return jsonify({"error": str(e)}), 500

    @app.route("/update_progress", methods=["POST"])
    async def update_progress():
        async with app.progress_lock:
            try:
                coverage_analysis = await geojson_handler.update_all_progress()
                return jsonify({
                    "total_streets": int(coverage_analysis["total_streets"]),
                    "traveled_streets": int(coverage_analysis["traveled_streets"]),
                    "coverage_percentage": float(coverage_analysis["coverage_percentage"])
                }), 200
            except Exception as e:
                logger.error(f"Error updating progress: {str(e)}", exc_info=True)
                return jsonify({"error": f"Error updating progress: {str(e)}"}), 500

    @app.route('/untraveled_streets')
    async def get_untraveled_streets():
        waco_boundary = request.args.get("wacoBoundary", "city_limits")
        untraveled_streets = geojson_handler.get_untraveled_streets(waco_boundary)
        return jsonify(json.loads(untraveled_streets))

    @app.route("/latest_bouncie_data")
    async def get_latest_bouncie_data():
        async with app.live_route_lock:
            return jsonify(getattr(app, 'latest_bouncie_data', {}))

    @app.route("/live_route", methods=["GET"])
    async def live_route():
        async with app.live_route_lock:
            return jsonify(app.live_route_data)

    @app.route("/historical_data_status")
    async def historical_data_status():
        async with app.historical_data_lock:
            return jsonify({
                "loaded": app.historical_data_loaded,
                "loading": app.historical_data_loading
            })

    @app.route("/historical_data")
    async def get_historical_data():
        async with app.historical_data_lock:
            try:
                params = HistoricalDataParams(
                    date_range=DateRange(
                        start_date=request.args.get("startDate") or "2020-01-01",
                        end_date=request.args.get("endDate") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    ),
                    filter_waco=request.args.get("filterWaco", "false").lower() == "true",
                    waco_boundary=request.args.get("wacoBoundary", "city_limits"),
                    bounds=[float(x) for x in request.args.get("bounds", "").split(",")] if request.args.get("bounds") else None
                )

                logger.info(f"Received request for historical data: {params}")

                waco_limits = None
                if params.filter_waco and params.waco_boundary != "none":
                    waco_limits = geojson_handler.load_waco_boundary(params.waco_boundary)

                filtered_features = await geojson_handler.filter_geojson_features(
                    params.date_range.start_date.isoformat(),
                    params.date_range.end_date.isoformat(),
                    params.filter_waco,
                    waco_limits,
                    bounds=params.bounds
                )

                result = {
                    "type": "FeatureCollection",
                    "features": filtered_features,
                    "total_features": len(filtered_features)
                }

                return jsonify(result)

            except ValueError as e:
                logger.error(f"Error parsing parameters: {str(e)}")
                return jsonify({"error": f"Invalid parameter: {str(e)}"}), 400
            except Exception as e:
                logger.error(f"Error filtering historical data: {str(e)}", exc_info=True)
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
                async with app.live_route_lock:
                    if not app.live_route_data or 'features' not in app.live_route_data or not app.live_route_data['features']:
                        app.live_route_data = {"type": "FeatureCollection", "features": []}

                    if not app.live_route_data["features"]:
                        app.live_route_data["features"].append({
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": []
                            },
                            "properties": {}
                        })

                    live_route_feature = app.live_route_data["features"][0]

                    new_coord = [bouncie_data["longitude"], bouncie_data["latitude"]]

                    if not live_route_feature["geometry"]["coordinates"] or new_coord != live_route_feature["geometry"]["coordinates"][-1]:
                        live_route_feature["geometry"]["coordinates"].append(new_coord)
                        save_live_route_data(app.live_route_data)
                        app.latest_bouncie_data = bouncie_data
                    else:
                        logger.debug("Duplicate point detected, not adding to live route")

                return jsonify(bouncie_data)
            return jsonify({"error": "No live data available"})
        except Exception as e:
            logger.error(f"An error occurred while fetching live data: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/trip_metrics")
    async def get_trip_metrics():
        formatted_metrics = await bouncie_api.get_trip_metrics()
        return jsonify(formatted_metrics)

    @app.route("/export_gpx")
    async def export_gpx():
        start_date = request.args.get("startDate") or "2020-01-01"
        end_date = request.args.get("endDate") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filter_waco = request.args.get("filterWaco", "false").lower() == "true"
        waco_boundary = request.args.get("wacoBoundary", "city_limits")

        try:
            gpx_data = await gpx_exporter.export_to_gpx(
                format_date(start_date), format_date(end_date), filter_waco, waco_boundary
            )

            if gpx_data is None:
                logger.warning("No data found for GPX export")
                return jsonify({"error": "No data found for the specified date range"}), 404

            return Response(
                gpx_data,
                mimetype="application/gpx+xml",
                headers={"Content-Disposition": "attachment;filename=export.gpx"},
            )
        except Exception as e:
            logger.error(f"Error in export_gpx: {str(e)}", exc_info=True)
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
            return jsonify({"error": "Location not found"}), 404
        except Exception as e:
            logger.error(f"Error during location search: {e}")
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
            return jsonify([])
        except Exception as e:
            logger.error(f"Error during location search: {e}")
            return jsonify({"error": "An error occurred during the search"}), 500

    @app.route("/update_historical_data", methods=["POST"])
    async def update_historical_data():
        async with app.processing_lock:
            if app.is_processing:
                return jsonify({"error": "Another process is already running"}), 429

            try:
                app.is_processing = True
                logger.info("Starting historical data update process")
                await geojson_handler.update_historical_data(fetch_all=True)
                logger.info("Historical data update process completed")
                return jsonify({"message": "Historical data updated successfully!"}), 200
            except Exception as e:
                logger.error(f"An error occurred during the update process: {e}")
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
            logger.error(f"Error getting progress GeoJSON: {str(e)}", exc_info=True)
            return jsonify({"error": f"Error getting progress GeoJSON: {str(e)}"}), 500

    @app.route('/processing_status')
    async def processing_status():
        async with app.processing_lock:
            return jsonify({'isProcessing': app.is_processing})

    @app.route('/waco_streets')
    async def get_waco_streets():
        try:
            waco_boundary = request.args.get("wacoBoundary", "city_limits")
            streets_filter = request.args.get("filter", "all")
            logging.info(f"Fetching Waco streets: boundary={waco_boundary}, filter={streets_filter}")
            streets_geojson = geojson_handler.get_waco_streets(waco_boundary, streets_filter)
            streets_data = json.loads(streets_geojson)
            logging.info(f"Returning {len(streets_data['features'])} street features")
            return jsonify(streets_data)
        except Exception as e:
            logging.error(f"Error in get_waco_streets: {str(e)}", exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/reset_progress", methods=["POST"])
    @login_required
    async def reset_progress():
        async with app.processing_lock:
            if app.is_processing:
                return jsonify({"error": "Another process is already running"}), 429

            try:
                app.is_processing = True
                logger.info("Starting progress reset process")

                # Reset the progress in the WacoStreetsAnalyzer
                waco_analyzer.reset_progress()

                # Recalculate the progress using all historical data
                await geojson_handler.update_all_progress()

                logger.info("Progress reset and recalculated successfully")
                return jsonify({"message": "Progress has been reset and recalculated successfully!"}), 200
            except Exception as e:
                logger.error(f"An error occurred during the progress reset process: {e}")
                return jsonify({"error": f"An error occurred: {str(e)}"}), 500
            finally:
                app.is_processing = False

    @app.route("/login", methods=["GET", "POST"])
    async def login():
        if request.method == "POST":
            form = await request.form
            pin = form.get("pin")
            if pin == app.config["PIN"]:
                session["authenticated"] = True
                return redirect(url_for("index"))
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
        
        # Calculate the start date for the last month
        last_month_start = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
        
        async with app.historical_data_lock:
            return await render_template(
                "index.html", 
                today=today, 
                historical_data_loaded=app.historical_data_loaded,
                last_month_start=last_month_start.strftime("%Y-%m-%d"),
                debug=config.DEBUG
            )

    # Async Tasks
    async def poll_bouncie_api():
        while True:
            try:
                bouncie_data = await bouncie_api.get_latest_bouncie_data()
                if bouncie_data:
                    async with app.live_route_lock:
                        if "features" not in app.live_route_data:
                            app.live_route_data["features"] = []

                        if not app.live_route_data["features"]:
                            app.live_route_data["features"].append({
                                "type": "Feature",
                                "geometry": {
                                    "type": "LineString",
                                    "coordinates": []
                                },
                                "properties": {}
                            })

                        live_route_feature = app.live_route_data["features"][0]

                        new_coord = [bouncie_data["longitude"], bouncie_data["latitude"]]

                        if not live_route_feature["geometry"]["coordinates"] or new_coord != live_route_feature["geometry"]["coordinates"][-1]:
                            live_route_feature["geometry"]["coordinates"].append(new_coord)
                            save_live_route_data(app.live_route_data)
                            app.latest_bouncie_data = bouncie_data
                        else:
                            logger.debug("Duplicate point detected, not adding to live route")

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error fetching live data: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def load_historical_data_background():
        async with app.historical_data_lock:
            app.historical_data_loading = True
        try:
            logger.info("Starting historical data load")
            await geojson_handler.load_historical_data()
            async with app.historical_data_lock:
                app.historical_data_loaded = True
            logger.info("Historical data loaded successfully")
        except Exception as e:
            logger.error(f"Error loading historical data: {str(e)}", exc_info=True)
        finally:
            async with app.historical_data_lock:
                app.historical_data_loading = False

    # App Lifecycle Events
    @app.before_serving
    async def startup():
        logger.info("Starting application initialization...")
        try:
            logger.info("Initializing historical data...")
            await load_historical_data_background()
            logger.info("Historical data initialized")

            if not hasattr(app, 'background_tasks_started'):
                app.task_manager.add_task(poll_bouncie_api())
                app.background_tasks_started = True
                logger.debug("Bouncie API polling task added")

            logger.debug(f"Available routes: {app.url_map}")
            logger.info("Application initialization complete")
        except Exception as e:
            logger.error(f"Error during startup: {str(e)}", exc_info=True)
            raise

    @app.after_serving
    async def shutdown():
        logger.info("Shutting down application...")
        try:
            await app.task_manager.cancel_all()
            logger.info("All tasks cancelled")

            if bouncie_api.client and bouncie_api.client.client_session:
                await bouncie_api.client.client_session.close()
                logger.info("Bouncie API client session closed")

            if geojson_handler.bouncie_api.client and geojson_handler.bouncie_api.client.client_session:
                await geojson_handler.bouncie_api.client.client_session.close()
                logger.info("GeoJSON handler Bouncie API client session closed")

        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}", exc_info=True)
        finally:
            logger.info("Shutdown complete")

    debug_log("App creation completed")
    return app

# Error Handler
def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    logger.error(f"Caught exception: {msg}")
    logger.info("Initiating shutdown due to exception...")
    asyncio.create_task(shutdown_app(loop))

async def shutdown_app(loop):
    logger.info("Shutting down due to exception...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

app = create_app()

# Main function
def main():
    return app

if __name__ == "__main__":
    multiprocessing.freeze_support()
    multiprocessing.set_start_method('spawn')

    async def run_app():
        app_local = main()
        config_local = HyperConfig()
        config_local.bind = ["0.0.0.0:8080"]
        config_local.workers = 1
        config_local.startup_timeout = 36000
        logger.info("Starting Hypercorn server...")
        try:
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(handle_exception)
            await serve(app_local, config_local)
        except Exception as e:
            logger.error(f"Error starting Hypercorn server: {str(e)}", exc_info=True)
            raise
        finally:
            await app_local.shutdown()

    logger.info("Starting application...")
    asyncio.run(run_app())
    logger.info("Application has shut down.")

# Custom exception handler
def custom_exception_handler(exc_type, exc_value, exc_traceback):
    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    sys.exit(1)

# Set the custom exception handler
sys.excepthook = custom_exception_handler