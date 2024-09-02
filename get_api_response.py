import asyncio
import json
from datetime import datetime, timezone

import aiohttp
from bounciepy import AsyncRESTAPIClient

# Your Bouncie Developer App Credentials
CLIENT_ID = "python-test"
CLIENT_SECRET = "v023rK8ZLVSh7pp0dhkrRu9rqYonaCbRDLSQ1Hh9JG5VR6REVr"
REDIRECT_URI = "http://localhost:8080/callback"
AUTH_CODE = "UfHLWwJJqrJkLyA2uy2a7fJvAsTUOOmkAq2H5Tfkuwc1ZMxsO2"

# Your Device ID
VEHICLE_ID = "5f31babdad03810038e10c32"


def clean_example(value, max_length=50):
    """Truncate and format example data for brevity."""
    if isinstance(value, list):
        return f"List of {len(value)} items"
    elif isinstance(value, dict):
        return "Nested Dictionary"
    elif isinstance(value, str):
        return value if len(value) <= max_length else value[:max_length] + "..."
    return str(value)


def summarize_data(data, path=""):
    """Recursively summarize the structure and types of the data."""
    summary = []
    if isinstance(data, dict):
        for key, value in data.items():
            new_path = f"{path}.{key}" if path else key
            summary.append({
                "path": new_path,
                "type": type(value).__name__,
                "example": clean_example(value)
            })
            summary.extend(summarize_data(value, new_path))
    elif isinstance(data, list):
        if data:
            summary.append({
                "path": path,
                "type": f"List[{type(data[0]).__name__}]",
                "example": clean_example(data)
            })
            summary.extend(summarize_data(data[0], f"{path}[0]"))
    return summary


async def fetch_summary_data(session, vehicle_id, date, headers):
    """Fetches trip summary data from Bouncie API."""
    start_time = f"{date}T00:00:00-05:00"
    end_time = f"{date}T23:59:59-05:00"
    summary_url = f"https://www.bouncie.app/api/vehicles/{vehicle_id}/triplegs/details/summary?bands=true&defaultColor=%2355AEE9&overspeedColor=%23CC0000&startDate={start_time}&endDate={end_time}"

    async with session.get(summary_url, headers=headers) as response:
        if response.status == 200:
            return await response.json()
        else:
            print(f"Error: Failed to fetch data for {date}. HTTP Status code: {response.status}. Response content: {await response.text()}")
            return None


async def explore_api_data(session, vehicle_id, headers, specific_date=None):
    """Explores the Bouncie API to understand available data."""

    # Use specific date if provided, otherwise use today
    if specific_date:
        date_to_use = specific_date.strftime("%Y-%m-%d")
    else:
        date_to_use = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    trips_data = await fetch_summary_data(session, vehicle_id, date_to_use, headers)

    if trips_data:
        # Summarize the structure and data types of the response
        data_summary = summarize_data(trips_data)

        return {
            "endpoint": "/vehicles/{vehicle_id}/triplegs/details/summary",
            "description": "Provides detailed trip summaries for a given vehicle and date range.",
            "summary": data_summary
        }
    else:
        return None


async def main():
    client = AsyncRESTAPIClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_url=REDIRECT_URI,
        auth_code=AUTH_CODE,
    )

    async with aiohttp.ClientSession() as session:
        try:
            success = await client.get_access_token()
            if not success:
                print("Failed to obtain access token.")
                return

            headers = {
                "Accept": "application/json",
                "Authorization": client.access_token,
                "Content-Type": "application/json"
            }

            # Specify the custom date here
            custom_date = datetime(2024, 7, 21)  # July 21, 2024

            api_data_summary = await explore_api_data(session, VEHICLE_ID, headers, specific_date=custom_date)

            if api_data_summary:
                with open("bouncie_api_data_summary.txt", "w") as f:
                    f.write(json.dumps(api_data_summary, indent=4))

                print("Bouncie API Data Summary saved to bouncie_api_data_summary.txt")
            else:
                print("Failed to retrieve data for API exploration.")

        except Exception as e:
            print(f"An error occurred: {e}")

        finally:
            await client.client_session.close()

if __name__ == "__main__":
    asyncio.run(main())
