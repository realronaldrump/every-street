// Map initialization
const map = L.map('map').setView([31.5493, -97.1117], 13); // Centered on Waco, TX

L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
  maxZoom: 19
}).addTo(map);

// Global variables
let wacoLimits;
let liveMarker;
let historicalDataLayer;
let liveRoutePolyline; // Store the live route polyline
let liveRoutePoints = []; // Store points for the live route
let playbackAnimation; // Store the animation object
let playbackPolyline; // Store the polyline used for playback

// DOM elements
const filterWacoCheckbox = document.getElementById('filterWaco');
const startDateInput = document.getElementById('startDate');
const endDateInput = document.getElementById('endDate');
const updateDataBtn = document.getElementById('updateDataBtn'); // New button

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

  // Split the address by <br> tags and join with newlines
  const formattedAddress = data.address.split('<br>').join('\n');
  document.getElementById('location').textContent = formattedAddress; 

  const latLng = [data.latitude, data.longitude];

  // Update live marker
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

  // Update live route
  liveRoutePoints.push(latLng);

  if (!liveRoutePolyline) {
    liveRoutePolyline = L.polyline(liveRoutePoints, {
      color: '#007bff', // Contrasting color for live route
      weight: 4
    }).addTo(map);
  } else {
    liveRoutePolyline.setLatLngs(liveRoutePoints);
  }

  // Store in Local Storage
  localStorage.setItem('liveRoutePoints', JSON.stringify(liveRoutePoints));
}

// Filter and display historical data
async function displayHistoricalData() {
  try {
    const startDate = startDateInput.value || "2020-01-01";
    const endDate = endDateInput.value || new Date().toISOString().split("T")[0];
    const filterWaco = filterWacoCheckbox.checked;

    const url = `/historical_data?startDate=${startDate}&endDate=${endDate}&filterWaco=${filterWaco}`;

    const response = await fetch(url);
    const data = await response.json();

    const totalDistance = calculateTotalDistance(data.features);
    document.getElementById('totalHistoricalDistance').textContent = `${totalDistance.toFixed(2)} miles`;

    if (historicalDataLayer) {
      map.removeLayer(historicalDataLayer);
    }

    historicalDataLayer = L.geoJSON(data, {
      style: {
        color: 'blue',
        weight: 2,
        opacity: 0.25
      },
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

// Add hover popup with route information and playback button
function addRoutePopup(feature, layer) {
  const timestamp = feature.properties.timestamp;
  const date = new Date(timestamp * 1000).toLocaleDateString();
  const time = new Date(timestamp * 1000).toLocaleTimeString();
  const distance = calculateTotalDistance([feature]);

  // Create the playback button element
  const playbackButton = document.createElement('button');
  playbackButton.textContent = 'Play Route';
  playbackButton.addEventListener('click', () => {
    playRoute(feature.geometry.coordinates);
  });

  // Create a container for the popup content
  const popupContent = document.createElement('div');
  popupContent.innerHTML = `Date: ${date}<br>Time: ${time}<br>Distance: ${distance.toFixed(2)} miles`;
  popupContent.appendChild(playbackButton); // Add the button to the popup

  layer.bindPopup(popupContent);
}

// Function to play back the route
function playRoute(coordinates) {
  // If an animation is already running, stop it
  if (playbackAnimation) {
    clearInterval(playbackAnimation);
    if (playbackPolyline) {
      map.removeLayer(playbackPolyline);
    }
  }

  // Initialize playbackPolyline 
  playbackPolyline = L.polyline([], { // Start with an empty array
    color: 'yellow', // Highlight color
    weight: 4
  }).addTo(map);

  let i = 0; // Start from the first point
  const playbackMarker = L.marker(L.latLng(coordinates[0][1], coordinates[0][0]), {
    icon: L.divIcon({
      className: 'blinking-marker',
      iconSize: [20, 20],
      html: '<div style="background-color: red; width: 100%; height: 100%; border-radius: 50%;"></div>'
    })
  }).addTo(map);

  playbackAnimation = setInterval(() => {
    if (i < coordinates.length) {
      const latLng = L.latLng(coordinates[i][1], coordinates[i][0]);
      playbackMarker.setLatLng(latLng);
      playbackPolyline.addLatLng(latLng);
      i++;
    } else {
      clearInterval(playbackAnimation);
      map.removeLayer(playbackMarker);
      i = 0; // Reset i to 0 after clearing the interval
    }
  }, 100); // Adjust the interval for playback speed
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

// Initialize
(async function init() {
  await loadWacoLimits();
  await displayHistoricalData();
  setInterval(updateLiveDataAndMetrics, 3000); // Update every 3 seconds

  // Load live route from Local Storage
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
  displayHistoricalData();
});

// Event listener for the "Check for new driving data" button
updateDataBtn.addEventListener('click', async () => {
  try {
    updateDataBtn.disabled = true; // Disable the button while updating
    updateDataBtn.textContent = "Updating..."; // Provide visual feedback

    const response = await fetch('/update_historical_data');
    const data = await response.json();

    if (response.ok) {
      console.log(data.message); // Log success message
      await displayHistoricalData(); // Refresh historical data on the map
    } else {
      console.error(data.error); // Log error message
    }
  } catch (error) {
    console.error('Error updating historical data:', error);
  } finally {
    updateDataBtn.disabled = false; // Re-enable the button
    updateDataBtn.textContent = "Check for new driving data"; // Reset text
  }
});

// Function to filter routes based on time period
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
  endDateInput.value = ''; // Clear end date
  applyDateFilter(); // Apply the filter
}