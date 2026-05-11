"""AnalysisAgent 外部 API 工具 - 高德地图、天气、Unsplash"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings


@dataclass
class PlaceInfo:
    """地点信息"""
    name: str
    address: str
    location: str  # 经纬度
    phone: str = ""
    rating: float = 0.0


@dataclass
class WeatherInfo:
    """天气信息"""
    weather: str  # 晴/雨/多云
    temperature: str  # 温度
    wind: str  # 风向风力
    humidity: str  # 湿度


@dataclass
class DailyWeather:
    """逐日天气预报"""
    date: date
    day_weather: str = "待查询"
    night_weather: str = "待查询"
    day_temp: Optional[int] = None
    night_temp: Optional[int] = None
    wind_direction: str = ""
    wind_power: str = ""


@dataclass
class HotelInfo:
    """酒店信息"""
    name: str
    address: str
    price_range: str
    rating: float = 0.0
    distance: str = ""
    location: str = ""


class AmapTool:
    """
    高德地图 API 工具

    职责：
    1. 查询景点详细信息（地址、经纬度、开放时间）
    2. 查询天气信息
    3. 查询附近酒店
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.api_key = (
            getattr(self.settings, "amap_api_key", None)
            or os.getenv("AMAP_API_KEY")
        )
        self.base_url = "https://restapi.amap.com/v3"

    async def get_place_info(self, name: str, city: str) -> Optional[PlaceInfo]:
        """
        查询地点详细信息

        Args:
            name: 地点名称
            city: 所在城市

        Returns:
            PlaceInfo 或 None（如果 API 未配置或查询失败）
        """
        if not self.api_key:
            return None

        # 地理编码查询
        url = f"{self.base_url}/place/text"
        params = {
            "keywords": name,
            "city": city,
            "key": self.api_key,
            "output": "json",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            if data.get("status") == "1" and data.get("pois"):
                poi = data["pois"][0]
                return PlaceInfo(
                    name=poi.get("name", name),
                    address=poi.get("address", ""),
                    location=poi.get("location", ""),  # 经度，纬度
                    phone=poi.get("tel", ""),
                    rating=float(poi.get("biz_ext", {}).get("rating", 0)),
                )
        except Exception:
            pass

        return None

    async def get_weather(self, city: str) -> Optional[WeatherInfo]:
        """
        查询城市天气

        Args:
            city: 城市名称

        Returns:
            WeatherInfo 或 None
        """
        if not self.api_key:
            return None

        url = f"{self.base_url}/weather/weatherInfo"
        params = {
            "city": city,
            "key": self.api_key,
            "output": "json",
            "extensions": "base",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            if data.get("status") == "1" and data.get("lives"):
                live = data["lives"][0]
                return WeatherInfo(
                    weather=live.get("weather", "未知"),
                    temperature=live.get("temperature", "未知"),
                    wind=live.get("winddirection", "") + live.get("windpower", "") + "级",
                    humidity=live.get("humidity", "未知"),
                )
        except Exception:
            pass

        return None

    async def get_weather_forecast(
        self,
        city: str,
        start_date: Optional[date],
        days: int,
    ) -> List[DailyWeather]:
        """查询逐日天气预报；失败时返回逐日占位，保证下游结构完整。"""
        fallback_start = start_date or date.today()
        fallback = [
            DailyWeather(date=fallback_start + timedelta(days=index))
            for index in range(max(days, 0))
        ]
        if not self.api_key or days <= 0:
            return fallback

        url = f"{self.base_url}/weather/weatherInfo"
        params = {
            "city": city,
            "key": self.api_key,
            "output": "json",
            "extensions": "all",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            forecasts = data.get("forecasts") or []
            casts = forecasts[0].get("casts") if forecasts else []
            if not casts:
                return fallback

            result: List[DailyWeather] = []
            for index in range(days):
                target_date = fallback_start + timedelta(days=index)
                cast = casts[index] if index < len(casts) else {}
                result.append(
                    DailyWeather(
                        date=self._parse_date(cast.get("date")) or target_date,
                        day_weather=cast.get("dayweather") or "待查询",
                        night_weather=cast.get("nightweather") or "待查询",
                        day_temp=self._parse_int(cast.get("daytemp")),
                        night_temp=self._parse_int(cast.get("nighttemp")),
                        wind_direction=cast.get("daywind") or cast.get("nightwind") or "",
                        wind_power=cast.get("daypower") or cast.get("nightpower") or "",
                    )
                )
            return result
        except Exception:
            return fallback

    async def get_hotels_nearby(
        self,
        location: str,
        radius: int = 2000,
        limit: int = 5,
    ) -> List[HotelInfo]:
        """
        查询指定地点附近的酒店

        Args:
            location: 经纬度（格式："经度，纬度"）
            radius: 搜索半径（米）
            limit: 返回数量限制

        Returns:
            酒店列表
        """
        if not self.api_key:
            return []

        url = f"{self.base_url}/place/text"
        params = {
            "keywords": "酒店",
            "location": location,
            "radius": radius,
            "types": "100000|110000",  # 酒店类型
            "key": self.api_key,
            "output": "json",
            "offset": limit,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            hotels: List[HotelInfo] = []
            if data.get("status") == "1" and data.get("pois"):
                for poi in data["pois"][:limit]:
                    hotels.append(
                        HotelInfo(
                            name=poi.get("name", "未知酒店"),
                            address=poi.get("address", ""),
                            price_range=poi.get("biz_ext", {}).get("cost", "未知"),
                            rating=float(poi.get("biz_ext", {}).get("rating", 0)),
                            distance=poi.get("distance", "未知"),
                            location=poi.get("location", ""),
                        )
                    )

            return hotels

        except Exception:
            return []

    @staticmethod
    def _parse_int(value: Any) -> Optional[int]:
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_date(value: Any) -> Optional[date]:
        try:
            return date.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None


class UnsplashTool:
    """
    Unsplash 图片 API 工具

    职责：
    1. 根据关键词搜索景点图片
    2. 返回图片 URL 列表
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.access_key = (
            getattr(self.settings, "unsplash_access_key", None)
            or os.getenv("UNSPLASH_ACCESS_KEY")
        )
        self.base_url = "https://api.unsplash.com"

    async def search_images(
        self,
        query: str,
        limit: int = 4,
    ) -> List[str]:
        """
        搜索图片

        Args:
            query: 搜索关键词（如"故宫 北京"）
            limit: 返回数量限制

        Returns:
            图片 URL 列表
        """
        if not self.access_key:
            return []

        url = f"{self.base_url}/search/photos"
        params = {
            "query": query,
            "per_page": limit,
            "orientation": "landscape",
        }
        headers = {
            "Authorization": f"Client-ID {self.access_key}",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()

            urls = []
            if "results" in data:
                for result in data["results"][:limit]:
                    urls.append(result["urls"]["regular"])

            return urls

        except Exception:
            return []

    async def get_place_images(
        self,
        place_name: str,
        city: str,
        limit: int = 4,
    ) -> List[str]:
        """
        获取景点图片

        Args:
            place_name: 景点名称
            city: 城市名称
            limit: 返回数量限制

        Returns:
            图片 URL 列表
        """
        query = f"{place_name} {city}"
        return await self.search_images(query, limit)


class WeatherTool:
    """
    备用天气 API 工具

    使用 OpenWeatherMap 或其他免费天气 API
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.api_key = os.getenv("OPENWEATHER_API_KEY")
        self.base_url = "https://api.openweathermap.org/data/2.5"

    async def get_weather(self, city: str) -> Optional[WeatherInfo]:
        """查询天气"""
        if not self.api_key:
            return None

        url = f"{self.base_url}/weather"
        params = {
            "q": city,
            "appid": self.api_key,
            "units": "metric",
            "lang": "zh_cn",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

            weather_desc = data.get("weather", [{}])[0].get("description", "未知")
            temp = data.get("main", {}).get("temp", 0)

            return WeatherInfo(
                weather=weather_desc,
                temperature=f"{int(temp)}°C",
                wind="",
                humidity="",
            )

        except Exception:
            return None
