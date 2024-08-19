// Global variables
let map, wacoLimits, liveMarker, historicalDataLayer, liveRoutePolyline, playbackAnimation, playbackPolyline, playbackMarker;
let playbackSpeed = 1;
let isPlaying = false;
let currentCoordIndex = 0;
let drawnItems;
let selectedWacoBoundary = 'city_limits'; // Default to city limits

// DOM elements
let filterWacoCheckbox, startDateInput, endDateInput, updateDataBtn, playPauseBtn, stopBtn, playbackSpeedInput, speedValueSpan, wacoBoundarySelect, clearRouteBtn, applyFilterBtn;

// Function to load Waco city limits
async function loadWacoLimits(boundaryType) {
  try {
    const filenames = {
      city_limits: '/static/city_limits.geojson',
      less_goofy: '/static/less-goofy-waco-boundary.geojson',
      goofy: '/static/goofy-waco-boundary.geojson'
    };

    const filename = filenames[boundaryType];
    if (!filename) return;

    const response = await fetch(filename);
    const data = await response.json();

    if (wacoLimits) {
      map.removeLayer(wacoLimits);
    }

    wacoLimits = L.geoJSON(data, {
      style: {
        color: 'red',
        weight: 2,
        fillColor: 'orange',
        fillOpacity: 0.03
      }
    });

    if (filterWacoCheckbox.checked) {
      wacoLimits.addTo(map);
    }
  } catch (error) {
    console.error('Error loading Waco limits:', error);
  }
}

// Function to send live data to the server
async function sendLiveDataToServer(data) {
  try {
    const response = await fetch('/live_route', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(data)
    });

    if (!response.ok) {
      throw new Error('Network response was not ok');
    }

    const responseData = await response.json();
    console.log(responseData.message);
  } catch (error) {
    console.error('Error sending live data to server:', error);
  }
}

// Update live data on the map and stats
function updateLiveData(data) {
  document.getElementById('lastUpdated').textContent = new Date(data.timestamp * 1000).toLocaleString();
  document.getElementById('speed').textContent = `${data.speed} mph`;
  document.getElementById('location').textContent = data.address.split('<br>').join('\n');

  const latLng = [data.latitude, data.longitude];

  if (!liveMarker) {
    liveMarker = L.marker(latLng, {
      icon: L.divIcon({
        className: 'blinking-marker',
        iconSize: [20, 20],
        html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
      })
    }).addTo(map);
  } else {
    const currentLatLng = liveMarker.getLatLng();

    if (currentLatLng.lat !== latLng[0] || currentLatLng.lng !== latLng[1]) {
      liveMarker.setLatLng(latLng);

      if (!liveRoutePolyline) {
        liveRoutePolyline = L.polyline([], { color: '#007bff', weight: 4 }).addTo(map);
      }
      liveRoutePolyline.addLatLng(latLng);

      sendLiveDataToServer(data);
    }
  }
}

// Filter and display historical data
async function displayHistoricalData() {
  try {
    const wacoBoundary = wacoBoundarySelect.value;
    const response = await fetch(`/historical_data?startDate=${startDateInput.value}&endDate=${endDateInput.value}&filterWaco=${filterWacoCheckbox.checked}&wacoBoundary=${wacoBoundary}`);
    const data = await response.json();

    const startDate = new Date(startDateInput.value).getTime() / 1000;
    const endDate = endDateInput.value ? new Date(endDateInput.value).getTime() / 1000 : Infinity;

    const filteredFeatures = data.features.filter(feature => {
      const timestamp = feature.properties.timestamp;
      return timestamp >= startDate && timestamp <= endDate;
    });

    const filteredGeoJSON = {
      type: "FeatureCollection",
      features: filteredFeatures
    };

    const totalDistance = calculateTotalDistance(filteredFeatures);
    document.getElementById('totalHistoricalDistance').textContent = `${totalDistance.toFixed(2)} miles`;

    if (historicalDataLayer) {
      map.removeLayer(historicalDataLayer);
    }

    historicalDataLayer = L.geoJSON(filteredGeoJSON, {
      style: {
        color: 'blue',
        weight: 2,
        opacity: 0.25
      },
      filter: feature => !filterWacoCheckbox.checked || isRouteInWaco(feature),
      onEachFeature: addRoutePopup
    }).addTo(map);
    const currentBounds = map.getBounds(); // Store current bounds
    
  // Fit the map to the bounds of the filtered data ONLY if data exists
  if (historicalDataLayer.getBounds().isValid() && historicalDataLayer.getLayers().length > 0) {
    map.fitBounds(historicalDataLayer.getBounds());
  } else {
    map.fitBounds(currentBounds); // Restore previous bounds if no data
  }

  } catch (error) {
    console.error('Error displaying historical data:', error);
  }
}

// Calculate total distance of displayed routes
function calculateTotalDistance(features) {
  return features.reduce((total, feature) => {
    const coords = feature.geometry.coordinates;
    return total + coords.reduce((routeTotal, coord, index) => {
      if (index === 0) return routeTotal;
      const prevLatLng = L.latLng(coords[index - 1][1], coords[index - 1][0]);
      const currLatLng = L.latLng(coord[1], coord[0]);
      return routeTotal + prevLatLng.distanceTo(currLatLng) * 0.000621371; // Convert meters to miles
    }, 0);
  }, 0);
}

// Modified function to check if a route is within Waco limits
function isRouteInWaco(feature) {
  if (!wacoLimits || !feature.geometry.coordinates) {
    return false;
  }

  const routeCoords = feature.geometry.coordinates.map(coord => [coord[1], coord[0]]);
  const routeLine = turf.lineString(routeCoords);
  const wacoPolygon = turf.polygon([wacoLimits.feature.geometry.coordinates[0]]); // Correctly access coordinates

  return turf.booleanContains(wacoPolygon, routeLine);
}

// Add hover popup with route information and playback button
function addRoutePopup(feature, layer) {
  const timestamp = feature.properties.timestamp;
  const date = new Date(timestamp * 1000).toLocaleDateString();
  const time = new Date(timestamp * 1000).toLocaleTimeString();
  const distance = calculateTotalDistance([feature]);

  const playbackButton = document.createElement('button');
  playbackButton.textContent = 'Play Route';
  playbackButton.addEventListener('click', () => {
    if (feature.geometry.type === 'LineString') {
      startPlayback(feature.geometry.coordinates);
    } else if (feature.geometry.type === 'MultiLineString') {
      feature.geometry.coordinates.forEach(segment => {
        startPlayback(segment);
      });
    }
  });

  const popupContent = document.createElement('div');
  popupContent.innerHTML = `Date: ${date}<br>Time: ${time}<br>Distance: ${distance.toFixed(2)} miles`;
  popupContent.appendChild(playbackButton);

  layer.bindPopup(popupContent);
}

// Function to start route playback
function startPlayback(coordinates) {
  if (playbackAnimation) {
    clearInterval(playbackAnimation);
    if (playbackPolyline) {
      map.removeLayer(playbackPolyline);
    }
    if (playbackMarker) {
      map.removeLayer(playbackMarker);
    }
  }

  currentCoordIndex = 0;
  playbackPolyline = L.polyline([], { color: 'yellow', weight: 4 }).addTo(map);

  playbackMarker = L.marker(L.latLng(coordinates[0][1], coordinates[0][0]), {
    icon: L.divIcon({
      className: 'blinking-marker',
      iconSize: [20, 20],
      html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
    })
  }).addTo(map);

  isPlaying = true;
  playPauseBtn.textContent = 'Pause';
  playbackAnimation = setInterval(() => {
    if (isPlaying && currentCoordIndex < coordinates.length) {
      const latLng = L.latLng(coordinates[currentCoordIndex][1], coordinates[currentCoordIndex][0]);
      playbackMarker.setLatLng(latLng);
      playbackPolyline.addLatLng(latLng);
      currentCoordIndex++;
    } else if (currentCoordIndex >= coordinates.length) {
      clearInterval(playbackAnimation);
      isPlaying = false;
      playPauseBtn.textContent = 'Play';
    }
  }, 100 / playbackSpeed);
}

// Function to toggle play/pause
function togglePlayPause() {
  isPlaying = !isPlaying;
  playPauseBtn.textContent = isPlaying ? 'Pause' : 'Play';
}

// Function to stop playback
function stopPlayback() {
  clearInterval(playbackAnimation);
  if (playbackPolyline) {
    map.removeLayer(playbackPolyline);
  }
  if (playbackMarker) {
    map.removeLayer(playbackMarker);
  }
  isPlaying = false;
  playPauseBtn.textContent = 'Play';
  currentCoordIndex = 0;
}

// Function to adjust playback speed
function adjustPlaybackSpeed() {
  playbackSpeed = playbackSpeedInput.value;
  speedValueSpan.textContent = `${playbackSpeed}x`;

  if (isPlaying) {
    clearInterval(playbackAnimation);
    startPlayback(playbackPolyline.getLatLngs().map(latlng => [latlng.lng, latlng.lat]));
  }
}

// Apply date filter
function applyDateFilter() {
  displayHistoricalData();
}

// Fetch and display live data periodically
async function updateLiveDataAndMetrics() {
  try {
    const [liveDataResponse, metricsResponse] = await Promise.all([
      fetch('/live_data'),
      fetch('/trip_metrics')
    ]);

    const liveData = await liveDataResponse.json();
    if (!liveData.error) {
      updateLiveData(liveData);
    } else {
      console.error('Error fetching live data:', liveData.error);
    }

    const metrics = await metricsResponse.json();
    document.getElementById('totalDistance').textContent = `${metrics.total_distance} miles`;
    document.getElementById('totalTime').textContent = metrics.total_time;
    document.getElementById('maxSpeed').textContent = `${metrics.max_speed} mph`;
    document.getElementById('startTime').textContent = metrics.start_time;
    document.getElementById('endTime').textContent = metrics.end_time;
  } catch (error) {
    console.error('Error updating live data and metrics:', error);
  }
}

// Export routes to GPX
function exportToGPX() {
  const startDate = startDateInput.value;
  const endDate = endDateInput.value || new Date().toISOString().slice(0, 10);
  const filterWaco = filterWacoCheckbox.checked ? 'true' : 'false';

  const exportUrl = `/export_gpx?startDate=${startDate}&endDate=${endDate}&filterWaco=${filterWaco}`;
  window.location.href = exportUrl;
}

// Fetch and display live route data from the server
async function loadLiveRouteData() {
  try {
    const response = await fetch('/live_route');
    const data = await response.json();

    if (data.features.length > 0) {
      const coordinates = data.features.map(feature => feature.geometry.coordinates);
      liveRoutePolyline = L.polyline(coordinates.map(coord => [coord[1], coord[0]]), {
        color: '#007bff',
        weight: 4
      }).addTo(map);

      const lastCoord = coordinates[coordinates.length - 1];
      liveMarker = L.marker([lastCoord[1], lastCoord[0]], {
        icon: L.divIcon({
          className: 'blinking-marker',
          iconSize: [20, 20],
          html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
        })
      }).addTo(map);
    }
  } catch (error) {
    console.error('Error loading live route data:', error);
  }
}

// Function to filter routes by a specific period
function filterRoutesBy(period) {
  const now = new Date();
  let startDate;

  const periodMap = {
    today: 0,
    yesterday: -1,
    lastWeek: -7,
    lastMonth: -30,
    lastYear: -365,
    allTime: new Date(2020, 0, 1)
  };

  startDate = periodMap[period] instanceof Date
    ? periodMap[period]
    : new Date(now.getFullYear(), now.getMonth(), now.getDate() + periodMap[period]);

  startDateInput.value = startDate.toISOString().slice(0, 10);
  endDateInput.value = now.toISOString().slice(0, 10);
  applyDateFilter();
}

// Function to clear the current live route playback AND the playback route
function clearLiveRoute() {
  [liveRoutePolyline, playbackPolyline, playbackMarker].forEach(layer => {
    if (layer) {
      map.removeLayer(layer);
      layer = null;
    }
  });

  stopPlayback();
}

// Function to filter historical data by a drawn polygon
async function filterHistoricalDataByPolygon(polygon) {
  try {
    const response = await fetch('/static/historical_data.geojson');
    const data = await response.json();

    const filteredFeatures = data.features.filter(feature => {
      return feature.geometry.coordinates.some(coord => {
        const point = L.latLng(coord[1], coord[0]);
        return polygon.getBounds().contains(point);
      });
    });

    const filteredGeoJSON = {
      type: "FeatureCollection",
      features: filteredFeatures
    };

    if (historicalDataLayer) {
      map.removeLayer(historicalDataLayer);
    }

    historicalDataLayer = L.geoJSON(filteredGeoJSON, {
      style: {
        color: 'blue',
        weight: 2,
        opacity: 0.25
      },
      onEachFeature: addRoutePopup
    }).addTo(map);

  } catch (error) {
    console.error('Error filtering historical data by polygon:', error);
  }
}

// Function to clear all drawn shapes
function clearDrawnShapes() {
  drawnItems.clearLayers();
  displayHistoricalData();
}

// Initialize map and related features
function initializeMap() {
  map = L.map('map').setView([31.5493, -97.1117], 13); // Centered on Waco, TX

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 19
  }).addTo(map);

  drawnItems = new L.FeatureGroup();
  map.addLayer(drawnItems);

  const drawControl = new L.Control.Draw({
    draw: {
      polyline: false,
      polygon: true,
      circle: false,
      rectangle: false,
      marker: false,
      circlemarker: false
    },
    edit: {
      featureGroup: drawnItems
    }
  });
  map.addControl(drawControl);

  // Event listeners for draw control
  map.on(L.Draw.Event.CREATED, (e) => {
    const layer = e.layer;
    drawnItems.addLayer(layer);
    filterHistoricalDataByPolygon(layer);
  });

  map.on(L.Draw.Event.EDITED, (e) => {
    const layers = e.layers;
    layers.eachLayer((layer) => {
      filterHistoricalDataByPolygon(layer);
    });
  });

  map.on(L.Draw.Event.DELETED, () => {
    displayHistoricalData(); // Revert to default filtering
  });
}

// Initialize Socket.IO for real-time updates
function initializeSocketIO() {
  const socket = io();
  socket.on('live_update', (data) => {
    updateLiveData(data);
  });
}

// Initialize the application
async function initializeApp() {
  await loadWacoLimits(selectedWacoBoundary);
  await displayHistoricalData();
  await loadLiveRouteData();
  setInterval(updateLiveDataAndMetrics, 3000);
}

// Modified function to set up event listeners
function setupEventListeners() {
  applyFilterBtn.addEventListener('click', async () => {
    await loadWacoLimits(wacoBoundarySelect.value);
    displayHistoricalData();
  });

  updateDataBtn.addEventListener('click', async () => {
    try {
      updateDataBtn.disabled = true;
      updateDataBtn.textContent = "Updating...";

      const response = await fetch('/update_historical_data');
      const data = await response.json();

      if (response.ok) {
        console.log(data.message);
        await displayHistoricalData();
      } else {
        console.error(data.error);
      }
    } catch (error) {
      console.error('Error updating historical data:', error);
    } finally {
      updateDataBtn.disabled = false;
      updateDataBtn.textContent = "Check for new driving data";
    }
  });

  wacoBoundarySelect.addEventListener('change', () => {
    selectedWacoBoundary = wacoBoundarySelect.value;
  });

  clearRouteBtn.addEventListener('click', clearLiveRoute);
  playPauseBtn.addEventListener('click', togglePlayPause);
  stopBtn.addEventListener('click', stopPlayback);
  playbackSpeedInput.addEventListener('input', adjustPlaybackSpeed);
}

// Modified DOMContentLoaded event listener
document.addEventListener('DOMContentLoaded', function() {
  // Initialize DOM elements
  filterWacoCheckbox = document.getElementById('filterWaco');
  startDateInput = document.getElementById('startDate');
  endDateInput = document.getElementById('endDate');
  updateDataBtn = document.getElementById('updateDataBtn');
  playPauseBtn = document.getElementById('playPauseBtn');
  stopBtn = document.getElementById('stopBtn');
  playbackSpeedInput = document.getElementById('playbackSpeed');
  speedValueSpan = document.getElementById('speedValue');
  wacoBoundarySelect = document.getElementById('wacoBoundarySelect');
  clearRouteBtn = document.getElementById('clearRouteBtn');
  applyFilterBtn = document.getElementById('applyFilterBtn');

  // Initialize map and features
  initializeMap();
  initializeSocketIO();
  setupEventListeners();
  initializeApp();
});