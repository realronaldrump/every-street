document.addEventListener('DOMContentLoaded', function() {
    const feedbackContainer = document.getElementById('feedback-container');
    const dbStats = document.getElementById('db-stats');

    function showFeedback(message, type = 'info') {
        const feedbackElement = document.createElement('div');
        feedbackElement.className = `feedback ${type}`;
        feedbackElement.textContent = message;
        feedbackContainer.appendChild(feedbackElement);
        setTimeout(() => feedbackContainer.removeChild(feedbackElement), 5000);
    }

    async function uploadFile(url, formData) {
        try {
            const response = await fetch(url, {
                method: 'POST',
                body: formData
            });
            const data = await response.json();
            if (response.ok) {
                showFeedback(data.message, 'success');
            } else {
                showFeedback(data.error, 'error');
            }
        } catch (error) {
            showFeedback('An error occurred while uploading the file.', 'error');
        }
    }

    document.getElementById('add-boundary-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const formData = new FormData();
        formData.append('name', document.getElementById('boundary-name').value);
        formData.append('file', document.getElementById('boundary-file').files[0]);
        await uploadFile('/add_waco_boundary', formData);
    });

    document.getElementById('add-streets-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const formData = new FormData();
        formData.append('file', document.getElementById('streets-file').files[0]);
        await uploadFile('/add_waco_streets', formData);
    });

    document.getElementById('add-historical-data-form').addEventListener('submit', async function(e) {
        e.preventDefault();
        const formData = new FormData();
        formData.append('file', document.getElementById('historical-data-file').files[0]);
        await uploadFile('/add_historical_data', formData);
    });

    async function updateDBStats() {
        try {
            const response = await fetch('/db_stats');
            const stats = await response.json();
            dbStats.innerHTML = `
                <p>Waco Boundaries: ${stats.waco_boundaries}</p>
                <p>Waco Streets: ${stats.waco_streets}</p>
                <p>Historical Data Points: ${stats.historical_data}</p>
                <p>Live Route Points: ${stats.live_route}</p>
            `;
        } catch (error) {
            dbStats.innerHTML = '<p>Failed to load database statistics.</p>';
        }
    }

    updateDBStats();
    setInterval(updateDBStats, 60000); // Update stats every minute
});