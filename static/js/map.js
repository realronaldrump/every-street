// Map initialization
const map = L.map('map').setView([31.5493, -97.1117], 13); // Centered on Waco, TX

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  maxZoom: 19
}).addTo(map);

// Global variables
let wacoLimits;
let liveMarker;
let historicalDataLayer;
let liveRoutePolyline;
let playbackAnimation;
let playbackPolyline;
let playbackMarker;
let playbackSpeed = 1;
let isPlaying = false;
let currentCoordIndex = 0;
let drawnItems = new L.FeatureGroup();
map.addLayer(drawnItems);

// DOM elements
const filterWacoCheckbox = document.getElementById('filterWaco');
const startDateInput = document.getElementById('startDate');
const endDateInput = document.getElementById('endDate');
const updateDataBtn = document.getElementById('updateDataBtn');

// Playback controls
const playPauseBtn = document.getElementById('playPauseBtn');
const stopBtn = document.getElementById('stopBtn');
const playbackSpeedInput = document.getElementById('playbackSpeed');
const speedValueSpan = document.getElementById('speedValue');

// Socket.IO setup for real-time updates
const socket = io();
socket.on('live_update', (data) => {
  updateLiveData(data);
});

// Load Waco city limits
async function loadWacoLimits() {
  try {
    const response = await fetch('/static/waco_city_limits.geojson');
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
function sendLiveDataToServer(data) {
  fetch('/live_route', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(data)
  })
  .then(response => {
    if (!response.ok) {
      throw new Error('Network response was not ok');
    }
    return response.json();
  })
  .then(data => {
    console.log(data.message);
  })
  .catch(error => {
    console.error('Error sending live data to server:', error);
  });
}

// Update live data on the map and stats
function updateLiveData(data) {
  document.getElementById('lastUpdated').textContent = new Date(data.timestamp * 1000).toLocaleString();
  document.getElementById('speed').textContent = `${data.speed} mph`;

  const formattedAddress = data.address.split('<br>').join('\n');
  document.getElementById('location').textContent = formattedAddress;

  const latLng = [data.latitude, data.longitude];

  // If the liveMarker does not exist, create it
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

    // Check if there has been movement
    if (currentLatLng.lat !== latLng[0] || currentLatLng.lng !== latLng[1]) {
      liveMarker.setLatLng(latLng);

      // Update live route polyline if there's been movement
      if (!liveRoutePolyline) {
        if (!liveRoutePolyline) {
          liveRoutePolyline = L.polyline([], { color: '#007bff', weight: 4 }).addTo(map);
        }
        liveRoutePolyline.addLatLng(latLng);
      }

      // Send live data to the server to record the movement
      sendLiveDataToServer(data);
    }
  }
}

// Filter and display historical data
async function displayHistoricalData() {
  try {
    const response = await fetch('/static/historical_data.geojson')
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

// Check if route is within Waco limits
function isRouteInWaco(feature) {
  return feature.geometry.coordinates.every(coord => {
    const latlng = L.latLng(coord[1], coord[0]);
    return wacoLimits.getBounds().contains(latlng);
  });
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
    startPlayback(feature.geometry.coordinates);
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
  if (isPlaying) {
    isPlaying = false;
    playPauseBtn.textContent = 'Play';
  } else {
    isPlaying = true;
    playPauseBtn.textContent = 'Pause';
  }
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
    const liveDataResponse = await fetch('/live_data');
    const liveData = await liveDataResponse.json();
    if (!liveData.error) {
      updateLiveData(liveData);
    } else {
      console.error('Error fetching live data:', liveData.error);
    }

    const metricsResponse = await fetch('/trip_metrics');
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

// Initialize (Modified to load live route data)
(async function init() {
  await loadWacoLimits();
  await displayHistoricalData();
  await loadLiveRouteData(); // Load live route data from server
  setInterval(updateLiveDataAndMetrics, 3000); // Update every 3 seconds
})();

// Event listeners
filterWacoCheckbox.addEventListener('change', () => {
  if (filterWacoCheckbox.checked) {
    wacoLimits.addTo(map);
  } else {
    wacoLimits.remove();
  }
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

// Event listener for filter buttons
function filterRoutesBy(period) {
  const now = new Date();
  let startDate;

  switch (period) {
    case 'today':
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      break;
    case 'yesterday':
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
      break;
    case 'lastWeek':
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 7);
      break;
    case 'lastMonth':
      startDate = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate());
      break;
    case 'lastYear':
      startDate = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
      break;
    case 'allTime':
      startDate = new Date(2020, 0, 1); // Set to your earliest data date
      break;
    default:
      startDate = new Date(2020, 0, 1); // Default to all time
  }

  startDateInput.value = startDate.toISOString().slice(0, 10);
  endDateInput.value = now.toISOString().slice(0, 10); // Ensure endDate is set to today
  applyDateFilter(); // Apply the filter
}

// Function to clear the current live route playback AND the playback route
function clearLiveRoute() {
  if (liveRoutePolyline) {
    map.removeLayer(liveRoutePolyline);
    liveRoutePolyline = null;
  }

  // Clear the playback route
  if (playbackPolyline) {
    map.removeLayer(playbackPolyline);
    playbackPolyline = null;
  }

  if (playbackMarker) {
    map.removeLayer(playbackMarker);
    playbackMarker = null;
  }

  // Stop the playback animation if it's running
  stopPlayback();
}

// Add event listener to the Clear Route button
const clearRouteBtn = document.getElementById('clearRouteBtn');
clearRouteBtn.addEventListener('click', clearLiveRoute);

// Event listener for play/pause button
playPauseBtn.addEventListener('click', () => {
  togglePlayPause();
});

// Event listener for stop button
stopBtn.addEventListener('click', () => {
  stopPlayback();
});

// Event listener for playback speed adjustment
playbackSpeedInput.addEventListener('input', () => {
  adjustPlaybackSpeed();
});

// Add drawing controls to the map
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

// Event listener for when a polygon is drawn
map.on(L.Draw.Event.CREATED, (e) => {
  const layer = e.layer;
  drawnItems.addLayer(layer);
  filterHistoricalDataByPolygon(layer);
});

// Event listener for when a polygon is edited
map.on(L.Draw.Event.EDITED, (e) => {
  const layers = e.layers;
  layers.eachLayer((layer) => {
    filterHistoricalDataByPolygon(layer);
  });
});

// Event listener for when a polygon is deleted
map.on(L.Draw.Event.DELETED, (e) => {
  displayHistoricalData(); // Revert to default filtering
});

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
  displayHistoricalData(); // Revert to default filtering
}