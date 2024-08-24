import asyncio
from bouncie_api import BouncieAPI, DEVICE_IMEI
import httpx

async def main():
    bouncie_api = BouncieAPI()
    await bouncie_api.client.get_access_token()
    async with httpx.AsyncClient() as client:
        vehicle_data = await client.get(f"https://www.bouncie.app/api/vehicles?imei={DEVICE_IMEI}", headers={"Authorization": bouncie_api.client.access_token})
        vehicle_data = vehicle_data.json()
    print(vehicle_data)

if __name__ == "__main__":
    asyncio.run(main())