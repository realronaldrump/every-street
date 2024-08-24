self.importScripts('https://cdn.jsdelivr.net/npm/@turf/turf@6/turf.min.js');

self.addEventListener('message', function(e) {
    const { action, data } = e.data;

    if (action === 'filterFeatures') {
        const { features, bounds } = data;
        const filteredFeatures = features.filter(feature => {
            return turf.booleanIntersects(turf.bboxPolygon(bounds), feature);
        });
        self.postMessage({ action: 'filterFeaturesResult', data: filteredFeatures });
    }
});