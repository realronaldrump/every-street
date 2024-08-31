import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from bouncie_api import BouncieAPI

load_dotenv()  # This will load environment variables from a .env file

async def test_bouncie_api():
    # Initialize the BouncieAPI
    bouncie_api = BouncieAPI()

    # Set the date range for historical data
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=30)  # Fetch last 30 days of data

    print(f"Fetching historical data from {start_date} to {end_date}")

    try:
        # Fetch historical data
        historical_data = await bouncie_api.fetch_historical_data(start_date, end_date)

        if historical_data:
            print(f"Successfully fetched {len(historical_data)} days of data")
            # Print the first day's data as an example
            print("Example data for the first day:")
            print(historical_data[0])
        else:
            print("No historical data returned")

    except Exception as e:
        print(f"An error occurred: {str(e)}")

    # Print the access token for debugging
    print(f"Access Token: {bouncie_api.client.access_token}")

if __name__ == "__main__":
    asyncio.run(test_bouncie_api())