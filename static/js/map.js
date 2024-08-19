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
  if (!filterWacoCheckbox.checked) {
    if (wacoLimits) {
      map.removeLayer(wacoLimits);
      wacoLimits = null;
    }
    return; // Exit if no filter is applied
  }

  const filenames = {
    city_limits: '/static/city_limits.geojson',
    less_goofy: '/static/less-goofy-waco-boundary.geojson',
    goofy: '/static/goofy-waco-boundary.geojson'
  };

  try {
    const response = await fetch(filenames[boundaryType]);
    const data = await response.json();

    if (wacoLimits) {
      map.removeLayer(wacoLimits);
    }

    wacoLimits = L.geoJSON(data, {
      style: { color: 'red', weight: 2, fillColor: 'orange', fillOpacity: 0.03 }
    }).addTo(map);

  } catch (error) {
    console.error('Error loading Waco limits:', error.message);
  }
}

// Function to clear the live route
function clearLiveRoute() {
  if (liveRoutePolyline) {
    map.removeLayer(liveRoutePolyline);
    liveRoutePolyline = null;
  }
  if (liveMarker) {
    map.removeLayer(liveMarker);
    liveMarker = null;
  }
}

// Function to load live route data
async function loadLiveRouteData() {
  try {
    const response = await fetch('/live_route');
    const data = await response.json();
    updateLiveData(data);
  } catch (error) {
    console.error('Error loading live route data:', error);
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

  if (data.address) {
    document.getElementById('location').textContent = data.address.split('<br>').join('\n');
  } else {
    document.getElementById('location').textContent = 'Address not available';
  }

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

// Function to display historical data
async function displayHistoricalData() {
  try {
    const wacoBoundary = wacoBoundarySelect.value;

    if (!filterWacoCheckbox.checked) {
      if (historicalDataLayer) {
        map.removeLayer(historicalDataLayer);
        historicalDataLayer = null;
      }
      if (wacoLimits) {
        map.removeLayer(wacoLimits);
        wacoLimits = null;
      }
      return; // Exit if no filter is applied
    }

    const response = await fetch(`/historical_data?startDate=${startDateInput.value}&endDate=${endDateInput.value}&filterWaco=${filterWacoCheckbox.checked}&wacoBoundary=${wacoBoundary}`);
    const data = await response.json();

    if (!data || !data.features || data.features.length === 0) {
      console.error("Invalid or empty GeoJSON data.");
      return;
    }

    const startDate = new Date(startDateInput.value).getTime() / 1000;
    const endDate = endDateInput.value ? new Date(endDateInput.value).getTime() / 1000 : Infinity;

    let filteredFeatures = data.features.filter(feature => {
      const timestamp = feature.properties.timestamp;
      return timestamp >= startDate && timestamp <= endDate;
    });

    if (filterWacoCheckbox.checked) {
      filteredFeatures = filteredFeatures.map(feature => {
        const clippedFeature = clipRouteToWacoBoundary(feature);
        return clippedFeature ? clippedFeature : null;
      }).filter(feature => feature !== null);
    }

    const filteredGeoJSON = {
      type: "FeatureCollection",
      features: filteredFeatures
    };

    console.log('Filtered GeoJSON:', filteredGeoJSON);

    const totalDistance = calculateTotalDistance(filteredFeatures);
    document.getElementById('totalHistoricalDistance').textContent = `${totalDistance.toFixed(2)} miles`;

    if (historicalDataLayer) {
      map.removeLayer(historicalDataLayer);
    }

    if (filteredFeatures.length === 0) {
      console.log('No data available for the selected filter');
      return;
    }

    historicalDataLayer = L.geoJSON(filteredGeoJSON, {
      style: {
        color: 'blue',
        weight: 2,
        opacity: 0.25
      },
      onEachFeature: addRoutePopup
    }).addTo(map);

    const layerBounds = historicalDataLayer.getBounds();
    if (layerBounds.isValid()) {
      console.log('Fitting bounds to:', layerBounds);
      map.fitBounds(layerBounds, { padding: [50, 50] });
    } else {
      console.log('Layer bounds are invalid, centering on a default location');
      map.setView([31.5493, -97.1117], 13); // Default to Waco, TX
    }

  } catch (error) {
    console.error('Error displaying historical data:', error);
    map.setView([31.5493, -97.1117], 13); // Default to Waco, TX in case of error
  }
}

// Function to update live data and metrics
async function updateLiveDataAndMetrics() {
  try {
    const response = await fetch('/live_data');
    const data = await response.json();
    updateLiveData(data);
  } catch (error) {
    console.error('Error updating live data:', error);
  }
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

// Calculate total distance of displayed routes
function calculateTotalDistance(features) {
  return features.reduce((total, feature) => {
    const coords = feature.geometry.coordinates;
    if (!coords || coords.length < 2) {
      console.error('Invalid coordinates:', coords);
      return total;
    }
    return total + coords.reduce((routeTotal, coord, index) => {
      if (index === 0) return routeTotal;
      const prevLatLng = L.latLng(coords[index - 1][1], coords[index - 1][0]);
      const currLatLng = L.latLng(coord[1], coord[0]);
      return routeTotal + prevLatLng.distanceTo(currLatLng) * 0.000621371; // Convert meters to miles
    }, 0);
  }, 0);
}

// Function to clip routes to Waco boundary
function clipRouteToWacoBoundary(feature) {
  if (!wacoLimits || !wacoLimits.getLayers || wacoLimits.getLayers().length === 0) {
    console.error('Waco limits not defined or empty');
    return null;
  }

  const wacoLayer = wacoLimits.getLayers()[0];
  if (!wacoLayer.feature || !wacoLayer.feature.geometry) {
    console.error('Invalid Waco limits geometry: Missing geometry property');
    return null;
  }

  let coordinates;
  if (wacoLayer.feature.geometry.type === 'Polygon') {
    coordinates = wacoLayer.feature.geometry.coordinates;
  } else if (wacoLayer.feature.geometry.type === 'MultiPolygon') {
    coordinates = wacoLayer.feature.geometry.coordinates.flat(1);
  } else {
    console.error('Invalid geometry type for Waco limits');
    return null;
  }

  const routeCoords = feature.geometry.coordinates.map(coord => {
    if (Array.isArray(coord) && coord.length >= 2 && typeof coord[0] === 'number' && typeof coord[1] === 'number') {
      return [coord[0], coord[1]];
    } else {
      console.error('Invalid coordinate found in route:', coord);
      return null;
    }
  }).filter(coord => coord !== null);

  if (routeCoords.length === 0) {
    console.error('No valid route coordinates after filtering');
    return null;
  }

  const routeLine = turf.lineString(routeCoords);
  const wacoPolygon = turf.polygon(coordinates);

  try {
    const split = turf.lineSplit(routeLine, wacoPolygon);

    if (split.features.length > 0) {
      const clippedFeatures = split.features.filter(segment => {
        if (segment.geometry.type !== 'LineString') {
          console.error('Invalid segment type during clipping:', segment.geometry.type);
          return false;
        }
        return turf.booleanWithin(segment, wacoPolygon) || turf.booleanOverlap(segment, wacoPolygon);
      });

      if (clippedFeatures.length > 0) {
        const mergedCoords = clippedFeatures.reduce((acc, segment) => {
          return acc.concat(segment.geometry.coordinates);
        }, []);

        const uniqueCoords = mergedCoords.filter((coord, index, self) => {
          return index === 0 || !arraysEqual(coord, self[index - 1]);
        });

        return turf.lineString(uniqueCoords);
      }
    } else if (turf.booleanWithin(routeLine, wacoPolygon)) {
      return routeLine;
    } else {
      console.log('Route does not intersect Waco boundary and is not within Waco');
      return null;
    }
  } catch (error) {
    console.error('Error during clipping operation:', error);
    return null;
  }
}

// Function to compare arrays for equality
function arraysEqual(arr1, arr2) {
  if (arr1.length !== arr2.length) return false;
  for (let i = 0; i < arr1.length; i++) {
    if (arr1[i] !== arr2[i]) return false;
  }
  return true;
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

// Function to toggle play/pause state
function togglePlayPause() {
  isPlaying = !isPlaying;
  playPauseBtn.textContent = isPlaying ? 'Pause' : 'Play';
}

// Function to stop playback
function stopPlayback() {
  isPlaying = false;
  playPauseBtn.textContent = 'Play';
  currentCoordIndex = 0;
  if (playbackAnimation) {
    clearInterval(playbackAnimation);
  }
  if (playbackPolyline) {
    map.removeLayer(playbackPolyline);
  }
  if (playbackMarker) {
    map.removeLayer(playbackMarker);
  }
}

// Function to adjust playback speed
function adjustPlaybackSpeed() {
  playbackSpeed = parseFloat(playbackSpeedInput.value);
  speedValueSpan.textContent = playbackSpeed.toFixed(1) + 'x';
  if (playbackAnimation) {
    clearInterval(playbackAnimation);
    startPlayback(playbackPolyline.getLatLngs());
  }
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

// Setup event listeners
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

// DOMContentLoaded event listener
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