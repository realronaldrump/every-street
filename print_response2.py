import asyncio
from datetime import datetime, timedelta
from bouncie_api import BouncieAPI, DEVICE_IMEI, VEHICLE_ID
import httpx

async def main():
    bouncie_api = BouncieAPI()
    await bouncie_api.client.get_access_token()

    # Set the date range for fetching historical data
    end_date = datetime.now()
    start_date = end_date - timedelta(days=7)  # Fetch last 7 days of data

    async with httpx.AsyncClient() as client:
        url = f"https://www.bouncie.app/api/vehicles/{VEHICLE_ID}/triplegs/details/summary"
        params = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "bands": "true",
            "defaultColor": "%2355AEE9",
            "overspeedColor": "%23CC0000"
        }
        headers = {"Authorization": bouncie_api.client.access_token}
        
        response = await client.get(url, params=params, headers=headers)
        historical_data = response.json()
        
        print("Sample of historical data structure:")
        print(historical_data[:2])  # Print first two trips for brevity

if __name__ == "__main__":
    asyncio.run(main())