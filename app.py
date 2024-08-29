import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
import functools
from quart import Quart, render_template, jsonify, request, Response, redirect, url_for, session
from quart_cors import cors
from hypercorn.asyncio import serve
from hypercorn.config import Config as HyperConfig
from dotenv import load_dotenv
from bouncie_api import BouncieAPI
from gpx_exporter import GPXExporter
from geopy.geocoders import Nominatim
from date_utils import parse_date, format_date, get_start_of_day, get_end_of_day, date_range, days_ago
from waco_streets_analyzer import WacoStreetsAnalyzer
import multiprocessing
from db_handler import DatabaseHandler
from sqlalchemy.exc import SQLAlchemyError


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

def login_required(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return await func(*args, **kwargs)
    return wrapper

def create_app():
    app = Quart(__name__)
    app = cors(app)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
    app.config["PIN"] = os.getenv("PIN")

    # Initialize app attributes
    app.historical_data_loaded = False
    app.historical_data_loading = False
    app.is_processing = False

    # Initialize Database Handler
    app.db_handler = DatabaseHandler()

    # Initialize API Clients and Handlers
    app.waco_analyzer = WacoStreetsAnalyzer('static/Waco-Streets.geojson')
    app.geolocator = Nominatim(user_agent="bouncie_viewer", timeout=10)
    app.bouncie_api = BouncieAPI()
    app.gpx_exporter = GPXExporter(app.db_handler)

    logger.info(f"Initialized WacoStreetsAnalyzer with {len(app.waco_analyzer.streets_gdf)} streets")

    # Asynchronous Locks
    app.historical_data_lock = asyncio.Lock()
    app.processing_lock = asyncio.Lock()
    app.live_route_lock = asyncio.Lock()
    app.progress_lock = asyncio.Lock()

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

    app.task_manager = TaskManager()

    # Routes
    @app.route('/progress')
    async def get_progress():
        async with app.progress_lock:
            try:
                coverage_analysis = await app.waco_analyzer.analyze_coverage()
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
                coverage_analysis = await app.waco_analyzer.update_progress()
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
        untraveled_streets = app.db_handler.get_waco_streets(traveled=False)
        return jsonify([street.to_dict() for street in untraveled_streets])

    @app.route("/latest_bouncie_data")
    async def get_latest_bouncie_data():
        async with app.live_route_lock:
            latest_point = app.db_handler.get_live_route(limit=1)
            if latest_point:
                return jsonify(latest_point[0].to_dict())
            return jsonify({})

    @app.route("/live_route", methods=["GET"])
    async def live_route():
        async with app.live_route_lock:
            live_route_data = app.db_handler.get_live_route()
            return jsonify([point.to_dict() for point in live_route_data])

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
            start_date = request.args.get("startDate") or "2020-01-01"
            end_date = request.args.get("endDate") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

            try:
                start_date = parse_date(start_date)
                end_date = parse_date(end_date)
                historical_data = app.db_handler.get_historical_data(start_date, end_date)
                return jsonify([data.to_dict() for data in historical_data])
            except Exception as e:
                logger.error(f"Error filtering historical data: {str(e)}", exc_info=True)
                return jsonify({"error": f"Error filtering historical data: {str(e)}"}), 500

    @app.route("/live_data")
    async def get_live_data():
        try:
            bouncie_data = await app.bouncie_api.get_latest_bouncie_data()
            if bouncie_data:
                app.db_handler.add_live_route_point(
                    timestamp=datetime.fromtimestamp(bouncie_data["timestamp"], tz=timezone.utc),
                    latitude=bouncie_data["latitude"],
                    longitude=bouncie_data["longitude"],
                    speed=bouncie_data["speed"],
                    address=bouncie_data["address"]
                )
                return jsonify(bouncie_data)
            return jsonify({"error": "No live data available"})
        except Exception as e:
            logger.error(f"An error occurred while fetching live data: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/trip_metrics")
    async def get_trip_metrics():
        formatted_metrics = await app.bouncie_api.get_trip_metrics()
        return jsonify(formatted_metrics)

    @app.route("/export_gpx")
    async def export_gpx():
        start_date = request.args.get("startDate") or "2020-01-01"
        end_date = request.args.get("endDate") or datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            gpx_data = await app.gpx_exporter.export_to_gpx(
                parse_date(start_date), parse_date(end_date)
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
            location = await asyncio.to_thread(app.geolocator.geocode, query)
            if location:
                return jsonify({
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "address": location.address
                })
            else:
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
            locations = await asyncio.to_thread(app.geolocator.geocode, query, exactly_one=False, limit=5)
            if locations:
                suggestions = [{"address": location.address} for location in locations]
                return jsonify(suggestions)
            else:
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
                await app.bouncie_api.update_historical_data(app.db_handler)
                logger.info("Historical data update process completed")
                return jsonify({"message": "Historical data updated successfully!"}), 200
            except Exception as e:
                logger.error(f"An error occurred during the update process: {e}")
                return jsonify({"error": f"An error occurred: {str(e)}"}), 500
            finally:
                app.is_processing = False

    @app.route("/add_waco_boundary", methods=["POST"])
    @login_required
    async def add_waco_boundary():
        try:
            form = await request.form
            name = form["name"]
            file = await request.files["file"]
            
            # Save the file temporarily
            temp_path = f"temp_{name}.geojson"
            await file.save(temp_path)
            
            # Add the boundary to the database
            app.db_handler.add_waco_boundary(name, temp_path)
            
            # Remove the temporary file
            os.remove(temp_path)
            
            return jsonify({"message": "Waco boundary added successfully"}), 200
        except Exception as e:
            logger.error(f"Error adding Waco boundary: {str(e)}")
            return jsonify({"error": "An error occurred while adding the Waco boundary"}), 500

    @app.route("/add_waco_streets", methods=["POST"])
    @login_required
    async def add_waco_streets():
        try:
            file = await request.files["file"]
            
            # Save the file temporarily
            temp_path = "temp_streets.geojson"
            await file.save(temp_path)
            
            # Add the streets to the database
            app.db_handler.add_waco_streets(temp_path)
            
            # Remove the temporary file
            os.remove(temp_path)
            
            return jsonify({"message": "Waco streets added successfully"}), 200
        except Exception as e:
            logger.error(f"Error adding Waco streets: {str(e)}")
            return jsonify({"error": "An error occurred while adding Waco streets"}), 500

    @app.route("/add_historical_data", methods=["POST"])
    @login_required
    async def add_historical_data():
        try:
            file = await request.files["file"]
            
            # Save the file temporarily
            temp_path = "temp_historical_data.geojson"
            await file.save(temp_path)
            
            # Add the historical data to the database
            app.db_handler.add_historical_data(temp_path)
            
            # Remove the temporary file
            os.remove(temp_path)
            
            return jsonify({"message": "Historical data added successfully"}), 200
        except Exception as e:
            logger.error(f"Error adding historical data: {str(e)}")
            return jsonify({"error": "An error occurred while adding historical data"}), 500

    @app.route("/db_stats")
    @login_required
    async def get_db_stats():
        try:
            stats = app.db_handler.get_stats()
            return jsonify(stats), 200
        except Exception as e:
            logger.error(f"Error getting database stats: {str(e)}")
            return jsonify({"error": "An error occurred while fetching database statistics"}), 500
        
    @app.route("/progress_geojson")
    async def get_progress_geojson():
        try:
            waco_boundary = request.args.get("wacoBoundary", "city_limits")
            progress_geojson = app.waco_analyzer.get_progress_geojson(waco_boundary)
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
            
            if streets_filter == "all":
                streets = app.db_handler.get_waco_streets()
            elif streets_filter == "traveled":
                streets = app.db_handler.get_waco_streets(traveled=True)
            elif streets_filter == "untraveled":
                streets = app.db_handler.get_waco_streets(traveled=False)
            else:
                return jsonify({"error": "Invalid filter parameter"}), 400

            streets_data = [street.to_dict() for street in streets]
            logging.info(f"Returning {len(streets_data)} street features")
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

                # Reset the progress in the database
                app.db_handler.reset_street_progress()

                # Recalculate the progress using all historical data
                await app.waco_analyzer.update_progress()

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
            username = form.get("username")
            password = form.get("password")
            user = app.db_handler.get_user(username)
            if user and user.password == password:
                session["authenticated"] = True
                session["username"] = username
                return redirect(url_for("index"))
            else:
                return await render_template("login.html", error="Invalid credentials. Please try again.")
        return await render_template("login.html")

    @app.route("/logout", methods=["GET", "POST"])
    async def logout():
        session.pop("authenticated", None)
        session.pop("username", None)
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    async def index():
        today = datetime.now().strftime("%Y-%m-%d")
        async with app.historical_data_lock:
            return await render_template("index.html", today=today, historical_data_loaded=app.historical_data_loaded)

    @app.route("/profile")
    @login_required
    async def profile():
        user = app.db_handler.get_user(session["username"])
        return await render_template("profile.html", user=user)

    @app.route("/update_profile", methods=["POST"])
    @login_required
    async def update_profile():
        form = await request.form
        try:
            app.db_handler.update_user(
                session["username"],
                client_id=form.get("client_id"),
                client_secret=form.get("client_secret"),
                auth_code=form.get("auth_code"),
                device_imei=form.get("device_imei"),
                vehicle_id=form.get("vehicle_id"),
                google_maps_api=form.get("google_maps_api")
            )
            return jsonify({"message": "Profile updated successfully"}), 200
        except SQLAlchemyError as e:
            logger.error(f"Database error while updating profile: {str(e)}")
            return jsonify({"error": "An error occurred while updating the profile"}), 500

    @app.route("/database")
    @login_required
    async def database():
        return await render_template("database.html")

    # Async Tasks
    async def poll_bouncie_api():
        while True:
            try:
                bouncie_data = await app.bouncie_api.get_latest_bouncie_data()
                if bouncie_data:
                    app.db_handler.add_live_route_point(
                        timestamp=datetime.fromtimestamp(bouncie_data["timestamp"], tz=timezone.utc),
                        latitude=bouncie_data["latitude"],
                        longitude=bouncie_data["longitude"],
                        speed=bouncie_data["speed"],
                        address=bouncie_data["address"]
                    )
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error fetching live data: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def load_historical_data_background():
        async with app.historical_data_lock:
            app.historical_data_loading = True
        try:
            await app.bouncie_api.update_historical_data(app.db_handler)
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

            if app.bouncie_api.client and app.bouncie_api.client.client_session:
                await app.bouncie_api.client.client_session.close()
                logger.info("Bouncie API client session closed")

        except Exception as e:
            logger.error(f"Error during shutdown: {str(e)}", exc_info=True)
        finally:
            logger.info("Shutdown complete")

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
        app = main()
        config = HyperConfig()
        config.bind = ["0.0.0.0:8080"]
        config.workers = 1
        config.startup_timeout = 36000
        logger.info("Starting Hypercorn server...")
        try:
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(handle_exception)
            await serve(app, config)
        except Exception as e:
            logger.error(f"Error starting Hypercorn server: {str(e)}", exc_info=True)
            raise
        finally:
            await app.shutdown()

    logger.info("Starting application...")
    asyncio.run(run_app())
    logger.info("Application has shut down.")

# Custom exception handler
def custom_exception_handler(exc_type, exc_value, exc_traceback):
    logger.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    import sys
    sys.exit(1)

# Set the custom exception handler
import sys
sys.excepthook = custom_exception_handler