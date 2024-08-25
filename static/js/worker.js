self.addEventListener('message', function (e) {
    const { action, data } = e.data;

    if (action === 'filterFeatures') {
        const { features, bounds } = data;

        // Filter features based on bounding box
        const filteredFeatures = features.filter(feature => {
            const coordinates = feature.geometry.coordinates;
            const [minLon, minLat, maxLon, maxLat] = bounds;

            if (feature.geometry.type === 'LineString') {
                return coordinates.some(coord => {
                    const [lon, lat] = coord;
                    return lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat;
                });
            } else if (feature.geometry.type === 'MultiLineString') {
                return coordinates.some(segment =>
                    segment.some(coord => {
                        const [lon, lat] = coord;
                        return lon >= minLon && lon <= maxLon && lat >= minLat && lat <= maxLat;
                    })
                );
            }

            return false; // If the geometry type is not supported
        });

        self.postMessage({ action: 'filterFeaturesResult', data: filteredFeatures });
    }
});