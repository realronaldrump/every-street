// Global variables
let map, wacoLimits, liveMarker, historicalDataLayer, liveRoutePolyline, playbackAnimation, playbackPolyline, playbackMarker, liveRouteDataLayer;
let playbackSpeed = 1;
let isPlaying = false;
let currentCoordIndex = 0;
let drawnItems;
let selectedWacoBoundary = 'less_goofy';
let worker;
let historicalDataLoaded = false;
let historicalDataLoading = false;
let isProcessing = false;
const processingQueue = [];
let historicalDataCache = {};
const CACHE_VERSION = 1; // Increment this when making changes to the cache structure
const MAX_CACHE_SIZE = 10; // Maximum number of cached items
let historicalDataLoadAttempts = 0;
const MAX_LOAD_ATTEMPTS = 3;
let isLoadingHistoricalData = false;

// DOM elements
const filterWacoCheckbox = document.getElementById('filterWaco');
const startDateInput = document.getElementById('startDate');
const endDateInput = document.getElementById('endDate');
const updateDataBtn = document.getElementById('updateDataBtn');
const playPauseBtn = document.getElementById('playPauseBtn');
const stopBtn = document.getElementById('stopBtn');
const playbackSpeedInput = document.getElementById('playbackSpeed');
const speedValueSpan = document.getElementById('speedValue');
const wacoBoundarySelect = document.getElementById('wacoBoundarySelect');
const clearRouteBtn = document.getElementById('clearRouteBtn');
const applyFilterBtn = document.getElementById('applyFilterBtn');
const searchInput = document.getElementById('searchInput');
const searchBtn = document.getElementById('searchBtn');
const exportToGPXBtn = document.getElementById('exportToGPXBtn');
const clearDrawnShapesBtn = document.getElementById('clearDrawnShapesBtn');
const suggestionsContainer = document.getElementById('searchSuggestions'); // Get the suggestions container

let searchMarker; // For search functionality

// Enhanced feedback function
function showFeedback(message, type = 'info', duration = 5000) {
    const feedbackContainer = document.getElementById('feedback-container');
    const feedbackElement = document.createElement('div');
    feedbackElement.className = `feedback ${type}`;

    const icon = document.createElement('span');
    icon.className = 'feedback-icon';
    icon.textContent = type === 'error' ? '❌' : type === 'success' ? '✅' : 'ℹ️';

    const textElement = document.createElement('span');
    textElement.textContent = message;

    feedbackElement.appendChild(icon);
    feedbackElement.appendChild(textElement);

    feedbackContainer.appendChild(feedbackElement);

    setTimeout(() => {
        feedbackElement.classList.add('fade-out');
        setTimeout(() => feedbackElement.remove(), 500);
    }, duration);
}

// Function to handle background tasks and UI locking
function handleBackgroundTask(taskFunction, feedbackMessage) {
    return async function(...args) {
        if (isProcessing) {
            showFeedback('A task is already in progress. Please wait.', 'warning');
            return;
        }

        isProcessing = true;
        disableUI();
        showFeedback(feedbackMessage, 'info');

        try {
            await taskFunction(...args);
        } catch (error) {
            console.error('Task failed:', error);
            showFeedback(`Error: ${error.message}`, 'error');
        } finally {
            isProcessing = false;
            enableUI();
            checkQueuedTasks();
        }
    };
}

// Function to disable UI elements
function disableUI() {
    document.body.classList.add('processing');
    document.querySelectorAll('button, input, select').forEach(el => {
        el.disabled = true;
    });
}

// Function to enable UI elements
function enableUI() {
    document.body.classList.remove('processing');
    document.querySelectorAll('button, input, select').forEach(el => {
        el.disabled = false;
    });
}

// Function to check and process queued tasks
function checkQueuedTasks() {
    if (processingQueue.length > 0 && !isProcessing) {
        const nextTask = processingQueue.shift();
        nextTask();
    }
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

    return map; // Return the map instance
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
            style: {
                color: 'red',
                weight: 2,
                fillColor: 'orange',
                fillOpacity: 0.03
            }
        }).addTo(map);

    } catch (error) {
        console.error('Error loading Waco limits:', error.message);
        showFeedback('Error loading Waco city limits', 'error');
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

        // Remove the previous live route layer if it exists
        if (liveRouteDataLayer) {
            map.removeLayer(liveRouteDataLayer);
            liveRouteDataLayer = null;
        }

        // Remove the previous live marker if it exists
        if (liveMarker) {
            map.removeLayer(liveMarker);
            liveMarker = null;
        }

        // Remove the previous live polyline if it exists
        if (liveRoutePolyline) {
            map.removeLayer(liveRoutePolyline);
            liveRoutePolyline = null;
        }

        // Error checking: Ensure data has the expected structure
        if (!data.features || !data.features[0] || !data.features[0].geometry || !data.features[0].geometry.coordinates) {
            console.error('Invalid live route data:', data);
            showFeedback('Error: Invalid live route data received from the server.', 'error');
            return; // Exit the function if the data is invalid
        }

        // Extract the LineString coordinates 
        const coordinates = data.features[0].geometry.coordinates;

        // Create the new live route polyline
        liveRoutePolyline = L.polyline(coordinates, { 
            color: '#007bff',
            weight: 4
        }).addTo(map);

        // Create the new live marker (at the last coordinate)
        if (coordinates.length > 0) {
            const lastCoord = coordinates[coordinates.length - 1];
            liveMarker = L.marker([lastCoord[1], lastCoord[0]], {
                icon: L.divIcon({
                    className: 'blinking-marker',
                    iconSize: [20, 20],
                    html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
                })
            }).addTo(map);

            // Set the map view to the last coordinate
            map.setView([lastCoord[1], lastCoord[0]], 13);
        }

    } catch (error) {
        console.error('Error loading live route data:', error);
        showFeedback('Error loading live route data', 'error');
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
        liveRoutePolyline = L.polyline([], {
            color: '#007bff',
            weight: 4
        }).addTo(map);
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

    }
}

// Function to display historical data
async function displayHistoricalData() {
    if (!historicalDataLoaded) {
        showFeedback('Historical data is not loaded yet. Please wait or refresh the page.', 'warning');
        return;
    }

    if (isLoadingHistoricalData) {
        showFeedback('Already loading historical data. Please wait.', 'info');
        return;
    }

    try {
        isLoadingHistoricalData = true;
        disableFilterButtons();
        showFeedback('Loading historical data...', 'info');

        const wacoBoundary = wacoBoundarySelect.value;
        const cacheKey = generateCacheKey();

        if (historicalDataCache[cacheKey]) {
            console.log('Using cached historical data');
            updateMapWithFilteredFeatures(historicalDataCache[cacheKey]);
        } else {
            console.log('Fetching new historical data');

            const response = await fetch(
                `/historical_data?startDate=${startDateInput.value}&endDate=${endDateInput.value}` +
                `&filterWaco=${filterWacoCheckbox.checked}&wacoBoundary=${wacoBoundary}`
            );

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const compressedData = await response.arrayBuffer();
            const decompressedData = pako.inflate(new Uint8Array(compressedData), { to: 'string' });
            const data = JSON.parse(decompressedData);

            if (!data || !data.features || data.features.length === 0) {
                showFeedback('No historical data available for the selected period.', 'warning');
                return;
            }

            cacheHistoricalData(cacheKey, data);

            updateMapWithFilteredFeatures(data);
        }
    } catch (error) {
        console.error('Error displaying historical data:', error);
        showFeedback(`Error loading historical data: ${error.message}. Please try again.`, 'error');
    } finally {
        isLoadingHistoricalData = false;
        enableFilterButtons();
    }
}

function disableFilterButtons() {
    document.querySelectorAll('#time-filters button').forEach(button => {
        button.disabled = true;
    });
    applyFilterBtn.disabled = true;
    filterWacoCheckbox.disabled = true;
    startDateInput.disabled = true;
    endDateInput.disabled = true;
    wacoBoundarySelect.disabled = true;
}

function enableFilterButtons() {
    document.querySelectorAll('#time-filters button').forEach(button => {
        button.disabled = false;
    });
    applyFilterBtn.disabled = false;
    filterWacoCheckbox.disabled = false;
    startDateInput.disabled = false;
    endDateInput.disabled = false;
    wacoBoundarySelect.disabled = false;
}

// Function to generate a cache key based on current filters and map bounds
function generateCacheKey() {
    return `${CACHE_VERSION}:${startDateInput.value}:${endDateInput.value}:` +
        `${filterWacoCheckbox.checked}:${wacoBoundarySelect.value}`;
}

// Function to cache historical data
function cacheHistoricalData(key, data) {
    if (Object.keys(historicalDataCache).length >= MAX_CACHE_SIZE) {
        const oldestKey = Object.keys(historicalDataCache)[0];
        delete historicalDataCache[oldestKey];
    }
    historicalDataCache[key] = data;
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
        showFeedback('Error updating live data and metrics', 'error');
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
    playbackPolyline = L.polyline([], {
        color: 'yellow',
        weight: 4
    }).addTo(map);

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
        playbackPolyline = null;
    }
    if (playbackMarker) {
        map.removeLayer(playbackMarker);
        playbackMarker = null;
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
    if (!historicalDataLayer || !historicalDataCache) return;

    const currentCacheKey = generateCacheKey();
    const allData = historicalDataCache[currentCacheKey];

    if (!allData || !allData.features) {
        showFeedback('No historical data available to filter.', 'warning');
        return;
    }

    const filteredFeatures = allData.features.filter(feature => {
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

    const filteredData = {
        type: 'FeatureCollection',
        features: filteredFeatures
    };

    updateMapWithFilteredFeatures(filteredData);
}

function clearDrawnShapes() {
    drawnItems.clearLayers();
    const currentCacheKey = generateCacheKey();
    const allData = historicalDataCache[currentCacheKey];
    if (allData) {
        updateMapWithFilteredFeatures(allData);
    } else {
        displayHistoricalData(); // Fallback to fetching data if it's not in the cache
    }
}

function initializeDataPolling() {
    setInterval(async () => {
        try {
            const response = await fetch('/latest_bouncie_data');
            const data = await response.json();
            if (Object.keys(data).length > 0) {
                updateLiveData(data);
            }
        } catch (error) {
            console.error('Error fetching latest data:', error);
        }
    }, 1000); // Poll every second
}

// Function to filter routes by a specific period
function filterRoutesBy(period) {
    if (isLoadingHistoricalData) {
        showFeedback('Already loading historical data. Please wait.', 'info');
        return;
    }

    const now = new Date();
    let startDate, endDate;

    switch (period) {
        case 'today':
            startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1); // Tomorrow at 00:00
            break;
        case 'yesterday':
            startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate()); // Today at 00:00
            break;
        case 'lastWeek':
            startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 7);
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1); // Tomorrow at 00:00
            break;
        case 'lastMonth':
            startDate = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate());
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1); // Tomorrow at 00:00
            break;
        case 'lastYear':
            startDate = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1); // Tomorrow at 00:00
            break;
        case 'allTime':
            startDate = new Date(2020, 0, 1); // Data starts from 2020
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1); // Tomorrow at 00:00
            break;
        default:
            // Handle invalid period (optional)
            console.error('Invalid period:', period);
            return;
    }

    startDateInput.value = startDate.toISOString().slice(0, 10);
    endDateInput.value = endDate.toISOString().slice(0, 10);
    displayHistoricalData(); // Update the map with the new date range
}

// Function to export data to GPX
async function exportToGPX() {
    showFeedback('Preparing GPX export...', 'info');
    const startDate = startDateInput.value;
    const endDate = endDateInput.value;
    const filterWaco = filterWacoCheckbox.checked;
    const wacoBoundary = wacoBoundarySelect.value;

    const url = `/export_gpx?startDate=${startDate}&endDate=${endDate}&filterWaco=${filterWaco}&wacoBoundary=${wacoBoundary}`;

    try {
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
    }
}

// Initialize WebWorker
function initializeWebWorker() {
    worker = new Worker('/static/js/worker.js');
    worker.onmessage = function (e) {
        const { action, data } = e.data;
        if (action === 'filterFeaturesResult') {
            updateMapWithFilteredFeatures(data);

            // Cache the filtered results
            const cacheKey = generateCacheKey();
            cacheHistoricalData(cacheKey, data);
        }
    };
}

function updateMapWithFilteredFeatures(data, fitBounds = true) {
    if (historicalDataLayer) {
        map.removeLayer(historicalDataLayer);
    }

    historicalDataLayer = L.geoJSON(data, {
        style: { color: 'blue', weight: 2, opacity: 0.25 },
        onEachFeature: addRoutePopup
    }).addTo(map);

    // Update total historical distance
    const totalDistance = calculateTotalDistance(data.features);
    document.getElementById('totalHistoricalDistance').textContent = `${totalDistance.toFixed(2)} miles`;

    showFeedback(`Displayed ${data.features.length} historical features`, 'success');
    enableFilterButtons();

    // Fit the map to the bounds of the historical data if requested
    if (fitBounds && data.features.length > 0) {
        map.fitBounds(historicalDataLayer.getBounds());
    }
}

// Function to check historical data status
async function checkHistoricalDataStatus() {
    try {
        const response = await fetch('/historical_data_status');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        historicalDataLoaded = data.loaded;
        historicalDataLoading = data.loading;

        if (historicalDataLoading) {
            showFeedback('Historical data is loading. Some features may be limited.', 'info');
        } else if (historicalDataLoaded) {
            showFeedback('Historical data loaded successfully.', 'success');
            await displayHistoricalData();
        } else {
            throw new Error('Historical data not loaded');
        }
    } catch (error) {
        console.error('Error checking historical data status:', error);
        showFeedback(`Error checking historical data status: ${error.message}`, 'error');

        if (historicalDataLoadAttempts < MAX_LOAD_ATTEMPTS) {
            historicalDataLoadAttempts++;
            showFeedback(`Retrying to load historical data (Attempt ${historicalDataLoadAttempts}/${MAX_LOAD_ATTEMPTS})`, 'info');
            setTimeout(checkHistoricalDataStatus, 5000); // Retry after 5 seconds
        } else {
            showFeedback('Failed to load historical data after multiple attempts. Please refresh the page or try again later.', 'error');
        }
    }
}

// Update the initializeApp function
async function initializeApp() {
    try {
        endDateInput.value = new Date().toISOString().slice(0, 10);

        showFeedback('Initializing application...', 'info');

        await loadWacoLimits(selectedWacoBoundary);
        await checkHistoricalDataStatus();

        if (!historicalDataLoaded) {
            const checkInterval = setInterval(async () => {
                await checkHistoricalDataStatus();
                if (historicalDataLoaded || historicalDataLoadAttempts >= MAX_LOAD_ATTEMPTS) {
                    clearInterval(checkInterval);
                    if (historicalDataLoaded) {
                        showFeedback('Historical data loaded successfully', 'success');
                        await displayHistoricalData();
                    }
                }
            }, 5000); // Check every 5 seconds
        }

        await loadLiveRouteData(); // Load the live route data on startup
        setInterval(updateLiveDataAndMetrics, 3000);

        showFeedback('Application initialized successfully', 'success');
    } catch (error) {
        console.error('Error initializing application:', error);
        showFeedback(`Error initializing application: ${error.message}. Please refresh the page.`, 'error');
    }
}

// Setup event listeners
function setupEventListeners() {
    applyFilterBtn.addEventListener('click', handleBackgroundTask(async () => {
        await loadWacoLimits(wacoBoundarySelect.value);
        await displayHistoricalData();
        showFeedback('Filters applied successfully', 'success');
    }, 'Applying filters...'));

    updateDataBtn.addEventListener('click', handleBackgroundTask(async () => {
        try {
            const response = await fetch('/update_historical_data', {
                method: 'POST'
            });
            const data = await response.json();
            if (response.ok) {
                showFeedback(data.message, 'success');
                await displayHistoricalData();
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            throw new Error('Error updating historical data: ' + error.message);
        }
    }, 'Checking for new driving data...'));

    wacoBoundarySelect.addEventListener('change', () => {
        selectedWacoBoundary = wacoBoundarySelect.value;
        showFeedback(`Waco boundary changed to ${selectedWacoBoundary}`, 'info');
    });

    clearRouteBtn.addEventListener('click', handleBackgroundTask(() => {
        clearLiveRoute();
        showFeedback('Live route cleared', 'info');
    }, 'Clearing live route...'));

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

    searchBtn.addEventListener('click', handleBackgroundTask(async () => {
        const query = searchInput.value;
        if (!query) {
            showFeedback('Please enter a location to search for.', 'warning');
            return;
        }

        try {
            const response = await fetch(`/search_location?query=${query}`);
            const data = await response.json();

            if (data.error) {
                throw new Error(data.error);
            } else {
                const {
                    latitude,
                    longitude,
                    address
                } = data;
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

                setTimeout(() => {
                    if (searchMarker) {
                        map.removeLayer(searchMarker);
                        searchMarker = null;
                    }
                }, 10000);
            }
        } catch (error) {
            throw new Error('Error searching for location: ' + error.message);
        }
    }, 'Searching for location...'));

    // Search input event listener with debounce
    searchInput.addEventListener('input', debounce(async () => {
        const query = searchInput.value;
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
                    suggestionElement.textContent = suggestion.address;
                    suggestionElement.addEventListener('click', () => {
                        searchInput.value = suggestion.address;
                        suggestionsContainer.innerHTML = ''; // Clear suggestions on click
                    });
                    suggestionsContainer.appendChild(suggestionElement);
                });
            }
        } catch (error) {
            console.error('Error fetching search suggestions:', error);
        }
    }, 300)); // Debounce with 300ms delay

    // Hide suggestions when Enter is pressed or Search button is clicked
    searchInput.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            suggestionsContainer.innerHTML = '';
        }
    });

    searchBtn.addEventListener('click', () => {
        suggestionsContainer.innerHTML = '';
    });

    // Event listeners for filter inputs (using 'change' event)
    filterWacoCheckbox.addEventListener('change', handleBackgroundTask(async () => {
        await loadWacoLimits(wacoBoundarySelect.value);
        await displayHistoricalData();
        showFeedback('Filters applied successfully', 'success');
    }, 'Applying filters...'));

    startDateInput.addEventListener('change', handleBackgroundTask(async () => {
        await displayHistoricalData();
        showFeedback('Filters applied successfully', 'success');
    }, 'Applying filters...'));

    endDateInput.addEventListener('change', handleBackgroundTask(async () => {
        await displayHistoricalData();
        showFeedback('Filters applied successfully', 'success');
    }, 'Applying filters...'));

    wacoBoundarySelect.addEventListener('change', handleBackgroundTask(async () => {
        await loadWacoLimits(wacoBoundarySelect.value);
        await displayHistoricalData();
        showFeedback('Filters applied successfully', 'success');
    }, 'Applying filters...'));

    // Event listener for Export to GPX button
    exportToGPXBtn.addEventListener('click', handleBackgroundTask(exportToGPX, 'Exporting to GPX...'));

    // Event listener for Clear Drawn Shapes button
    clearDrawnShapesBtn.addEventListener('click', handleBackgroundTask(clearDrawnShapes, 'Clearing drawn shapes...'));
}

// Debounce function
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

// DOMContentLoaded event listener
document.addEventListener('DOMContentLoaded', function () {
    map = initializeMap(); // Initialize the map
    initializeDataPolling();
    initializeWebWorker();
    setupEventListeners();

    // Check with the server if any long-running process is active
    fetch('/processing_status')
        .then(response => response.json())
        .       then(data => {
            if (data.isProcessing) {
                isProcessing = true;
                disableUI();
                showFeedback('A background task is in progress. Please wait.', 'info');
            } else {
                initializeApp();
            }
        })
        .catch(error => {
            console.error('Error checking processing status:', error);
            showFeedback('Error checking application status. Please refresh the page.', 'error');
        });
  });