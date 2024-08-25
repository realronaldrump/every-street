self.addEventListener('message', function(e) {
    const { action, data } = e.data;

    if (action === 'filterFeatures') {
        const { features, bounds } = data;
        const filteredFeatures = features.filter(feature => {
            // Implement a simple bounding box check
            const coordinates = feature.geometry.coordinates;
            const [minLon, minLat, maxLon, maxLat] = bounds;
            return coordinates.some(coord => {
                const [lon, lat] = coord;
                return lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat;
            });
        });
        self.postMessage({ action: 'filterFeaturesResult', data: filteredFeatures });
    }
});