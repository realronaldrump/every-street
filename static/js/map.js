// Global constants
const MCLENNAN_COUNTY_BOUNDS = L.latLngBounds(
  L.latLng(31.3501, -97.4585), // Southwest corner
  L.latLng(31.7935, -96.8636)  // Northeast corner
);
const DEFAULT_WACO_BOUNDARY = 'less_goofy';
const DEFAULT_WACO_STREETS_OPACITY = 0.7;
const DEFAULT_WACO_STREETS_FILTER = 'all';
const ALL_TIME_START_DATE = new Date(2020, 0, 1); // Assuming data starts from 2020
const MAX_HISTORICAL_DATA_LOAD_ATTEMPTS = 3;
const DATA_POLLING_INTERVAL = 1000; // Poll for live data every second
const PROGRESS_UPDATE_INTERVAL = 60000; // Update progress every minute
const PROGRESS_DATA_UPDATE_INTERVAL = 300000; // Update progress data every 5 minutes
const LIVE_DATA_AND_METRICS_UPDATE_INTERVAL = 3000; // Update live data and metrics every 3 seconds
const FEEDBACK_DURATION = 5000; // Default duration for feedback messages

// Custom marker icons
const BLUE_BLINKING_MARKER_ICON = L.divIcon({
  className: 'blinking-marker',
  iconSize: [20, 20],
  html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
});

const RED_BLINKING_MARKER_ICON = L.divIcon({
  className: 'blinking-marker animate__animated animate__bounce',
  iconSize: [20, 20],
  html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
});

const RED_MARKER_ICON = L.divIcon({
  className: 'custom-marker animate__animated animate__bounceInDown',
  iconSize: [30, 30],
  html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
});

// Global variables
let map;
let wacoLimitsLayer;
let progressLayer;
let historicalDataLayer;
let liveRoutePolyline;
let liveMarker;
let playbackPolyline;
let playbackMarker;
let wacoStreetsLayer;
let drawnItems;
let playbackSpeed = 1;
let isPlaying = false;
let currentCoordIndex = 0;
let playbackAnimation;
let isProcessing = false;
const processingQueue = [];

// Initialize the application
document.addEventListener('DOMContentLoaded', async () => {
  try {
    showFeedback('Initializing application...', 'info');

    // Initialize the map
    map = initMap();

    // Initialize layers and controls
    await Promise.all([
      initWacoLimitsLayer(),
      initProgressLayer(),
      initWacoStreetsLayer(),
      loadHistoricalData(),
      loadLiveRouteData()
    ]);

    // Set up data polling and updates
    setInterval(updateLiveDataAndMetrics, LIVE_DATA_AND_METRICS_UPDATE_INTERVAL);
    setInterval(updateProgress, PROGRESS_UPDATE_INTERVAL);
    setInterval(loadProgressData, PROGRESS_DATA_UPDATE_INTERVAL);

    // Set up event listeners
    setupEventListeners();

    showFeedback('Application initialized successfully', 'success');
  } catch (error) {
    console.error('Error initializing application:', error);
    showFeedback(`Error initializing application: ${error.message}. Please refresh the page.`, 'error');
  }
});

// Initialize the Leaflet map
function initMap() {
  try {
    const map = L.map('map').fitBounds(MCLENNAN_COUNTY_BOUNDS);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      maxZoom: 19
    }).addTo(map);

    // Create map panes with correct z-index order
    map.createPane('wacoLimitsPane').style.zIndex = 400;
    map.createPane('progressPane').style.zIndex = 410;
    map.createPane('historicalDataPane').style.zIndex = 430;
    map.createPane('wacoStreetsPane').style.zIndex = 440;

    // Add progress controls
    const progressControl = L.control({ position: 'bottomleft' });
    progressControl.onAdd = () => {
      const div = L.DomUtil.create('div', 'progress-control');
      div.innerHTML = '<div id="progress-bar-container"><div id="progress-bar"></div></div><div id="progress-text"></div>';
      return div;
    };
    progressControl.addTo(map);

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
      drawnItems.addLayer(e.layer);
      filterHistoricalDataByPolygon(e.layer);
    });

    map.on(L.Draw.Event.EDITED, (e) => {
      e.layers.eachLayer(filterHistoricalDataByPolygon);
    });

    map.on(L.Draw.Event.DELETED, displayHistoricalData);

    return map;
  } catch (error) {
    console.error('Error initializing map:', error);
    showFeedback('Error initializing map. Please refresh the page.', 'error');
    throw error;
  }
}

// Initialize the Waco city limits layer
async function initWacoLimitsLayer() {
  try {
    wacoLimitsLayer = L.geoJSON(await fetchGeoJSON(`/static/${DEFAULT_WACO_BOUNDARY}.geojson`), {
      style: {
        color: 'red',
        weight: 2,
        fillColor: 'orange',
        fillOpacity: 0.03
      },
      pane: 'wacoLimitsPane'
    });
    updateWacoLimitsLayerVisibility();
  } catch (error) {
    console.error('Error initializing Waco limits layer:', error);
    showFeedback('Error loading Waco limits. Some features may be unavailable.', 'error');
  }
}

// Update the visibility of the Waco limits layer based on checkbox state
function updateWacoLimitsLayerVisibility() {
  const showWacoLimits = document.getElementById('wacoLimitsCheckbox').checked;
  if (showWacoLimits && map && !map.hasLayer(wacoLimitsLayer)) {
    wacoLimitsLayer.addTo(map);
  } else if (!showWacoLimits && map && map.hasLayer(wacoLimitsLayer)) {
    map.removeLayer(wacoLimitsLayer);
  }
}

// Initialize the progress layer
async function initProgressLayer() {
  try {
    progressLayer = L.geoJSON(await fetchGeoJSON(`/progress_geojson?wacoBoundary=${DEFAULT_WACO_BOUNDARY}`), {
      style: (feature) => ({
        color: feature.properties.traveled ? '#00ff00' : '#ff0000',
        weight: 3,
        opacity: 0.7
      }),
      pane: 'progressPane'
    });
    updateProgressLayerVisibility();
  } catch (error) {
    console.error('Error initializing progress layer:', error);
    showFeedback('Error loading progress data. Some features may be unavailable.', 'error');
  }
}

// Update the visibility of the progress layer based on checkbox state
function updateProgressLayerVisibility() {
  const showProgressLayer = document.getElementById('progressLayerCheckbox').checked;
  if (showProgressLayer && map && !map.hasLayer(progressLayer)) {
    progressLayer.addTo(map);
  } else if (!showProgressLayer && map && map.hasLayer(progressLayer)) {
    map.removeLayer(progressLayer);
  }
}

// Initialize the Waco streets layer
async function initWacoStreetsLayer() {
  try {
    wacoStreetsLayer = L.geoJSON(await fetchGeoJSON('/static/Waco-Streets.geojson'), {
      style: {
        color: '#808080',
        weight: 1,
        opacity: DEFAULT_WACO_STREETS_OPACITY
      },
      pane: 'wacoStreetsPane'
    });
    updateWacoStreetsLayerVisibility();
  } catch (error) {
    console.error('Error initializing Waco streets layer:', error);
    showFeedback('Error loading Waco streets. Some features may be unavailable.', 'error');
  }
}

// Update the visibility of the Waco streets layer based on checkbox state
function updateWacoStreetsLayerVisibility() {
  const showWacoStreets = document.getElementById('wacoStreetsCheckbox').checked;
  if (showWacoStreets && map && !map.hasLayer(wacoStreetsLayer)) {
    wacoStreetsLayer.addTo(map);
  } else if (!showWacoStreets && map && map.hasLayer(wacoStreetsLayer)) {
    map.removeLayer(wacoStreetsLayer);
  }
}

// Load historical data from the server
async function loadHistoricalData() {
  let historicalDataLoadAttempts = 0;
  while (historicalDataLoadAttempts < MAX_HISTORICAL_DATA_LOAD_ATTEMPTS) {
    try {
      const data = await fetchHistoricalData();
      if (data?.features && data.features.length > 0) {
        historicalDataLayer = L.geoJSON(data, {
          style: {
            color: '#0000FF',
            weight: 3,
            opacity: 0.7
          },
          onEachFeature: addRoutePopup,
          pane: 'historicalDataPane'
        });
        updateHistoricalDataLayerVisibility();
        animateStatUpdate('totalHistoricalDistance', `${calculateTotalDistance(data.features).toFixed(2)} miles`);
        showFeedback('Historical data loaded successfully', 'success');
        return;
      } else {
        throw new Error('Invalid or empty historical data received');
      }
    } catch (error) {
      console.error('Error loading historical data:', error);
      showFeedback('Error loading historical data. Retrying...', 'error');
    }
    historicalDataLoadAttempts++;
    await delay(5000); // Wait 5 seconds before retrying
  }
  showFeedback('Error loading historical data. Please refresh the page.', 'error');
}

// Fetch historical data based on filter settings
async function fetchHistoricalData() {
  const startDate = document.getElementById('startDate').value;
  const endDate = document.getElementById('endDate').value;
  const filterWaco = document.getElementById('filterWaco').checked;
  const wacoBoundary = document.getElementById('wacoBoundarySelect').value;

  const response = await fetch(
    `/historical_data?startDate=${startDate}&endDate=${endDate}` +
    `&filterWaco=${filterWaco}&wacoBoundary=${wacoBoundary}`
  );

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }

  const data = await response.json();
  
  // Check if the data is in GeoDataFrame format and convert if necessary
  if (data.features && Array.isArray(data.features)) {
    return {
      type: "FeatureCollection",
      features: data.features.map(feature => ({
        type: "Feature",
        geometry: feature.geometry,
        properties: {
          timestamp: feature.timestamp,
          // Add any other properties you need
        }
      }))
    };
  }

  return data; // If it's already in GeoJSON format, return as is
}

// Update the visibility of the historical data layer based on checkbox state
function updateHistoricalDataLayerVisibility() {
  const showHistoricalData = document.getElementById('historicalDataCheckbox').checked;
  if (showHistoricalData && historicalDataLayer && map) {
    historicalDataLayer.addTo(map);
  } else if (!showHistoricalData && historicalDataLayer && map) {
    map.removeLayer(historicalDataLayer);
  }
}

// Load live route data from the server
async function loadLiveRouteData() {
  try {
    const data = await fetchGeoJSON('/live_route');
    if (data?.features?.[0]?.geometry?.coordinates?.length > 0) {
      const coordinates = data.features[0].geometry.coordinates.filter(coord => 
        Array.isArray(coord) && coord.length >= 2 && 
        !isNaN(coord[0]) && !isNaN(coord[1])
      );

      if (coordinates.length > 1) {
        liveRoutePolyline = L.polyline(coordinates.map(coord => [coord[1], coord[0]]), {
          color: '#007bff',
          weight: 4
        }).addTo(map);
      } else {
        console.warn('Not enough valid coordinates to create a polyline');
        showFeedback('Not enough valid coordinates to display route', 'warning');
      }

      if (coordinates.length > 0) {
        const lastCoord = coordinates[coordinates.length - 1];
        liveMarker = createAnimatedMarker([lastCoord[1], lastCoord[0]], { icon: BLUE_BLINKING_MARKER_ICON }).addTo(map);
        map.setView([lastCoord[1], lastCoord[0]], 13);
      } else {
        console.warn('No valid coordinates found');
        showFeedback('No valid coordinates found for live route', 'warning');
      }
    } else {
      console.warn('Live route data is missing or incomplete:', data);
      showFeedback('Live route data is missing or incomplete', 'warning');
    }
  } catch (error) {
    console.error('Error loading live route data:', error);
    showFeedback('Error loading live route data', 'error');
  }
}

// Clear the live route from the map
function clearLiveRoute() {
  removeLayer(liveRoutePolyline);
  removeLayer(liveMarker);
}

// Update live data on the map
function updateLiveData(liveData) {
  removeLayer(liveMarker);
  const latLng = [liveData.latitude, liveData.longitude];
  liveMarker = createAnimatedMarker(latLng, { icon: BLUE_BLINKING_MARKER_ICON }).addTo(map);
}

// Update live data and metrics
async function updateLiveDataAndMetrics() {
  try {
    const [liveData, metrics] = await Promise.all([
      fetchJSON('/live_data'),
      fetchJSON('/trip_metrics')
    ]);
    if (!liveData.error) {
      updateLiveData(liveData);
    } else {
      console.error('Error fetching live data:', liveData.error);
    }
    animateStatUpdates(metrics);
  } catch (error) {
    console.error('Error updating live data and metrics:', error);
    showFeedback('Error updating live data and metrics', 'error');
  }
}

// Animate updates to the statistics panel
function animateStatUpdates(metrics) {
  ['totalDistance', 'totalTime', 'maxSpeed', 'startTime', 'endTime'].forEach(id => {
    animateStatUpdate(id, metrics[id.toLowerCase()]);
  });
}

// Update the progress bar and text
async function updateProgress() {
  try {
    const data = await fetchJSON('/progress');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');

    if (progressBar && progressText && data.coverage_percentage !== undefined) {
      const newWidth = `${data.coverage_percentage}%`;
      if (progressBar.style.width !== newWidth) {
        progressBar.style.width = newWidth;
        progressBar.classList.add('progress-bar-update');
        setTimeout(() => {
          progressBar.classList.remove('progress-bar-update');
        }, 1000);
      }
      progressText.innerHTML = `
        <strong>Length Coverage:</strong> ${data.coverage_percentage.toFixed(2)}% of Waco Streets Traveled<br>
        <strong>Street Count:</strong> ${data.traveled_streets} / ${data.total_streets} (${(data.traveled_streets / data.total_streets * 100).toFixed(2)}%)
      `;
    } else {
      console.warn("Progress data is incomplete or DOM elements not found");
    }
  } catch (error) {
    console.error('Error fetching progress:', error);
  }
}

// Load progress data and update the progress layer
async function loadProgressData() {
  try {
    const wacoBoundary = document.getElementById('wacoBoundarySelect').value;
    const data = await fetchGeoJSON(`/progress_geojson?wacoBoundary=${wacoBoundary}`);
    if (progressLayer && map) {
      map.removeLayer(progressLayer);
    }
    progressLayer = L.geoJSON(data, {
      style: (feature) => ({
        color: feature.properties.traveled ? '#00ff00' : '#ff0000',
        weight: 3,
        opacity: 0.7
      }),
      pane: 'progressPane'
    });
    updateProgressLayerVisibility();
  } catch (error) {
    console.error('Error loading progress data:', error);
    showFeedback('Error loading progress data. Please try again.', 'error');
  }
}

// Load Waco streets data and update the Waco streets layer
async function loadWacoStreets() {
  try {
    const data = await fetchGeoJSON('/static/Waco-Streets.geojson');
    if (wacoStreetsLayer && map) {
      map.removeLayer(wacoStreetsLayer);
    }
    wacoStreetsLayer = L.geoJSON(data, {
      style: {
        color: '#808080', // Light gray color
        weight: 1,
        opacity: 0.7
      },
      pane: 'wacoStreetsPane'
    });
    updateWacoStreetsLayerVisibility();
    showFeedback('Waco streets displayed', 'success');
  } catch (error) {
    console.error('Error loading Waco streets:', error);
    showFeedback('Error loading Waco streets', 'error');
  }
}

// Add a popup to a route feature
function addRoutePopup(feature, layer) {
  const timestamp = feature.properties.timestamp || feature.timestamp;
  const date = new Date(timestamp * 1000).toLocaleDateString();
  const time = new Date(timestamp * 1000).toLocaleTimeString();
  const distance = calculateTotalDistance([feature]);

  const playbackButton = document.createElement('button');
  playbackButton.textContent = 'Play Route';
  playbackButton.classList.add('animate__animated', 'animate__pulse');
  playbackButton.addEventListener('click', () => {
    if (feature.geometry.type === 'LineString' && feature.geometry.coordinates.length > 1) {
      startPlayback(feature.geometry.coordinates);
    } else if (feature.geometry.type === 'MultiLineString') {
      const validSegments = feature.geometry.coordinates.filter(segment => segment.length > 1);
      validSegments.forEach(startPlayback);
    }
  });

  const popupContent = document.createElement('div');
  popupContent.innerHTML = `Date: ${date}<br>Time: ${time}<br>Distance: ${distance.toFixed(2)} miles`;
  popupContent.appendChild(playbackButton);

  layer.bindPopup(popupContent);
}

// Calculate the total distance of a set of features
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

// Start route playback
function startPlayback(coordinates) {
  stopPlayback();
  currentCoordIndex = 0;
  playbackPolyline = L.polyline([], {
    color: 'yellow',
    weight: 4
  }).addTo(map);

  playbackMarker = createAnimatedMarker(L.latLng(coordinates[0][1], coordinates[0][0]), {
    icon: RED_BLINKING_MARKER_ICON
  }).addTo(map);

  isPlaying = true;
  document.getElementById('playPauseBtn').textContent = 'Pause';
  playbackAnimation = setInterval(() => {
    if (isPlaying && currentCoordIndex < coordinates.length) {
      const latLng = L.latLng(coordinates[currentCoordIndex][1], coordinates[currentCoordIndex][0]);
      playbackMarker.setLatLng(latLng);
      playbackPolyline.addLatLng(latLng);
      currentCoordIndex++;
    } else if (currentCoordIndex >= coordinates.length) {
      stopPlayback();
    }
  }, 100 / playbackSpeed);
}

// Toggle route playback
function togglePlayPause() {
  isPlaying = !isPlaying;
  document.getElementById('playPauseBtn').textContent = isPlaying ? 'Pause' : 'Play';
  showFeedback(isPlaying ? 'Playback resumed' : 'Playback paused', 'info');
}

// Stop route playback
function stopPlayback() {
  isPlaying = false;
  document.getElementById('playPauseBtn').textContent = 'Play';
  currentCoordIndex = 0;
  clearInterval(playbackAnimation);
  removeLayer(playbackPolyline);
  removeLayer(playbackMarker);
}

// Adjust route playback speed
function adjustPlaybackSpeed() {
  playbackSpeed = parseFloat(document.getElementById('playbackSpeed').value);
  document.getElementById('speedValue').textContent = playbackSpeed.toFixed(1) + 'x';
  if (playbackAnimation) {
    clearInterval(playbackAnimation);
    startPlayback(playbackPolyline.getLatLngs());
  }
  animateElement(document.getElementById('speedValue'), 'animate__rubberBand');
}

// Filter historical data by a drawn polygon
function filterHistoricalDataByPolygon(polygon) {
  if (!historicalDataLayer) return;

  const filteredFeatures = historicalDataLayer.toGeoJSON().features.filter(feature => {
    if (feature.geometry.type === 'LineString') {
      return turf.booleanCrosses(polygon.toGeoJSON(), feature) ||
          turf.booleanWithin(feature, polygon.toGeoJSON());
    } else if (feature.geometry.type === 'MultiLineString') {
      return feature.geometry.coordinates.some(segment =>
          turf.booleanCrosses(polygon.toGeoJSON(), turf.lineString(segment)) ||
          turf.booleanWithin(turf.lineString(segment), polygon.toGeoJSON())
      );
    }
    return false;
  });

  updateMapWithHistoricalData({
    type: 'FeatureCollection',
    features: filteredFeatures
  });
}

// Clear all drawn shapes from the map
function clearDrawnShapes() {
  drawnItems.clearLayers();
  displayHistoricalData();
}

// Filter routes by a predefined time period
function filterRoutesBy(period) {
  const now = new Date();
  let startDate, endDate;

  switch (period) {
    case 'today':
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      endDate = now;
      break;
    case 'yesterday':
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
      endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      break;
    case 'lastWeek':
      startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 7);
      endDate = now;
      break;
    case 'lastMonth':
      startDate = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate());
      endDate = now;
      break;
    case 'lastYear':
      startDate = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
      endDate = now;
      break;
    case 'allTime':
      startDate = ALL_TIME_START_DATE;
      endDate = now;
      break;
    default:
      console.error('Invalid period:', period);
      return;
  }

  document.getElementById('startDate').value = startDate.toISOString().slice(0, 10);
  document.getElementById('endDate').value = endDate.toISOString().slice(0, 10);
  displayHistoricalData();
}

// Export historical data to a GPX file
async function exportToGPX() {
  showLoading('Preparing GPX export...');
  try {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const filterWaco = document.getElementById('filterWaco').checked;
    const wacoBoundary = document.getElementById('wacoBoundarySelect').value;

    const url = `/export_gpx?startDate=${startDate}&endDate=${endDate}&filterWaco=${filterWaco}&wacoBoundary=${wacoBoundary}`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.style.display = 'none';
    a.href = downloadUrl;
    a.download = 'export.gpx';
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(downloadUrl);
    showFeedback('GPX export completed. Check your downloads.', 'success');
  } catch (error) {
    console.error('Error exporting GPX:', error);
    showFeedback('Error exporting GPX. Please try again.', 'error');
  } finally {
    hideLoading();
  }
}

// Update the map with historical data
function updateMapWithHistoricalData(data) {
  removeLayer(historicalDataLayer);

  historicalDataLayer = L.geoJSON(data, {
    style: {
      color: '#0000FF',
      weight: 3,
      opacity: 0.7
    },
    onEachFeature: addRoutePopup,
    filter: (feature) => feature.geometry?.coordinates && feature.geometry.coordinates.length > 0,
    pane: 'historicalDataPane'
  });

  if (map && !map.hasLayer(historicalDataLayer)) {
    historicalDataLayer.addTo(map);
  }

  const totalDistance = calculateTotalDistance(data.features);
  animateStatUpdate('totalHistoricalDistance', `${totalDistance.toFixed(2)} miles`);

  showFeedback(`Displayed ${data.features.length} historical features`, 'success');

  if (data.features.length > 0 && historicalDataLayer) {
    const bounds = historicalDataLayer.getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds);
    } else {
      console.warn('Invalid bounds for historical data');
    }
  }
  
  // Animate the historical data layer
  if (historicalDataLayer) {
    historicalDataLayer.eachLayer(function (layer) {
      if (layer.getElement()) {
        layer.getElement().classList.add('animate__animated', 'animate__fadeIn');
      }
    });
  }
}

// Display historical data based on filter settings
async function displayHistoricalData() {
  if (isProcessing) {
    showFeedback('A task is already in progress. Please wait.', 'warning');
    return;
  }

  isProcessing = true;
  disableFilterControls();
  showLoading('Loading historical data...');

  try {
    const data = await fetchHistoricalData();
    updateMapWithHistoricalData(data);
  } catch (error) {
    console.error('Error displaying historical data:', error);
    showFeedback(`Error loading historical data: ${error.message}. Please try again.`, 'error');
  } finally {
    isProcessing = false;
    enableFilterControls();
    hideLoading();
    checkQueuedTasks();
  }
}

// Disable filter controls
function disableFilterControls() {
  ['#time-filters button', '#applyFilterBtn', '#filterWaco', '#startDate', '#endDate', '#wacoBoundarySelect']
    .forEach(selector => {
      document.querySelectorAll(selector).forEach(el => el.disabled = true);
    });
}

// Enable filter controls
function enableFilterControls() {
  ['#time-filters button', '#applyFilterBtn', '#filterWaco', '#startDate', '#endDate', '#wacoBoundarySelect']
    .forEach(selector => {
      document.querySelectorAll(selector).forEach(el => el.disabled = false);
    });
}

// Set up event listeners for all controls
function setupEventListeners() {
  // Filter Controls
  document.getElementById('startDate').addEventListener('change', displayHistoricalData);
  document.getElementById('endDate').addEventListener('change', displayHistoricalData);
  document.getElementById('wacoBoundarySelect').addEventListener('change', displayHistoricalData);
  document.getElementById('filterWaco').addEventListener('change', displayHistoricalData);
  document.getElementById('applyFilterBtn').addEventListener('click', displayHistoricalData);
  document.querySelectorAll('#time-filters button').forEach(button => {
    button.addEventListener('click', () => filterRoutesBy(button.dataset.period));
  });

  // Layer Control Checkboxes
  document.getElementById('historicalDataCheckbox').addEventListener('change', updateHistoricalDataLayerVisibility);
  document.getElementById('wacoStreetsCheckbox').addEventListener('change', updateWacoStreetsLayerVisibility);
  document.getElementById('wacoLimitsCheckbox').addEventListener('change', updateWacoLimitsLayerVisibility);
  document.getElementById('progressLayerCheckbox').addEventListener('change', updateProgressLayerVisibility);

  // Data Update Button
  const updateDataBtn = document.getElementById('updateDataBtn');
  if (updateDataBtn) {
    updateDataBtn.addEventListener('click', handleBackgroundTask(async () => {
      try {
        const response = await fetch('/update_historical_data', { method: 'POST' });
        const data = await response.json();
        if (response.ok) {
          showFeedback(data.message, 'success');
          await Promise.all([
            displayHistoricalData(),
            updateProgress()
          ]);
        } else {
          throw new Error(data.error);
        }
      } catch (error) {
        throw new Error('Error updating historical data: ' + error.message);
      }
    }, 'Checking for new driving data...'));
  }

  // Clear Route Button
  const clearRouteBtn = document.getElementById('clearRouteBtn');
  if (clearRouteBtn) {
    clearRouteBtn.addEventListener('click', handleBackgroundTask(() => {
      clearLiveRoute();
      showFeedback('Live route cleared', 'info');
    }, 'Clearing live route...'));
  }

  // Playback Controls
  const playPauseBtn = document.getElementById('playPauseBtn');
  if (playPauseBtn) {
    playPauseBtn.addEventListener('click', togglePlayPause);
  }

  const stopBtn = document.getElementById('stopBtn');
  if (stopBtn) {
    stopBtn.addEventListener('click', () => {
      stopPlayback();
      showFeedback('Playback stopped', 'info');
    });
  }

  const playbackSpeedInput = document.getElementById('playbackSpeed');
  if (playbackSpeedInput) {
    playbackSpeedInput.addEventListener('input', adjustPlaybackSpeed);
  }

  // Search Controls
  const searchInput = document.getElementById('searchInput');
  const searchBtn = document.getElementById('searchBtn');
  if (searchBtn && searchInput) {
    searchBtn.addEventListener('click', handleBackgroundTask(async () => {
      const query = searchInput.value;
      if (!query) {
        showFeedback('Please enter a location to search for.', 'warning');
        return;
      }

      try {
        const data = await fetchJSON(`/search_location?query=${query}`);
        if (data.error) {
          throw new Error(data.error);
        } else {
          const { latitude, longitude, address } = data;
          map.setView([latitude, longitude], 13);
          removeLayer(searchMarker);
          searchMarker = createAnimatedMarker([latitude, longitude], { icon: RED_MARKER_ICON })
            .addTo(map)
            .bindPopup(`<b>${address}</b>`)
            .openPopup();

          showFeedback(`Found location: ${address}`, 'success');

          setTimeout(() => removeLayer(searchMarker), 10000);
        }
      } catch (error) {
        throw new Error('Error searching for location: ' + error.message);
      }
    }, 'Searching for location...'));

    searchInput.addEventListener('input', debounce(async () => {
      const query = searchInput.value;
      const suggestionsContainer = document.getElementById('searchSuggestions');
      suggestionsContainer.innerHTML = '';

      if (query.length < 3) {
        return;
      }

      try {
        const suggestions = await fetchJSON(`/search_suggestions?query=${query}`);
        if (suggestions.length > 0) {
          suggestions.forEach(suggestion => {
            const suggestionElement = document.createElement('div');
            suggestionElement.textContent = suggestion.address;
            suggestionElement.classList.add('animate__animated', 'animate__fadeIn');
            suggestionElement.addEventListener('click', () => {
              searchInput.value = suggestion.address;
              suggestionsContainer.innerHTML = '';
            });
            suggestionsContainer.appendChild(suggestionElement);
          });
        }
      } catch (error) {
        console.error('Error fetching search suggestions:', error);
      }
    }, 300));

    searchInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        document.getElementById('searchSuggestions').innerHTML = '';
      }
    });

    searchBtn.addEventListener('click', () => {
      document.getElementById('searchSuggestions').innerHTML = '';
    });
  }

  // Export to GPX Button
  const exportToGPXBtn = document.getElementById('exportToGPXBtn');
  if (exportToGPXBtn) {
    exportToGPXBtn.addEventListener('click', handleBackgroundTask(exportToGPX, 'Exporting to GPX...'));
  }

  // Clear Drawn Shapes Button
  const clearDrawnShapesBtn = document.getElementById('clearDrawnShapesBtn');
  if (clearDrawnShapesBtn) {
    clearDrawnShapesBtn.addEventListener('click', handleBackgroundTask(clearDrawnShapes, 'Clearing drawn shapes...'));
  }

  // Reset Progress Button
  const resetProgressBtn = document.getElementById('resetProgressBtn');
  if (resetProgressBtn) {
    resetProgressBtn.addEventListener('click', handleBackgroundTask(async () => {
      try {
        const response = await fetch('/reset_progress', { method: 'POST' });
        const data = await response.json();
        if (response.ok) {
          showFeedback(data.message, 'success');
          await Promise.all([
            updateProgress(),
            loadWacoStreets(),
            loadProgressData()
          ]);
        } else {
          throw new Error(data.error);
        }
      } catch (error) {
        throw new Error('Error resetting progress: ' + error.message);
      }
    }, 'Resetting progress...'));
  }

  // Streets Filter Select
  const streetsSelect = document.getElementById('streets-select');
  if (streetsSelect) {
    streetsSelect.addEventListener('change', (e) => {
      const wacoStreetsFilter = e.target.value;
      if (wacoStreetsLayer) {
        loadWacoStreets();
      }
    });
  }

  // Logout Button
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) {
    logoutBtn.addEventListener('click', () => {
      window.location.href = '/logout';
    });
  }
}

// Debounce function for search suggestions
function debounce(func, wait) {
  let timeout;
  return function executedFunction(...args) {
    const later = () => {
      clearTimeout(timeout);
      func(...args);
    };
    clearTimeout(timeout);
    timeout = setTimeout(later, wait);
  };
}

// Show loading overlay with a message
function showLoading(message = 'Loading...') {
  const loadingOverlay = document.getElementById('loadingOverlay');
  if (loadingOverlay) {
    loadingOverlay.querySelector('.loading-text').textContent = message;
    loadingOverlay.style.display = 'flex';
  }
}

// Hide loading overlay
function hideLoading() {
  const loadingOverlay = document.getElementById('loadingOverlay');
  if (loadingOverlay) {
    loadingOverlay.style.display = 'none';
  }
}

// Create an animated marker
function createAnimatedMarker(latLng, options = {}) {
  return L.marker(latLng, { icon: BLUE_BLINKING_MARKER_ICON, ...options });
}

// Animate the update of a statistic element
function animateStatUpdate(elementId, newValue) {
  const element = document.getElementById(elementId);
  if (element) {
    animateElement(element, 'animate__flipInX');
    element.textContent = newValue;
  }
}

// Animate an element with a given animation class
function animateElement(element, animationClass) {
  element.classList.add('animate__animated', animationClass);
  setTimeout(() => {
    element.classList.remove('animate__animated', animationClass);
  }, 1000);
}

// Fetch JSON data from a given URL
async function fetchJSON(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`);
  }
  return response.json();
}

// Fetch GeoJSON data from a given URL
async function fetchGeoJSON(url) {
  return fetchJSON(url);
}

// Display a feedback message
function showFeedback(message, type = 'info', duration = FEEDBACK_DURATION) {
  const feedbackContainer = document.getElementById('feedback-container');
  const feedbackElement = document.createElement('div');
  feedbackElement.className = `feedback ${type} animate__animated animate__fadeInDown`;

  const icon = document.createElement('span');
  icon.className = 'feedback-icon';
  icon.textContent = type === 'error' ? '❌' : type === 'success' ? '✅' : 'ℹ️';

  const textElement = document.createElement('span');
  textElement.textContent = message;

  feedbackElement.appendChild(icon);
  feedbackElement.appendChild(textElement);
  feedbackContainer.appendChild(feedbackElement);

  setTimeout(() => {
    feedbackElement.classList.remove('animate__fadeInDown');
    feedbackElement.classList.add('animate__fadeOutUp');
    setTimeout(() => feedbackElement.remove(), 1000);
  }, duration);
}

// Handle a background task with UI locking and feedback
function handleBackgroundTask(taskFunction, feedbackMessage) {
  return async function(...args) {
    if (isProcessing) {
      showFeedback('A task is already in progress. Please wait.', 'warning');
      return;
    }
    isProcessing = true;
    disableUI();
    showLoading(feedbackMessage);
    try {
      await taskFunction(...args);
    } catch (error) {
      console.error('Task failed:', error);
      showFeedback(`Error: ${error.message}`, 'error');
    } finally {
      isProcessing = false;
      enableUI();
      hideLoading();
      checkQueuedTasks();
    }
  };
}

// Disable UI elements
function disableUI() {
  document.body.classList.add('processing');
  document.querySelectorAll('button, input, select').forEach(el => el.disabled = true);
}

// Enable UI elements
function enableUI() {
  document.body.classList.remove('processing');
  document.querySelectorAll('button, input, select').forEach(el => el.disabled = false);
}

// Check and process queued background tasks
function checkQueuedTasks() {
  if (processingQueue.length > 0 && !isProcessing) {
    processingQueue.shift()();
  }
}

// Helper function to remove a layer from the map
function removeLayer(layer) {
  if (layer && map.hasLayer(layer)) {
    map.removeLayer(layer);
  }
}

// Helper function to introduce a delay
function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Initialize the application when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', () => {
  setupEventListeners();
  initMap();
});