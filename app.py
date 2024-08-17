import os
import asyncio
import logging
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
from dotenv import load_dotenv

from bouncie_api import BouncieAPI
from geojson_handler import GeoJSONHandler
from gpx_exporter import GPXExporter

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'  # Replace with a secure secret key
socketio = SocketIO(app)

# Initialize helper classes
bouncie_api = BouncieAPI()
geojson_handler = GeoJSONHandler()
gpx_exporter = GPXExporter()

# Load historical data on startup
asyncio.run(geojson_handler.load_historical_data())


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

    filtered_features = geojson_handler.filter_geojson_features(
        start_date, end_date, filter_waco
    )
    return jsonify({"type": "FeatureCollection", "features": filtered_features})


# Route to get live data
@app.route("/live_data")
async def get_live_data():
    bouncie_data = await bouncie_api.get_latest_bouncie_data()
    if bouncie_data:
        socketio.emit("live_update", bouncie_data)
        return jsonify(bouncie_data)
    return jsonify({"error": "No live data available"})


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

    gpx_data = gpx_exporter.export_to_gpx(start_date, end_date, filter_waco)
    return Response(
        gpx_data,
        mimetype="application/gpx+xml",
        headers={"Content-Disposition": "attachment;filename=export.gpx"},
    )


# Update historical data and push changes to GitHub
@app.route("/update_historical_data")
async def update_historical_data():
    try:
        await geojson_handler.update_historical_data()
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
    loop.create_task(periodic_data_update())
    socketio.run(
        app, debug=False, host="0.0.0.0", port=int(os.environ.get("PORT", 8080))
    )