# Every Street: Tracking Davis's progess as he drives down every road in Waco

## Overview
This project is dedicated to tracking Davis's ambitious goal of driving every single street in Waco, Texas. Using a combination of the Bouncie API for real-time location data, Leaflet.js for interactive mapping, and clever JavaScript logic, we visualize Davis's progress, providing insights into his journey.

## Key Features
- **Live Location Tracking:**  Visualize Davis's current position on the map, updating every few seconds for a true "live" experience.
- **Historical Route Visualization:**  See all the streets Davis has conquered, color-coded by date range for easy analysis.
- **Waco City Limits Filter:** Toggle the display of Waco's city limits on the map to see Davis's progress within the city boundaries.
- **Interactive Date Filtering:** Explore Davis's driving history by filtering routes based on specific date ranges or predefined periods (today, yesterday, last week, etc.).
- **Trip Metrics:**  Get a quick overview of Davis's latest trip, including total distance, total time, max speed, start time, and end time.
- **Historical Data Updates:**  The application automatically checks for and downloads new driving data from Bouncie at regular intervals, ensuring the historical route data is always up-to-date.
- **Automatic GitHub Integration:**  Every time new historical data is downloaded, the changes are automatically committed and pushed to the GitHub repository, ensuring that the data and codebase are always in sync.

## Main Files & Their Roles

| File               | Role                                                                 |
|--------------------|------------------------------------------------------------------------|
| `app.py`           | The heart of the application – handles API calls, data processing, and server-side logic. |
| `templates/index.html` | The front-end structure of the web application; defines the user interface. |
| `static/js/map.js` | Manages all the Leaflet.js map interactions, including marker updates, route drawing, and data filtering.  |
| `static/css/styles.css` |  Styles the web application for a sleek and user-friendly interface. |
| `requirements.txt`  |  Lists the Python libraries required to run the application.     |
| `static/waco_city_limits.geojson` | Contains the GeoJSON data defining Waco's city limits. |
| `Procfile`         |  A Heroku-specific file used to declare how to run the web application. |
| `.env`            | Stores sensitive API keys and credentials that are loaded by the application (not included in the repository for security reasons). |

## Key Functions

**`app.py`**

- `reverse_geocode(lat, lon, retries=3)`: Asynchronously reverse geocodes latitude and longitude coordinates into a human-readable address.
- `fetch_trip_data(session, vehicle_id, date, headers)`: Fetches trip summary data from the Bouncie API for a specific vehicle and date.
- `create_geojson_features_from_trips(data)`:  Transforms raw Bouncie trip data into GeoJSON features for display on the map.
- `get_latest_bouncie_data(client)`: Retrieves the latest location, speed, battery status, and other data from the Bouncie API.
- `load_historical_data()`: Loads historical trip data from the Bouncie API and saves it to a local GeoJSON file.
- `update_historical_data()`:  Fetches new trip data since the last update, adds it to the existing historical data, saves the updated data, and pushes the changes to GitHub.
- `periodic_data_update()`: An asynchronous function that runs in the background, periodically updating the historical data.
- `get_historical_data()`: Serves the historical GeoJSON data to the front-end.
- `get_live_data()`: Retrieves and returns the latest live location data.
- `get_trip_metrics()`: Calculates and returns various metrics for the current trip.
- `format_time(seconds)`:  Helper function to format seconds into a more readable "HH:MM:SS" format. 

**`static/js/map.js`**

- `loadWacoLimits()`:  Loads and displays the Waco city limits on the map.
- `updateLiveData(data)`:  Updates the live marker position, route, and stats display with new data from the Bouncie API. 
- `displayHistoricalData()`:  Fetches, filters, and displays historical route data on the map based on the selected date range and Waco filter.
- `calculateTotalDistance(features)`: Calculates the total distance covered by an array of GeoJSON route features.
- `isRouteInWaco(feature)`: Checks if a GeoJSON route feature is entirely within the bounds of Waco city limits.
- `addRoutePopup(feature, layer)`: Adds a popup to a route on the map, displaying information about the route's date, time, and distance.
- `applyDateFilter()`: Applies the selected date filter to the historical route data.
- `updateLiveDataAndMetrics()`:  Periodically fetches and updates the live location data and trip metrics.
- `filterRoutesBy(period)`: Filters the displayed routes based on predefined time periods (today, yesterday, last week, etc.). 

## Development Guidelines

1. **Clone the repository:** `git clone https://github.com/realronaldrump/every-street.git`
2. **Install dependencies:** `pip install -r requirements.txt`
3. **Obtain Bouncie API Credentials:**
   - Register for a developer account on Bouncie's website.
   - Create a new application and obtain your `CLIENT_ID`, `CLIENT_SECRET`, and `REDIRECT_URI`.
4. **Set up Environment Variables:**
   - Create a `.env` file in the root directory.
   - Add your Bouncie credentials and any other sensitive information to the `.env` file (see `.env.example` for a template).
5. **(Optional) Set Up GitHub Integration:**
    - If you want automatic updates to your own repository, create a personal access token on GitHub with repository write permissions.
    - Add your GitHub username and personal access token to the `.env` file. 
6. **Run the Application:** `python app.py`

## External Libraries

This project leverages the following external Python libraries:
- **Flask:**  A micro web framework for building web applications.
- **aiohttp:**  An asynchronous HTTP client/server for asyncio.
- **bounciepy:** A Python wrapper for the Bouncie API (find it on PyPI).
- **geopy:**  A Python library for geocoding and reverse geocoding. 
- **python-dotenv:**  Loads environment variables from `.env` files.
- **GitPython:**  Provides access to Git repositories from within Python. 

On the front end we use:
- **Leaflet.js**: An open-source JavaScript library for interactive maps.

## Feature Roadmap

- **Street Completion Percentage:** Calculate and display the percentage of Waco streets that Davis has driven.
- **Customizable Markers and Routes:**  Allow users to personalize the map with custom marker icons and route colors.