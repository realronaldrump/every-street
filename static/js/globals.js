let map, drawnItems, liveRoutePolyline, liveMarker, liveRouteDataLayer, historicalDataLayer, playbackPolyline, playbackMarker, playbackAnimation;
let isPlaying = false;
let currentCoordIndex = 0;
let playbackSpeed = 1.0;

const filterWacoCheckbox = document.getElementById('filterWaco');
const wacoBoundarySelect = document.getElementById('wacoBoundary');
const startDateInput = document.getElementById('startDate');
const endDateInput = document.getElementById('endDate');
const applyFilterBtn = document.getElementById('applyFilter');
const updateDataBtn = document.getElementById('updateData');
const playPauseBtn = document.getElementById('playPause');
const playbackSpeedInput = document.getElementById('playbackSpeed');
const speedValueSpan = document.getElementById('speedValue');
