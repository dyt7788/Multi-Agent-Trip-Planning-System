"""Runtime tool bridge for generated Python scripts."""

from __future__ import annotations

import os
import time
from typing import Any, Dict

import httpx


class ToolRuntimeUtils:
    """Expose a stable call_function interface for generated code."""

    def __init__(self) -> None:
        self.base_url = "https://restapi.amap.com"

    def call_function(self, funcname: str, **kwargs: Any) -> Dict[str, Any]:
        handlers = {
            "trustoken_map_maps_geo": self._maps_geo,
            "trustoken_map_maps_around_search": self._maps_around_search,
            "trustoken_map_maps_weather": self._maps_weather,
        }

        if funcname not in handlers:
            raise ValueError(f"Unsupported function: {funcname}")

        result = handlers[funcname](**kwargs)
        return {
            "timestamp": time.time(),
            "parent_task_id": None,
            "subtask_id": None,
            "funcname": funcname,
            "kwargs": kwargs,
            "result": result,
        }

    def _api_key(self) -> str:
        key = os.getenv("AMAP_API_KEY") or os.getenv("AMAP_MAPS_API_KEY")
        if not key:
            raise ValueError("AMAP_API_KEY is not configured")
        return key

    def _get_json(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(params)
        params["key"] = self._api_key()

        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{self.base_url}{path}", params=params)
            response.raise_for_status()
            data = response.json()

        status = str(data.get("status", ""))
        if status != "1":
            info = data.get("info") or "AMap API returned failure"
            raise RuntimeError(info)

        return {"meta": None, "content": data}

    def _maps_geo(self, address: str, city: str = "") -> Dict[str, Any]:
        return self._get_json(
            "/v3/geocode/geo",
            {
                "address": address,
                "city": city,
            },
        )

    def _maps_around_search(
        self,
        location: str,
        keywords: str,
        radius: str = "3000",
        sortrule: str = "distance",
        offset: str = "20",
        page: str = "1",
    ) -> Dict[str, Any]:
        return self._get_json(
            "/v3/place/around",
            {
                "location": location,
                "keywords": keywords,
                "radius": radius,
                "sortrule": sortrule,
                "offset": offset,
                "page": page,
                "extensions": "all",
            },
        )

    def _maps_weather(self, city: str, extensions: str = "all") -> Dict[str, Any]:
        return self._get_json(
            "/v3/weather/weatherInfo",
            {
                "city": city,
                "extensions": extensions,
            },
        )


utils = ToolRuntimeUtils()
