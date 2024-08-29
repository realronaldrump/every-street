// map.js

// Global variables
let map, wacoLimits, liveMarker, historicalDataLayer, liveRoutePolyline, playbackAnimation,
    playbackPolyline, playbackMarker, liveRouteDataLayer;
let progressLayer, wacoStreetsLayer;
let playbackSpeed = 1;
let isPlaying = false;
let currentCoordIndex = 0;
let drawnItems;
let selectedWacoBoundary = 'less_goofy';
let historicalDataLoaded = false;
let historicalDataLoading = false;
let isProcessing = false;
const processingQueue = [];
let historicalDataLoadAttempts = 0;
const MAX_LOAD_ATTEMPTS = 3;
let isLoadingHistoricalData = false;
let progressBar, progressText;
let wacoStreetsOpacity = 0.7;
let wacoStreetsFilter = 'all'; // Default to showing all streets
let untraveledStreetsLayer = null;

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
const logoutBtn = document.getElementById('logoutBtn');
const toggleUntraveledBtn = document.getElementById('toggleUntraveledBtn');
const toggleWacoStreetsBtn = document.getElementById('toggleWacoStreetsBtn');
const hideWacoStreetsBtn = document.getElementById('hideWacoStreetsBtn');
const resetProgressBtn = document.getElementById('resetProgressBtn');
const streetsSelect = document.getElementById('streets-select');

let searchMarker;

// Enhanced feedback function
function showFeedback(message, type = 'info', duration = 5000) {
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

// Function to handle background tasks and UI locking
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

document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('#time-filters button').forEach(button => {
        button.addEventListener('click', function() {
            const period = this.getAttribute('data-filter');
            filterRoutesBy(period);
        });
    });
});

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
    [liveRoutePolyline, liveMarker, liveRouteDataLayer].forEach(layer => {
        if (layer && map) {
            map.removeLayer(layer);
        }
    });

    liveRoutePolyline = null;
    liveMarker = null;
    liveRouteDataLayer = null;

    stopPlayback();
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
        clearLiveRoute();

        // Check if live route data is valid and has coordinates
        if (data?.features?.[0]?.geometry?.coordinates?.length > 0) {
            const coordinates = data.features[0].geometry.coordinates;

            // Add live route polyline if there are at least two coordinates
            if (coordinates.length > 1) {
                liveRoutePolyline = L.polyline(coordinates.map(coord => [coord[1], coord[0]]), {
                    color: '#007bff',
                    weight: 4
                });
                if (map) {
                    liveRoutePolyline.addTo(map);
                }
            }

            // Add live marker at the last coordinate
            if (coordinates.length > 0) {
                const lastCoord = coordinates[coordinates.length - 1];
                liveMarker = createAnimatedMarker([lastCoord[1], lastCoord[0]], {
                    icon: L.divIcon({
                        className: 'blinking-marker',
                        iconSize: [20, 20],
                        html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
                    })
                });
                if (map) {
                    liveMarker.addTo(map);
                }

                // Center the map on the last coordinate
                if (map) {
                    map.setView([lastCoord[1], lastCoord[0]], 13);
                }
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
        const response = await fetch(`/progress_geojson?wacoBoundary=${wacoBoundarySelect.value}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();

        if (progressLayer && map) {
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
        });
        if (map) {
            progressLayer.addTo(map);
        }
    } catch (error) {
        console.error('Error loading progress data:', error);
        showFeedback('Error loading progress data. Please try again.', 'error');
    }
}

async function updateProgress() {
    try {
        const response = await fetch('/progress');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        console.log("Progress data:", data);

        const progressBar = document.getElementById('progress-bar');
        const progressText = document.getElementById('progress-text');

        if (progressBar && progressText && data.coverage_percentage !== undefined) {
            progressBar.style.width = `${data.coverage_percentage}%`;
            progressText.textContent = `${data.coverage_percentage.toFixed(2)}% of Waco Streets Traveled`;

            // Animate the progress update
            progressBar.classList.add('animate__animated', 'animate__pulse');
            progressText.classList.add('animate__animated', 'animate__bounce');

            setTimeout(() => {
                progressBar.classList.remove('animate__animated', 'animate__pulse');
                progressText.classList.remove('animate__animated', 'animate__bounce');
            }, 1000);
        } else {
            console.error("Invalid progress data received or DOM elements not found");
        }
    } catch (error) {
        console.error('Error fetching progress:', error);
    }
}

async function toggleUntraveledStreets() {
    console.log("Toggling untraveled streets");
    if (untraveledStreetsLayer && map) {
        console.log("Removing untraveled streets layer");
        map.removeLayer(untraveledStreetsLayer);
        untraveledStreetsLayer = null;
        showFeedback('Untraveled streets hidden', 'info');
    } else {
        console.log("Loading untraveled streets");
        await loadUntraveledStreets();
    }
}

async function loadUntraveledStreets() {
    try {
        console.log(`Fetching untraveled streets: boundary=${wacoBoundarySelect.value}`);
        const response = await fetch(`/untraveled_streets?wacoBoundary=${wacoBoundarySelect.value}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        console.log(`Received untraveled streets data: ${data.features.length} features`);

        untraveledStreetsLayer = L.geoJSON(data, {
            style: function() {
                return {
                    color: '#FF0000',  // Red color for untraveled streets
                    weight: 2,
                    opacity: 0.3
                };
            },
            pane: 'untraveledStreetsPane'
        });
        if (map) {
            untraveledStreetsLayer.addTo(map);
        }

        console.log("Added untraveled streets layer to map");
        showFeedback('Untraveled streets displayed', 'success');
    } catch (error) {
        console.error('Error loading untraveled streets:', error);
        showFeedback('Error loading untraveled streets', 'error');
    }
}

async function toggleWacoStreets() {
    console.log("Toggling Waco streets");
    if (wacoStreetsLayer && map) {
        console.log("Removing Waco streets layer");
        map.removeLayer(wacoStreetsLayer);
        wacoStreetsLayer = null;
        showFeedback('Waco streets hidden', 'info');
    } else {
        console.log("Loading Waco streets");
        await loadWacoStreets();
    }
}

async function loadWacoStreets() {
    try {
        console.log(`Fetching Waco streets: boundary=${wacoBoundarySelect.value}, filter=${wacoStreetsFilter}`);
        const response = await fetch(`/waco_streets?wacoBoundary=${wacoBoundarySelect.value}&filter=${wacoStreetsFilter}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        console.log(`Received Waco streets data: ${data.features.length} features`);

        if (wacoStreetsLayer && map) {
            console.log("Removing existing Waco streets layer");
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
        });
        if (map) {
            wacoStreetsLayer.addTo(map);
        }

        console.log("Added Waco streets layer to map");
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

        await Promise.all([
            loadWacoLimits(selectedWacoBoundary),
            updateProgress(),
            loadUntraveledStreets(),
            loadProgressData(),
            loadWacoStreets()
        ]);

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
        [wacoLimits, progressLayer, untraveledStreetsLayer, wacoStreetsLayer, 
         historicalDataLayer, liveRoutePolyline, liveMarker].forEach(layer => {
            if (layer && map) map.addLayer(layer);
        });

        showFeedback('Application initialized successfully', 'success');
    } catch (error) {
        console.error('Error initializing application:', error);
        showFeedback(`Error initializing application: ${error.message}. Please refresh the page.`, 'error');
    }
}

function updateLiveData(data) {
    animateStatUpdate('lastUpdated', new Date(data.timestamp * 1000).toLocaleString());
    animateStatUpdate('speed', `${data.speed} mph`);
    animateStatUpdate('location', data.address ? data.address.split('<br>').join('\n') : 'Address not available');

    const latLng = [data.latitude, data.longitude];

    if (liveMarker && map) {
        map.removeLayer(liveMarker);
    }
    liveMarker = createAnimatedMarker(latLng, {
        icon: L.divIcon({
            className: 'blinking-marker animate__animated animate__pulse animate__infinite',
            iconSize: [20, 20],
            html: '<div style="background-color: blue; width: 100%; height: 100%; border-radius: 50%;"></div>'
        })
    });
    if (map) {
        liveMarker.addTo(map);
    }

    if (liveRouteDataLayer && map) {
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
        });
        if (map) {
            liveRoutePolyline.addTo(map);
        }
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
    if (isLoadingHistoricalData) {
        showFeedback('Already loading historical data. Please wait.', 'info');
        return;
    }

    isLoadingHistoricalData = true;
    disableFilterButtons();
    showLoading('Loading historical data...');

    try {
        const startDateStr = startDateInput.value;
        const endDateStr = endDateInput.value;
        const filterWaco = filterWacoCheckbox.checked;
        const wacoBoundary = wacoBoundarySelect.value;

        const response = await fetch(
            `/historical_data?startDate=${startDateStr}&endDate=${endDateStr}` +
            `&filterWaco=${filterWaco}&wacoBoundary=${wacoBoundary}`
        );

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        if (historicalDataLayer && map) {
            map.removeLayer(historicalDataLayer);
        }

        historicalDataLayer = L.geoJSON(data, {
            style: {
                color: '#0000FF',
                weight: 3,
                opacity: 0.7
            },
            onEachFeature: addRoutePopup
        });
        if (map) {
            historicalDataLayer.addTo(map);
        }

        const totalDistance = calculateTotalDistance(data.features);
        animateStatUpdate('totalHistoricalDistance', `${totalDistance.toFixed(2)} miles`);

        showFeedback(`Displayed ${data.features.length} historical features`, 'success');

        if (data.features.length > 0 && map) {
            map.fitBounds(historicalDataLayer.getBounds());
        }

        // Animate the historical data layer
        if (historicalDataLayer) {
            historicalDataLayer.eachLayer(function (layer) {
                if (layer.getElement()) {
                    layer.getElement().classList.add('animate__animated', 'animate__fadeIn');
                }
            });
        }
    } catch (error) {
        console.error('Error displaying historical data:', error);
        showFeedback(`Error loading historical data: ${error.message}. Please try again.`, 'error');
    } finally {
        isLoadingHistoricalData = false;
        enableFilterButtons();
        hideLoading();
    }
}

function disableFilterButtons() {
    ['#time-filters button', '#applyFilterBtn', '#filterWaco', '#startDate', '#endDate', '#wacoBoundarySelect']
        .forEach(selector => {
            document.querySelectorAll(selector).forEach(el => el.disabled = true);
        });
}

function enableFilterButtons() {
    ['#time-filters button', '#applyFilterBtn', '#filterWaco', '#startDate', '#endDate', '#wacoBoundarySelect']
        .forEach(selector => {
            document.querySelectorAll(selector).forEach(el => el.disabled = false);
        });
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
        ['totalDistance', 'totalTime', 'maxSpeed', 'startTime', 'endTime'].forEach(id => {
            animateStatUpdate(id, metrics[id.toLowerCase()]);
        });
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
    playbackButton.classList.add('animate__animated', 'animate__pulse');
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
        [playbackPolyline, playbackMarker].forEach(layer => {
            if (layer && map) map.removeLayer(layer);
        });
    }

    currentCoordIndex = 0;
    playbackPolyline = L.polyline([], {
        color: 'yellow',
        weight: 4
    });
    if (map) {
        playbackPolyline.addTo(map);
    }

    playbackMarker = createAnimatedMarker(L.latLng(coordinates[0][1], coordinates[0][0]), {
        icon: L.divIcon({
            className: 'blinking-marker animate__animated animate__bounce',
            iconSize: [20, 20],
            html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
        })
    });
    if (map) {
        playbackMarker.addTo(map);
    }

    isPlaying = true;
    playPauseBtn.textContent = 'Pause';
    playbackAnimation = setInterval(() => {
        if (isPlaying && currentCoordIndex < coordinates.length) {
            const latLng = L.latLng(coordinates[currentCoordIndex][1], coordinates[currentCoordIndex][0]);
            if (playbackMarker) playbackMarker.setLatLng(latLng);
            if (playbackPolyline) playbackPolyline.addLatLng(latLng);
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
    playPauseBtn.classList.add('animate__animated', 'animate__pulse');
    setTimeout(() => playPauseBtn.classList.remove('animate__animated', 'animate__pulse'), 1000);
}

function stopPlayback() {
    isPlaying = false;
    playPauseBtn.textContent = 'Play';
    currentCoordIndex = 0;
    if (playbackAnimation) {
        clearInterval(playbackAnimation);
    }
    [playbackPolyline, playbackMarker].forEach(layer => {
        if (layer && map) {
            map.removeLayer(layer);
        }
    });
    playbackPolyline = null;
    playbackMarker = null;
}

function adjustPlaybackSpeed() {
    playbackSpeed = parseFloat(playbackSpeedInput.value);
    speedValueSpan.textContent = playbackSpeed.toFixed(1) + 'x';
    if (playbackAnimation) {
        clearInterval(playbackAnimation);
        startPlayback(playbackPolyline.getLatLngs());
    }
    speedValueSpan.classList.add('animate__animated', 'animate__rubberBand');
    setTimeout(() => speedValueSpan.classList.remove('animate__animated', 'animate__rubberBand'), 1000);
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

    updateMapWithHistoricalData({
        type: 'FeatureCollection',
        features: filteredFeatures
    });
}

function clearDrawnShapes() {
    if (drawnItems) {
        drawnItems.clearLayers();
    }
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
            startDate = new Date(2020, 0, 1);  // Assuming data starts from 2020
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
    showLoading('Preparing GPX export...');
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
    } finally {
        hideLoading();
    }
}

function updateMapWithHistoricalData(data) {
    if (historicalDataLayer && map) {
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

    if (map && !map.hasLayer(historicalDataLayer)) {
        historicalDataLayer.addTo(map);
    }

    const totalDistance = calculateTotalDistance(data.features);
    animateStatUpdate('totalHistoricalDistance', `${totalDistance.toFixed(2)} miles`);

    showFeedback(`Displayed ${data.features.length} historical features`, 'success');

    if (data.features.length > 0 && historicalDataLayer) {
        const bounds = historicalDataLayer.getBounds();
        if (bounds.isValid() && map) {
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
    if (applyFilterBtn) {
        applyFilterBtn.addEventListener('click', handleBackgroundTask(async () => {
            await loadWacoLimits(wacoBoundarySelect.value);
            await displayHistoricalData();
            await loadProgressData(); // Update progress layer based on new filters
            await loadUntraveledStreets(); // Update untraveled streets based on new filters
            await loadWacoStreets(); // Update Waco streets based on new filters
            showFeedback('Filters applied successfully', 'success');
        }, 'Applying filters...'));
    }

    if (updateDataBtn) {
        updateDataBtn.addEventListener('click', handleBackgroundTask(async () => {
            try {
                const response = await fetch('/update_historical_data', { method: 'POST' });
                const data = await response.json();
                if (response.ok) {
                    showFeedback(data.message, 'success');
                    await Promise.all([
                        displayHistoricalData(),
                        updateProgress(), // Call this after updating historical data
                        loadUntraveledStreets()
                    ]);
                } else {
                    throw new Error(data.error);
                }
            } catch (error) {
                throw new Error('Error updating historical data: ' + error.message);
            }
        }, 'Checking for new driving data...'));
    }

    if (wacoBoundarySelect) {
        wacoBoundarySelect.addEventListener('change', handleBackgroundTask(async () => {
            selectedWacoBoundary = wacoBoundarySelect.value;
            await loadWacoLimits(selectedWacoBoundary);
            await displayHistoricalData();
            await loadProgressData(); // Update progress layer based on new boundary
            await loadUntraveledStreets(); // Update untraveled streets based on new boundary
            await loadWacoStreets(); // Update Waco streets based on new boundary
            showFeedback(`Waco boundary changed to ${selectedWacoBoundary}`, 'success');
        }, 'Changing Waco boundary...'));
    }

    if (clearRouteBtn) {
        clearRouteBtn.addEventListener('click', handleBackgroundTask(() => {
            clearLiveRoute();
            showFeedback('Live route cleared', 'info');
        }, 'Clearing live route...'));
    }

    if (playPauseBtn) {
        playPauseBtn.addEventListener('click', () => {
            togglePlayPause();
            showFeedback(isPlaying ? 'Playback resumed' : 'Playback paused', 'info');
        });
    }

    if (stopBtn) {
        stopBtn.addEventListener('click', () => {
            stopPlayback();
            showFeedback('Playback stopped', 'info');
        });
    }

    if (playbackSpeedInput) {
        playbackSpeedInput.addEventListener('input', () => {
            adjustPlaybackSpeed();
            showFeedback(`Playback speed set to ${playbackSpeed.toFixed(1)}x`, 'info');
        });
    }

    if (searchBtn && searchInput) {
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
                    const { latitude, longitude, address } = data;
                    if (map) {
                        map.setView([latitude, longitude], 13);
                    }

                    if (searchMarker && map) {
                        map.removeLayer(searchMarker);
                    }

                    searchMarker = createAnimatedMarker([latitude, longitude], {
                        icon: L.divIcon({
                            className: 'custom-marker animate__animated animate__bounceInDown',
                            iconSize: [30, 30],
                            html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
                        })
                    });
                    if (map) {
                        searchMarker.addTo(map)
                            .bindPopup(`<b>${address}</b>`)
                            .openPopup();
                    }

                    showFeedback(`Found location: ${address}`, 'success');

                    setTimeout(() => {
                        if (searchMarker && map) {
                            map.removeLayer(searchMarker);
                            searchMarker = null;
                        }
                    }, 10000);
                }
            } catch (error) {
                throw new Error('Error searching for location: ' + error.message);
            }
        }, 'Searching for location...'));
    }

    if (searchInput) {
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
                suggestionsContainer.innerHTML = '';
            }
        });
    }

    if (searchBtn) {
        searchBtn.addEventListener('click', () => {
            suggestionsContainer.innerHTML = '';
        });
    }

    const filterHandlers = [
        { element: filterWacoCheckbox, action: loadWacoLimits },
        { element: startDateInput, action: displayHistoricalData },
        { element: endDateInput, action: displayHistoricalData },
        { element: wacoBoundarySelect, action: loadWacoLimits }
    ];

    filterHandlers.forEach(({ element, action }) => {
        if (element) {
            element.addEventListener('change', handleBackgroundTask(async () => {
                await action(element.value);
                await displayHistoricalData();
                await loadProgressData(); // Update progress layer based on new filters
                await loadUntraveledStreets(); // Update untraveled streets based on new filters
                await loadWacoStreets(); // Update Waco streets based on new filters
                showFeedback('Filters applied successfully', 'success');
            }, 'Applying filters...'));
        }
    });

    if (exportToGPXBtn) {
        exportToGPXBtn.addEventListener('click', handleBackgroundTask(exportToGPX, 'Exporting to GPX...'));
    }

    if (clearDrawnShapesBtn) {
        clearDrawnShapesBtn.addEventListener('click', handleBackgroundTask(clearDrawnShapes, 'Clearing drawn shapes...'));
    }

    if (toggleUntraveledBtn) {
        toggleUntraveledBtn.addEventListener('click', handleBackgroundTask(toggleUntraveledStreets, 'Toggling untraveled streets...'));
    }

    if (toggleWacoStreetsBtn) {
        toggleWacoStreetsBtn.addEventListener('click', handleBackgroundTask(toggleWacoStreets, 'Toggling Waco streets...'));
    }

    if (resetProgressBtn) {
        resetProgressBtn.addEventListener('click', handleBackgroundTask(async () => {
            try {
                const response = await fetch('/reset_progress', { method: 'POST' });
                const data = await response.json();
                if (response.ok) {
                    showFeedback(data.message, 'success');
                    await Promise.all([
                        updateProgress(), // Call this after resetting progress
                        loadUntraveledStreets(),
                        loadWacoStreets(),
                        loadProgressData() // Update progress layer after reset
                    ]);
                } else {
                    throw new Error(data.error);
                }
            } catch (error) {
                throw new Error('Error resetting progress: ' + error.message);
            }
        }, 'Resetting progress...'));
    }

    if (hideWacoStreetsBtn) {
        hideWacoStreetsBtn.addEventListener('click', () => {
            console.log("Hide Waco streets button clicked");
            if (wacoStreetsLayer && map) {
                map.removeLayer(wacoStreetsLayer);
                wacoStreetsLayer = null;
                showFeedback('Waco streets hidden', 'info');
            } else {
                console.warn("wacoStreetsLayer is not initialized or map is not available");
            }
        });
    }

    if (streetsSelect) {
        streetsSelect.addEventListener('change', function(e) {
            wacoStreetsFilter = e.target.value;
            if (wacoStreetsLayer) {
                loadWacoStreets();
            }
        });
    }

    if (logoutBtn) {
        logoutBtn.addEventListener('click', () => {
            window.location.href = '/logout';
        });
    }
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

function showLoading(message = 'Loading...') {
    const loadingOverlay = document.getElementById('loadingOverlay');
    if (loadingOverlay) {
        const loadingText = loadingOverlay.querySelector('.loading-text');
        if (loadingText) {
            loadingText.textContent = message;
        }
        loadingOverlay.style.display = 'flex';
    }
}

function hideLoading() {
    const loadingOverlay = document.getElementById('loadingOverlay');
    if (loadingOverlay) {
        loadingOverlay.style.display = 'none';
    }
}

function createAnimatedMarker(latLng, options = {}) {
    const defaultIcon = L.divIcon({
        className: 'custom-marker animate__animated animate__bounce',
        html: '<div style="background-color: #007bff; width: 100%; height: 100%; border-radius: 50%;"></div>',
        iconSize: [20, 20]
    });

    return L.marker(latLng, { icon: defaultIcon, ...options });
}

function animateStatUpdate(elementId, newValue) {
    const element = document.getElementById(elementId);
    if (element) {
        element.classList.add('animate__animated', 'animate__flipInX');
        element.textContent = newValue;
        setTimeout(() => {
            element.classList.remove('animate__animated', 'animate__flipInX');
        }, 1000);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    checkHistoricalDataStatus(); // Start checking historical data status

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
    setupEventListeners();
    updateProgress();
});