from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from flops.tools.tool import ToolContext, Tool, ToolResult, tool


class WeatherParams(BaseModel):
    city: str = Field(description="City name to query weather for.")


@dataclass
class WeatherResult:
    city: str
    temperature: float
    windspeed: float
    weathercode: int


GEO_API = "https://geocoding-api.open-meteo.com/v1/search"
WEATHER_API = "https://api.open-meteo.com/v1/forecast"


async def get_weather(city: str) -> WeatherResult:
    # 1️⃣ City -> coordinates
    async with httpx.AsyncClient() as client:
        geo_resp = await client.get(GEO_API, params={"name": city, "count": 1})
        geo_data = geo_resp.json()

        if "results" not in geo_data:
            raise ValueError(f"City not found: {city}")

        location = geo_data["results"][0]
        lat = location["latitude"]
        lon = location["longitude"]

        # 2️⃣ Query weather
        weather_resp = await client.get(
            WEATHER_API,
            params={
                "latitude": lat,
                "longitude": lon,
                "current_weather": True,
            },
        )

        weather_data = weather_resp.json()["current_weather"]

    return WeatherResult(
        city=city,
        temperature=weather_data["temperature"],
        windspeed=weather_data["windspeed"],
        weathercode=weather_data["weathercode"],
    )


@tool
class WeatherTool(Tool):
    """Query the current weather for a specified city."""

    params_model = WeatherParams

    def render(self, tool_input: dict) -> str:
        return f"🌤️ Weather({tool_input.get('city', '<no city>')})"

    async def execute(self, ctx: ToolContext, params: WeatherParams) -> ToolResult:
        try:
            result = await get_weather(params.city)
            return ToolResult(
                content=f"{result.city} current temperature {result.temperature}°C, wind speed {result.windspeed} km/h, weather code {result.weathercode}"
            )
        except Exception as e:
            return ToolResult(content=str(e), is_error=True)
