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
let liveRoutePoints = [];
let playbackAnimation;
let playbackPolyline;
let playbackMarker;
let playbackSpeed = 1;
let isPlaying = false;
let currentCoordIndex = 0;

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
        fillColor: 'blue',
        fillOpacity: 0.1
      }
    });

    if (filterWacoCheckbox.checked) {
      wacoLimits.addTo(map);
    }
  } catch (error) {
    console.error('Error loading Waco limits:', error);
  }
}

// Update live data on the map and stats
function updateLiveData(data) {
  document.getElementById('lastUpdated').textContent = new Date(data.timestamp * 1000).toLocaleString();
  document.getElementById('speed').textContent = `${data.speed} mph`;

  const formattedAddress = data.address.split('<br>').join('\n');
  document.getElementById('location').textContent = formattedAddress;

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
    liveMarker.setLatLng(latLng);
  }

  liveRoutePoints.push(latLng);

  if (!liveRoutePolyline) {
    liveRoutePolyline = L.polyline(liveRoutePoints, {
      color: '#007bff',
      weight: 4
    }).addTo(map);
  } else {
    liveRoutePolyline.setLatLngs(liveRoutePoints);
  }

  localStorage.setItem('liveRoutePoints', JSON.stringify(liveRoutePoints));
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

  // Continue `startPlayback` function
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
  displayHistoricalData(); // This should reload the historical data based on the current filter
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

// Initialize
(async function init() {
  await loadWacoLimits();
  await displayHistoricalData();
  setInterval(updateLiveDataAndMetrics, 3000); // Update every 3 seconds

  const storedRoute = localStorage.getItem('liveRoutePoints');
  if (storedRoute) {
    liveRoutePoints = JSON.parse(storedRoute);
    if (liveRoutePoints.length > 0) {
      liveRoutePolyline = L.polyline(liveRoutePoints, {
        color: '#007bff',
        weight: 4
      }).addTo(map);
    }
  }
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