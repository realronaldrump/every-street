// Global variables
let map, wacoLimits, liveMarker, historicalDataLayer, liveRoutePolyline, playbackAnimation,
    playbackPolyline, playbackMarker, liveRouteDataLayer;
let progressLayer, untraveledStreetsLayer, wacoStreetsLayer;
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
let historicalDataLoadAttempts = 0;
const MAX_LOAD_ATTEMPTS = 3;
let isLoadingHistoricalData = false;
let progressBar, progressText;
let wacoStreetsOpacity = 0.7;
let wacoStreetsFilter = 'all';

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
const suggestionsContainer = document.getElementById('searchSuggestions');

let searchMarker;

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

function addLayerControls() {
    const overlayMaps = {
        "Historical Data": historicalDataLayer,
        "Waco Streets": wacoStreetsLayer
    };

    L.control.layers(null, overlayMaps).addTo(map);
}

// Function to initialize the map
function initializeMap() {
    map = L.map('map').setView([31.5493, -97.1117], 13);

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19
    }).addTo(map);

    // Create map panes with correct z-index order
    map.createPane('wacoLimitsPane').style.zIndex = 400;
    map.createPane('progressPane').style.zIndex = 410;
    map.createPane('untraveledStreetsPane').style.zIndex = 420;
    map.createPane('historicalDataPane').style.zIndex = 430;
    map.createPane('wacoStreetsPane').style.zIndex = 440;

    // Add progress controls
    let progressControl = L.control({
        position: 'bottomleft'
    });
    progressControl.onAdd = function(map) {
        let div = L.DomUtil.create('div', 'progress-control');
        div.innerHTML = '<div id="progress-bar-container"><div id="progress-bar"></div></div><div id="progress-text"></div>';
        return div;
    };
    progressControl.addTo(map);

    progressBar = document.getElementById('progress-bar');
    progressText = document.getElementById('progress-text');

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
        displayHistoricalData();
    });

    return map;
}

// Function to load Waco city limits
async function loadWacoLimits(boundaryType) {
    if (!filterWacoCheckbox.checked) {
        if (wacoLimits) {
            map.removeLayer(wacoLimits);
            wacoLimits = null;
        }
        return;
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
            },
            pane: 'wacoLimitsPane'
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

    stopPlayback();

    if (liveRouteDataLayer) {
        map.removeLayer(liveRouteDataLayer);
        liveRouteDataLayer = null;
    }
}

// Function to load live route data
async function loadLiveRouteData() {
    try {
        const response = await fetch('/live_route');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        // Clear existing live route layers
        if (liveRouteDataLayer) {
            map.removeLayer(liveRouteDataLayer);
            liveRouteDataLayer = null;
        }
        if (liveMarker) {
            map.removeLayer(liveMarker);
            liveMarker = null;
        }
        if (liveRoutePolyline) {
            map.removeLayer(liveRoutePolyline);
            liveRoutePolyline = null;
        }

        // Check if live route data is valid and has coordinates
        if (data && data.features && data.features.length > 0 &&
            data.features[0].geometry && data.features[0].geometry.coordinates &&
            data.features[0].geometry.coordinates.length > 0) {

            const coordinates = data.features[0].geometry.coordinates;

            // Add live route polyline if there are at least two coordinates
            if (coordinates.length > 1) {
                liveRoutePolyline = L.polyline(coordinates.map(coord => [coord[1], coord[0]]), {
                    color: '#007bff',
                    weight: 4
                }).addTo(map);
            }

            // Add live marker at the last coordinate
            if (coordinates.length > 0) {
                const lastCoord = coordinates[coordinates.length - 1];
                liveMarker = L.marker([lastCoord[1], lastCoord[0]], {
                    icon: L.divIcon({
                        className: 'blinking-marker',
                        iconSize: [20, 20],
                        html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
                    })
                }).addTo(map);

                // Center the map on the last coordinate
                map.setView([lastCoord[1], lastCoord[0]], 13);
            }

        } else {
            console.warn('Live route data is missing or incomplete:', data);
        }

    } catch (error) {
        console.error('Error loading live route data:', error);
        showFeedback('Error loading live route data', 'error');
    }
}

async function loadProgressData() {
    try {
        const response = await fetch('/progress_geojson');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        if (progressLayer) {
            map.removeLayer(progressLayer);
        }

        progressLayer = L.geoJSON(data, {
            style: function(feature) {
                return {
                    color: feature.properties.traveled ? '#00ff00' : '#ff0000',
                    weight: 2,
                    opacity: 0.7
                };
            },
            pane: 'progressPane'
        }).addTo(map);
    } catch (error) {
        console.error('Error loading progress data:', error);
        showFeedback('Error loading progress data. Please try again.', 'error');
    }
}

async function updateProgress() {
    try {
        const response = await fetch('/update_progress', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        progressBar.style.width = `${data.progress}%`;
        progressText.textContent = `${data.progress.toFixed(2)}% of Waco streets traveled`;
    } catch (error) {
        console.error('Error updating progress:', error);
        showFeedback(error.message, 'error');
    }
}

async function loadUntraveledStreets() {
    try {
        const response = await fetch(`/untraveled_streets?wacoBoundary=${wacoBoundarySelect.value}`);
        if (!response.ok) {
            throw new Error(`Error loading untraveled streets: ${response.status} ${response.statusText}`);
        }

        const data = await response.json();

        if (untraveledStreetsLayer) {
            map.removeLayer(untraveledStreetsLayer);
        }

        untraveledStreetsLayer = L.geoJSON(data, {
            style: {
                color: '#FFFFFF',
                weight: 2,
                opacity: 0.8
            },
            filter: feature => !feature.properties.traveled,
            pane: 'untraveledStreetsPane'
        }).addTo(map);
    } catch (error) {
        console.error('Error loading untraveled streets:', error);
        showFeedback(error.message, 'error');
    }
}

async function toggleWacoStreets() {
    if (wacoStreetsLayer) {
        map.removeLayer(wacoStreetsLayer);
        wacoStreetsLayer = null;
        showFeedback('Waco streets hidden', 'info');
    } else {
        await loadWacoStreets();
    }
}

async function loadWacoStreets() {
    try {
        const response = await fetch(`/waco_streets?wacoBoundary=${wacoBoundarySelect.value}&filter=${wacoStreetsFilter}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        if (wacoStreetsLayer) {
            map.removeLayer(wacoStreetsLayer);
        }

        wacoStreetsLayer = L.geoJSON(data, {
            style: function(feature) {
                return {
                    color: feature.properties.traveled ? '#00FF00' : '#FF0000',
                    weight: 2,
                    opacity: wacoStreetsOpacity
                };
            },
            filter: function(feature) {
                if (wacoStreetsFilter === 'all') return true;
                if (wacoStreetsFilter === 'traveled') return feature.properties.traveled;
                if (wacoStreetsFilter === 'untraveled') return !feature.properties.traveled;
            },
            pane: 'wacoStreetsPane'
        }).addTo(map);

        showFeedback('Waco streets displayed', 'success');
    } catch (error) {
        console.error('Error loading Waco streets:', error);
        showFeedback('Error loading Waco streets', 'error');
    }
}

async function initializeApp() {
    try {
        endDateInput.value = new Date().toISOString().slice(0, 10);

        showFeedback('Initializing application...', 'info');

        map = initializeMap();

        await loadWacoLimits(selectedWacoBoundary);
        await updateProgress();
        await loadUntraveledStreets();
        await loadProgressData();
        await loadWacoStreets();

        setInterval(loadProgressData, 300000);
        setInterval(updateProgress, 60000);
        setInterval(loadUntraveledStreets, 300000);

        await new Promise(resolve => {
            const checkInterval = setInterval(async () => {
                await checkHistoricalDataStatus();
                if (historicalDataLoaded || historicalDataLoadAttempts >= MAX_LOAD_ATTEMPTS) {
                    clearInterval(checkInterval);
                    if (historicalDataLoaded) {
                        showFeedback('Historical data loaded successfully', 'success');
                        displayHistoricalData();
                    }
                    resolve();
                }
            }, 5000);
        });

        await loadLiveRouteData();
        setInterval(updateLiveDataAndMetrics, 3000);

        // Ensure layers are visible
        if (wacoLimits) map.addLayer(wacoLimits);
        if (progressLayer) map.addLayer(progressLayer);
        if (untraveledStreetsLayer) map.addLayer(untraveledStreetsLayer);
        if (wacoStreetsLayer) map.addLayer(wacoStreetsLayer);
        if (historicalDataLayer) map.addLayer(historicalDataLayer);
        if (liveRoutePolyline) map.addLayer(liveRoutePolyline);
        if (liveMarker) map.addLayer(liveMarker);

        showFeedback('Application initialized successfully', 'success');
    } catch (error) {
        console.error('Error initializing application:', error);
        showFeedback(`Error initializing application: ${error.message}. Please refresh the page.`, 'error');
    }
}

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

async function displayHistoricalData() {
    let isLoadingHistoricalData = true;
    try {
        if (!historicalDataLoaded) {
            showFeedback('Historical data is not loaded yet. Please wait or refresh the page.', 'warning');
            return;
        }

        if (isLoadingHistoricalData) {
            showFeedback('Already loading historical data. Please wait.', 'info');
            return;
        }

        isLoadingHistoricalData = true;
        disableFilterButtons();
        showFeedback('Loading historical data...', 'info');

        const wacoBoundary = wacoBoundarySelect.value;

        const response = await fetch(
            `/historical_data?startDate=${startDateInput.value}&endDate=${endDateInput.value}` +
            `&filterWaco=${filterWacoCheckbox.checked}&wacoBoundary=${wacoBoundary}`
        );

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        if (!data || !data.features || data.features.length === 0) {
            showFeedback('No historical data available for the selected period.', 'warning');
            return;
        }

        // Send data to web worker for filtering (Assuming worker is initialized)
        worker.postMessage({
            action: 'filterFeatures',
            data: {
                features: data.features,
                bounds: map.getBounds().toBBoxString().split(',').map(Number)
            }
        });

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

function addRoutePopup(feature, layer) {
    const timestamp = feature.properties.timestamp;
    const date = new Date(timestamp * 1000).toLocaleDateString();
    const time = new Date(timestamp * 1000).toLocaleTimeString();
    const distance = calculateTotalDistance([feature]);

    const playbackButton = document.createElement('button');
    playbackButton.textContent = 'Play Route';
    playbackButton.addEventListener('click', () => {
        if (feature.geometry.type === 'LineString' && feature.geometry.coordinates.length > 1) {
            startPlayback(feature.geometry.coordinates);
        } else if (feature.geometry.type === 'MultiLineString') {
            const validSegments = feature.geometry.coordinates.filter(segment => segment.length > 1);
            if (validSegments.length > 0) {
                validSegments.forEach(segment => {
                    startPlayback(segment);
                });
            }
        }
    });

    const popupContent = document.createElement('div');
    popupContent.innerHTML = `Date: ${date}<br>Time: ${time}<br>Distance: ${distance.toFixed(2)} miles`;
    popupContent.appendChild(playbackButton);

    layer.bindPopup(popupContent);
}

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

function togglePlayPause() {
    isPlaying = !isPlaying;
    playPauseBtn.textContent = isPlaying ? 'Pause' : 'Play';
}

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

function adjustPlaybackSpeed() {
    playbackSpeed = parseFloat(playbackSpeedInput.value);
    speedValueSpan.textContent = playbackSpeed.toFixed(1) + 'x';
    if (playbackAnimation) {
        clearInterval(playbackAnimation);
        startPlayback(playbackPolyline.getLatLngs());
    }
}

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

    const filteredData = {
        type: 'FeatureCollection',
        features: filteredFeatures
    };

    updateMapWithHistoricalData(filteredData);
}

function clearDrawnShapes() {
    drawnItems.clearLayers();
    displayHistoricalData(); // Reload all data after clearing shapes
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
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
            break;
        case 'yesterday':
            startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            break;
        case 'lastWeek':
            startDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 7);
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
            break;
        case 'lastMonth':
            startDate = new Date(now.getFullYear(), now.getMonth() - 1, now.getDate());
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
            break;
        case 'lastYear':
            startDate = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
            break;
        case 'allTime':
            startDate = new Date(2020, 0, 1);
            endDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
            break;
        default:
            console.error('Invalid period:', period);
            return;
    }

    startDateInput.value = startDate.toISOString().slice(0, 10);
    endDateInput.value = endDate.toISOString().slice(0, 10);
    displayHistoricalData();
}

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

function initializeWebWorker() {
    worker = new Worker('/static/js/worker.js');
    worker.onmessage = function(e) {
        const { action, data } = e.data;
        if (action === 'filterFeaturesResult') {
            // Update the map with the filtered data from the worker
            updateMapWithHistoricalData({ type: 'FeatureCollection', features: data }); 
        }
    };
}
function updateMapWithHistoricalData(data) {
    if (historicalDataLayer) {
        map.removeLayer(historicalDataLayer);
    }

    historicalDataLayer = L.geoJSON(data, {
        style: {
            color: '#0000FF', // Blue color for historical data
            weight: 3,
            opacity: 0.7
        },
        onEachFeature: addRoutePopup,
        filter: function(feature) {
            return feature.geometry && feature.geometry.coordinates && feature.geometry.coordinates.length > 0;
        },
        pane: 'historicalDataPane'
    });

    // Add the layer to the map if it's not already there
    if (!map.hasLayer(historicalDataLayer)) {
        historicalDataLayer.addTo(map);
    }

    const totalDistance = calculateTotalDistance(data.features);
    document.getElementById('totalHistoricalDistance').textContent = `${totalDistance.toFixed(2)} miles`;

    showFeedback(`Displayed ${data.features.length} historical features`, 'success');

    if (data.features.length > 0) {
        const bounds = historicalDataLayer.getBounds();
        if (bounds.isValid()) {
            map.fitBounds(bounds);
        } else {
            console.warn('Invalid bounds for historical data');
        }
    }
}

async function checkHistoricalDataStatus() {
    try {
        const response = await fetch('/historical_data_status');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        console.log('Historical data status:', data);
        historicalDataLoaded = data.loaded;
        historicalDataLoading = data.loading;

        if (historicalDataLoading) {
            showFeedback('Historical data is loading. Some features may be limited.', 'info');
            setTimeout(checkHistoricalDataStatus, 5000); // Check again in 5 seconds
        } else if (historicalDataLoaded) {
            showFeedback('Historical data loaded successfully.', 'success');
            await displayHistoricalData(); // Display data only once when loaded
        } else {
            throw new Error('Historical data not loaded');
        }
    } catch (error) {
        console.error('Error checking historical data status:', error);
        showFeedback(`Error checking historical data status: ${error.message}`, 'error');

        if (historicalDataLoadAttempts < MAX_LOAD_ATTEMPTS) {
            historicalDataLoadAttempts++;
            showFeedback(`Retrying to load historical data (Attempt ${historicalDataLoadAttempts}/${MAX_LOAD_ATTEMPTS})`, 'info');
            setTimeout(checkHistoricalDataStatus, 5000); // Retry in 5 seconds
        } else {
            showFeedback('Failed to load historical data after multiple attempts. Please refresh the page or try again later.', 'error');
        }
    }
}

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
                await updateProgress();
                await loadUntraveledStreets();
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
            suggestionsContainer.innerHTML = '';
        }
    });

    searchBtn.addEventListener('click', () => {
        suggestionsContainer.innerHTML = '';
    });

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

    exportToGPXBtn.addEventListener('click', handleBackgroundTask(exportToGPX, 'Exporting to GPX...'));
    clearDrawnShapesBtn.addEventListener('click', handleBackgroundTask(clearDrawnShapes, 'Clearing drawn shapes...'));

    // New event listeners for Waco streets controls
    document.querySelector('.toggle-untraveled-btn').addEventListener('click', toggleWacoStreets);

    document.getElementById('opacity-slider').addEventListener('input', function(e) {
        wacoStreetsOpacity = parseFloat(e.target.value);
        if (wacoStreetsLayer) {
            wacoStreetsLayer.setStyle({
                opacity: wacoStreetsOpacity
            });
        }
    });

    document.getElementById('streets-select').addEventListener('change', function(e) {
        wacoStreetsFilter = e.target.value;
        if (wacoStreetsLayer) {
            loadWacoStreets();
        }
    });

    // Checkbox for showing/hiding historical data
    document.getElementById('showHistoricalData').addEventListener('change', function(e) {
        if (e.target.checked) {
            map.addLayer(historicalDataLayer);
        } else {
            map.removeLayer(historicalDataLayer);
        }
    });

    // Checkbox for showing/hiding Waco streets
    document.getElementById('showWacoStreets').addEventListener('change', function(e) {
        if (e.target.checked) {
            loadWacoStreets();
        } else {
            if (wacoStreetsLayer) {
                map.removeLayer(wacoStreetsLayer);
            }
        }
    });

    // Select for filtering Waco streets
    document.getElementById('wacoStreetsFilter').addEventListener('change', function(e) {
        wacoStreetsFilter = e.target.value;
        if (wacoStreetsLayer) {
            loadWacoStreets();
        }
    });
}

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

document.addEventListener('DOMContentLoaded', function() {
    initializeWebWorker();
    checkHistoricalDataStatus(); // Start checking historical data status

    // Load initial live route data before starting polling
    loadLiveRouteData()
        .then(() => {
            initializeDataPolling();

            fetch('/processing_status')
                .then(response => response.json())
                .then(data => {
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
            })
            .catch(error => {
                console.error('Error loading initial live route data:', error);
                showFeedback('Error loading initial live route data. Please refresh the page.', 'error');
            });
        setupEventListeners(); // Call after DOM is ready
    });