import os
import asyncio
import logging
import json
from datetime import datetime, timezone
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
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "your_secret_key")
socketio = SocketIO(app)

# Initialize helper classes
bouncie_api = BouncieAPI()
geojson_handler = GeoJSONHandler()
gpx_exporter = GPXExporter()

# Load historical data on startup
asyncio.run(geojson_handler.load_historical_data())

LIVE_ROUTE_DATA_FILE = "live_route_data.geojson"

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

@app.route("/")
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

@app.route("/live_data")
def get_live_data():
    loop = asyncio.get_event_loop()
    try:
        bouncie_data = loop.run_until_complete(bouncie_api.get_latest_bouncie_data())
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
            return jsonify({"error": "No data found for the specified date range"}), 404
        
        return Response(
            gpx_data,
            mimetype="application/gpx+xml",
            headers={"Content-Disposition": "attachment;filename=export.gpx"},
        )
    except Exception as e:
        logging.error(f"Error in export_gpx: {str(e)}")
        return jsonify({"error": f"An error occurred while exporting GPX: {str(e)}"}), 500

@app.route("/update_historical_data")
def update_historical_data():
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(geojson_handler.update_historical_data())
        return jsonify({"message": "Historical data updated successfully!"}), 200
    except Exception as e:
        logging.error(f"An error occurred during the update process: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(poll_bouncie_api())
    try:
        socketio.run(app, debug=os.environ.get("DEBUG"), host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), use_reloader=False)
    finally:
        loop.close()
