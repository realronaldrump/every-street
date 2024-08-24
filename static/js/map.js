// Global variables
let map, wacoLimits, liveMarker, historicalDataLayer, liveRoutePolyline, playbackAnimation, playbackPolyline, playbackMarker, liveRouteDataLayer;
let playbackSpeed = 1;
let isPlaying = false;
let currentCoordIndex = 0;
let drawnItems;
let selectedWacoBoundary = 'less_goofy'; // Default to the more precise boundary

// DOM elements
let filterWacoCheckbox, startDateInput, endDateInput, updateDataBtn, playPauseBtn, stopBtn, playbackSpeedInput, speedValueSpan, wacoBoundarySelect, clearRouteBtn, applyFilterBtn, searchInput, searchBtn;

// Feedback function
function showFeedback(message, type = 'info') {
  const feedbackContainer = document.getElementById('feedback-container');
  const feedbackElement = document.createElement('div');
  feedbackElement.className = `feedback ${type}`;
  feedbackElement.textContent = message;
  feedbackContainer.appendChild(feedbackElement);

  // Remove the feedback after 5 seconds
  setTimeout(() => {
    feedbackElement.remove();
  }, 5000);
}

// Function to initialize the map
function initializeMap() {
  map = L.map('map').setView([31.5493, -97.1117], 13); // Centered on Waco, TX

  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 19
  }).addTo(map);

  // Initialize drawing tools
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

  // Event listeners for drawing tools
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

// Function to load Waco city limits
async function loadWacoLimits(boundaryType) {
  if (!filterWacoCheckbox.checked) {
    if (wacoLimits) {
      map.removeLayer(wacoLimits);
      wacoLimits = null;
    }
    return; // Exit if no filter is applied
  }

  try {
    const response = await fetch(`/static/${boundaryType}.geojson`);
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

  // Clear playback route as well
  stopPlayback();

  // Also clear the live route data layer
  if (liveRouteDataLayer) {
    map.removeLayer(liveRouteDataLayer);
    liveRouteDataLayer = null;
  }
}

// Function to load live route data
async function loadLiveRouteData() {
  try {
    const response = await fetch('/live_route');
    const data = await response.json();

    if (liveRouteDataLayer) {
      map.removeLayer(liveRouteDataLayer);
    }

    liveRouteDataLayer = L.geoJSON(data, {
      style: { color: '#007bff', weight: 4 }, // Style the live route
      pointToLayer: function (feature, latlng) { // Use a custom marker for the live point
        if (liveMarker) {
          map.removeLayer(liveMarker);
        }
        liveMarker = L.marker(latlng, {
          icon: L.divIcon({
            className: 'blinking-marker',
            iconSize: [20, 20],
            html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
          })
        });
        return liveMarker;
      }
    }).addTo(map);

    // If there are points in the live route, set the view to the last point
    if (data.features.length > 0) {
      const lastCoord = data.features[data.features.length - 1].geometry.coordinates;
      map.setView([lastCoord[1], lastCoord[0]], 13);
    }

  } catch (error) {
    console.error('Error loading live route data:', error);
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

  if (liveMarker) {
    map.removeLayer(liveMarker);
  }
  liveMarker = L.marker(latLng, {
    icon: L.divIcon({
      className: 'blinking-marker',
      iconSize: [20, 20],
      html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
    })
  }).addTo(map);

  // Ensure only one live marker is present
  if (liveRouteDataLayer) {
    liveRouteDataLayer.eachLayer(layer => {
      if (layer instanceof L.Marker && layer !== liveMarker) {
        map.removeLayer(layer);
      }
    });
  }

  if (!liveRoutePolyline) {
    liveRoutePolyline = L.polyline([], { color: '#007bff', weight: 4 }).addTo(map);
  } else {
    liveRoutePolyline.addLatLng(latLng);
  }

  // Update the live route data layer
  if (liveRouteDataLayer) {
    liveRouteDataLayer.addData({
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [data.longitude, data.latitude]
      },
      "properties": {
        "timestamp": data.timestamp
      }
    });
  } else {
    loadLiveRouteData(); // Load the layer if it doesn't exist
  }
}

// Function to display historical data
async function displayHistoricalData() {
  try {
    showFeedback('Loading historical data...', 'info');
    const wacoBoundary = wacoBoundarySelect.value;
    const response = await fetch(`/historical_data?startDate=${startDateInput.value}&endDate=${endDateInput.value}&filterWaco=${filterWacoCheckbox.checked}&wacoBoundary=${wacoBoundary}`);
    const data = await response.json();

    if (historicalDataLayer) {
      map.removeLayer(historicalDataLayer);
    }

    // Clear any existing historical data
    if (drawnItems) {
      drawnItems.clearLayers();
    }

    historicalDataLayer = L.geoJSON(data, {
      style: { color: 'blue', weight: 2, opacity: 0.25 },
      onEachFeature: addRoutePopup
    }).addTo(map);

    // Update total historical distance
    const totalDistance = calculateTotalDistance(data.features);
    document.getElementById('totalHistoricalDistance').textContent = `${totalDistance.toFixed(2)} miles`;

    showFeedback(`Displayed ${data.features.length} historical features from monthly data`, 'success');
  } catch (error) {
    console.error('Error displaying historical data:', error);
    showFeedback('Error loading monthly historical data. Please try again.', 'error');
  }
}

// Function to update live data and metrics
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
    playbackPolyline = null; // Reset the polyline
  }
  if (playbackMarker) {
    map.removeLayer(playbackMarker);
    playbackMarker = null; // Reset the marker
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

// Function to filter historical data by a drawn polygon
function filterHistoricalDataByPolygon(polygon) {
  if (!historicalDataLayer) return;

  const filteredFeatures = historicalDataLayer.getLayers().filter(layer => {
    const routeGeoJSON = layer.toGeoJSON();
    if (routeGeoJSON.geometry.type === 'LineString') {
      return turf.booleanCrosses(polygon.toGeoJSON(), routeGeoJSON) ||
        turf.booleanWithin(routeGeoJSON, polygon.toGeoJSON());
    } else if (routeGeoJSON.geometry.type === 'MultiLineString') {
      return routeGeoJSON.geometry.coordinates.some(segment =>
        turf.booleanCrosses(polygon.toGeoJSON(), turf.lineString(segment)) ||
        turf.booleanWithin(turf.lineString(segment), polygon.toGeoJSON())
      );
    }
    return false;
  });

  // Create a new GeoJSON layer with the filtered features
  const filteredData = {
    type: 'FeatureCollection',
    features: filteredFeatures.map(layer => layer.toGeoJSON())
  };

  // Replace the existing historical data layer with the filtered one
  map.removeLayer(historicalDataLayer);
  historicalDataLayer = L.geoJSON(filteredData, {
    style: { color: 'blue', weight: 2, opacity: 0.25 },
    onEachFeature: addRoutePopup
  }).addTo(map);
}

// Function to clear drawn shapes
function clearDrawnShapes() {
  drawnItems.clearLayers();
  displayHistoricalData(); // Re-display all historical data
}

// Initialize Socket.IO for real-time updates
function initializeSocketIO() {
  const socket = io();
  socket.on('live_update', (data) => {
    updateLiveData(data);
  });
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
    allTime: new Date(2020, 0, 1) // Assuming your data starts from 2020
  };

  startDate = periodMap[period] instanceof Date
    ? periodMap[period]
    : new Date(now.getFullYear(), now.getMonth(), now.getDate() + periodMap[period]);

  startDateInput.value = startDate.toISOString().slice(0, 10);
  endDateInput.value = now.toISOString().slice(0, 10);
  displayHistoricalData(); // Update the map with the new date range
}

// Function to export data to GPX
function exportToGPX() {
  showFeedback('Preparing GPX export...', 'info');
  const startDate = startDateInput.value;
  const endDate = endDateInput.value;
  const filterWaco = filterWacoCheckbox.checked;
  const wacoBoundary = wacoBoundarySelect.value;

  const url = `/export_gpx?startDate=${startDate}&endDate=${endDate}&filterWaco=${filterWaco}&wacoBoundary=${wacoBoundary}`;

  // Create a temporary link element to trigger the download
  const link = document.createElement('a');
  link.href = url;
  link.download = 'export.gpx';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  showFeedback('GPX export completed. Check your downloads.', 'success');
}

// Initialize the application
async function initializeApp() {
  // Set endDateInput to today's date
  endDateInput.value = new Date().toISOString().slice(0, 10);

  await loadWacoLimits(selectedWacoBoundary);
  await displayHistoricalData();
  await loadLiveRouteData(); // Load the live route data on startup
  setInterval(updateLiveDataAndMetrics, 3000);
}

// Setup event listeners
function setupEventListeners() {
  applyFilterBtn.addEventListener('click', async () => {
    showFeedback('Applying filters...', 'info');
    await loadWacoLimits(wacoBoundarySelect.value);
    await displayHistoricalData();
    showFeedback('Filters applied successfully', 'success');
  });

  updateDataBtn.addEventListener('click', async () => {
    try {
      updateDataBtn.disabled = true;
      updateDataBtn.textContent = "Updating...";
      showFeedback('Checking for new driving data...', 'info');

      const response = await fetch('/update_historical_data', { method: 'POST' });
      const data = await response.json();

      if (response.ok) {
        showFeedback(data.message, 'success');
        await displayHistoricalData();
      } else {
        showFeedback(data.error, 'error');
      }
    } catch (error) {
      console.error('Error updating historical data:', error);
      showFeedback('Error updating historical data. Please try again.', 'error');
    } finally {
      updateDataBtn.disabled = false;
      updateDataBtn.textContent = "Check for new driving data";
    }
  });

  wacoBoundarySelect.addEventListener('change', () => {
    selectedWacoBoundary = wacoBoundarySelect.value;
    showFeedback(`Waco boundary changed to ${selectedWacoBoundary}`, 'info');
  });

  clearRouteBtn.addEventListener('click', () => {
    clearLiveRoute();
    showFeedback('Live route cleared', 'info');
  });

  playPauseBtn.addEventListener('click', () => {
    togglePlayPause();
    showFeedback(isPlaying ? 'Playback resumed' : 'Playback paused', 'info');
  });

  stopBtn.addEventListener('click', () => {
    stopPlayback();
    showFeedback('Playback stopped', 'info');
  });

  playbackSpeedInput.addEventListener('input', () => {
    adjustPlaybackSpeed();
    showFeedback(`Playback speed set to ${playbackSpeed.toFixed(1)}x`, 'info');
  });

  searchBtn.addEventListener('click', async () => {
    const query = searchInput.value;
    if (!query) {
      showFeedback('Please enter a location to search for.', 'warning');
      return;
    }

    try {
      const response = await fetch(`/search_location?query=${query}`);
      const data = await response.json();

      if (data.error) {
        showFeedback(data.error, 'error');
      } else {
        const { latitude, longitude, address } = data;
        map.setView([latitude, longitude], 13);

        if (searchMarker) {
          map.removeLayer(searchMarker);
        }

        searchMarker = L.marker([latitude, longitude], {
          icon: L.divIcon({
            className: 'custom-marker',
            iconSize: [30, 30],
            html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
          })
        }).addTo(map)
          .bindPopup(`<b>${address}</b>`)
          .openPopup();

        showFeedback(`Found location: ${address}`, 'success');

        // Remove the marker after 10 seconds
        setTimeout(() => {
          if (searchMarker) {
            map.removeLayer(searchMarker);
            searchMarker = null;
          }
        }, 10000);
      }
    } catch (error) {
      console.error('Error searching for location:', error);
      showFeedback('Error searching for location. Please try again.', 'error');
    }
  });

  searchInput.addEventListener('input', async () => {
    const query = searchInput.value;
    if (query.length < 3) {
      return;
    }

    try {
      const response = await fetch(`/search_suggestions?query=${query}`);
      const suggestions = await response.json();

      const suggestionsContainer = document.getElementById('searchSuggestions');
      suggestionsContainer.innerHTML = '';

      suggestions.forEach(suggestion => {
        const suggestionElement = document.createElement('div');
        suggestionElement.textContent = suggestion;
        suggestionElement.addEventListener('click', () => {
          searchInput.value = suggestion;
          suggestionsContainer.innerHTML = '';
        });
        suggestionsContainer.appendChild(suggestionElement);
      });
    } catch (error) {
      console.error('Error fetching search suggestions:', error);
    }
  });
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
  searchInput = document.getElementById('searchInput');
  searchBtn = document.getElementById('searchBtn');

  let searchMarker;

  searchBtn.addEventListener('click', async () => {
      const query = searchInput.value;
      if (!query) {
          showFeedback('Please enter a location to search for.', 'warning');
          return;
      }

      try {
          const response = await fetch(`/search_location?query=${query}`);
          const data = await response.json();

          if (data.error) {
              showFeedback(data.error, 'error');
          } else {
              const { latitude, longitude, address } = data;
              map.setView([latitude, longitude], 13);

              if (searchMarker) {
                  map.removeLayer(searchMarker);
              }

              searchMarker = L.marker([latitude, longitude], {
                  icon: L.divIcon({
                      className: 'custom-marker',
                      iconSize: [30, 30],
                      html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
                  })
              }).addTo(map)
                .bindPopup(`<b>${address}</b>`)
                .openPopup();

              showFeedback(`Found location: ${address}`, 'success');

              // Remove the marker after 10 seconds
              setTimeout(() => {
                  if (searchMarker) {
                      map.removeLayer(searchMarker);
                      searchMarker = null;
                  }
              }, 10000);
          }
      } catch (error) {
          console.error('Error searching for location:', error);
          showFeedback('Error searching for location. Please try again.', 'error');
      }
  });

  searchInput.addEventListener('input', async () => {
      const query = searchInput.value;
      const suggestionsContainer = document.getElementById('searchSuggestions');
      
      // Clear the suggestions container when input changes
      suggestionsContainer.innerHTML = '';

      if (query.length < 3) {
          return;
      }

      try {
          const response = await fetch(`/search_suggestions?query=${query}`);
          const suggestions = await response.json();

          if (suggestions.length > 0) {
              suggestions.forEach(suggestion => {
                  const suggestionElement = document.createElement('div');
                  suggestionElement.textContent = suggestion.address; // Access the address property
                  suggestionElement.addEventListener('click', () => {
                      searchInput.value = suggestion.address; // Set the input value to the selected address
                      suggestionsContainer.innerHTML = '';
                  });
                  suggestionsContainer.appendChild(suggestionElement);
              });
          }
      } catch (error) {
          console.error('Error fetching search suggestions:', error);
      }
  });

  initializeMap();
  initializeSocketIO();
  setupEventListeners();
  initializeApp();
});


