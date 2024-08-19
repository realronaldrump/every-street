import os
import asyncio
import logging
import json
from datetime import datetime, timezone
from json import JSONDecodeError

from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
from dotenv import load_dotenv

from bouncie_api import BouncieAPI
from geojson_handler import GeoJSONHandler
from gpx_exporter import GPXExporter
from shapely.geometry import Polygon, LineString

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = "your_secret_key"  # Replace with a secure secret key
socketio = SocketIO(app)

# Initialize helper classes
bouncie_api = BouncieAPI()
geojson_handler = GeoJSONHandler()
gpx_exporter = GPXExporter()

# Load historical data on startup
asyncio.run(geojson_handler.load_historical_data())

# --- Live Route Data Handling ---
LIVE_ROUTE_DATA_FILE = "live_route_data.json"


def load_live_route_data():
    try:
        with open(LIVE_ROUTE_DATA_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"type": "FeatureCollection", "features": []}


def save_live_route_data(data):
    with open(LIVE_ROUTE_DATA_FILE, "w") as f:
        json.dump(data, f)


live_route_data = load_live_route_data()


@app.route("/live_route", methods=["GET", "POST"])
def live_route():
    global live_route_data
    if request.method == "POST":
        new_point = request.get_json()
        new_coordinates = [new_point["longitude"], new_point["latitude"]]
        new_timestamp = new_point["timestamp"]

        # Check if there is at least one feature already
        if live_route_data["features"]:
            last_feature = live_route_data["features"][-1]
            last_coordinates = last_feature["geometry"]["coordinates"]
            last_timestamp = last_feature["properties"]["timestamp"]

            # Only add the new point if coordinates or timestamp have changed
            if new_coordinates == last_coordinates and new_timestamp == last_timestamp:
                return jsonify({"message": "No change detected, point not added"})

        # If coordinates or timestamp are different, add the new point
        feature = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": new_coordinates},
            "properties": {"timestamp": new_timestamp},
        }
        live_route_data["features"].append(feature)
        save_live_route_data(live_route_data)
        return jsonify({"message": "Point added to live route"})
    else:
        return jsonify(live_route_data)


# Route to serve the index page
@app.route("/")
def index():
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template("index.html", today=today)


# Helper function to load Waco boundary
def load_waco_boundary(boundary_name):
    filenames = {
        "city_limits": "city_limits.geojson",
        "less_goofy": "less_goofy.geojson",  # Corrected filename
        "goofy": "goofy.geojson",  # Corrected filename
    }
    filename = filenames.get(boundary_name)
    if filename:
        with open(f"static/{filename}") as f:
            waco_limits_data = json.load(f)
            return waco_limits_data["features"][0]["geometry"]["coordinates"][0]
    else:
        logging.error(f"Invalid wacoBoundary value: {boundary_name}")
        return None


# Route to get filtered historical data
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
            waco_limits = load_waco_boundary(waco_boundary)

        filtered_features = geojson_handler.filter_geojson_features(
            start_date, end_date, filter_waco, waco_limits
        )

        # Ensure a valid GeoJSON response even if no features are found
        if filtered_features is None:
            filtered_features = []

        return jsonify({"type": "FeatureCollection", "features": filtered_features})

    except Exception as e:
        logging.error(f"Error filtering historical data: {e}")
        return (
            jsonify({"error": "Error filtering historical data", "details": str(e)}),
            500,
        )


# Route to get live data
@app.route("/live_data")
def get_live_data():
    loop = asyncio.get_event_loop()
    try:
        bouncie_data = loop.run_until_complete(bouncie_api.get_latest_bouncie_data())
        if bouncie_data:
            socketio.emit("live_update", bouncie_data)

            # Update live_route_data
            global live_route_data
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


# Route to get trip metrics
@app.route("/trip_metrics")
def get_trip_metrics():
    formatted_metrics = bouncie_api.get_trip_metrics()
    return jsonify(formatted_metrics)


# Route to export data to GPX format
@app.route("/export_gpx")
def export_gpx():
    start_date = request.args.get("startDate", "2020-01-01")
    end_date = request.args.get("endDate")
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")  # Default to current date
    filter_waco = request.args.get("filterWaco", "false").lower() == "true"
    waco_boundary = request.args.get("wacoBoundary", "city_limits")

    gpx_data = gpx_exporter.export_to_gpx(
        start_date, end_date, filter_waco, waco_boundary
    )
    return Response(
        gpx_data,
        mimetype="application/gpx+xml",
        headers={"Content-Disposition": "attachment;filename=export.gpx"},
    )


# Update historical data and push changes to GitHub
@app.route("/update_historical_data")
def update_historical_data():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(geojson_handler.update_historical_data())
        return jsonify({"message": "Historical data updated successfully!"}), 200
    except Exception as e:
        logging.error(f"An error occurred during the update process: {e}")
        return jsonify({"error": str(e)}), 500


# Periodic update for historical data
async def periodic_data_update():
    while True:
        try:
            await geojson_handler.update_historical_data()
            logging.info("Historical data updated successfully via periodic update.")
        except Exception as e:
            logging.error(f"An error occurred during periodic update: {e}")

        await asyncio.sleep(3600)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.create_task(periodic_data_update())
        socketio.run(
            app,
            debug=os.environ.get("DEBUG"),
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 8080)),
            use_reloader=False,
        )
    finally:
        loop.close()