"""ImageAgent: searches Unsplash for destination/spot images."""

from __future__ import annotations

from typing import Optional

from app.config import Settings, get_settings
from app.models.schemas import (
    AgentTrace,
    ImageAgentOutput,
    ImageObservation,
    ImageAnalysisRequest,
    TripPlanRequest,
)
from AnalysisAgent.tools.external_api import UnsplashTool
from TravelCore.text import utc_now


class ImageAgent:
    """
    Image Agent - searches Unsplash for destination and scenic spot images.
    """

    name = "ImageAgent"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.unsplash_tool = UnsplashTool(self.settings)

    async def run(self, request: TripPlanRequest | ImageAnalysisRequest) -> ImageAgentOutput:
        """
        Search for destination-related images.

        Flow:
        1. Search Unsplash for destination landscape images
        2. Search for specific spot images from preferences/scenic types
        3. Return image observations for downstream agents
        """
        started = utc_now()

        destination = getattr(request, "destination", None)
        image_urls = getattr(request, "image_urls", [])
        spots = getattr(request, "preferences", []) or []

        observations: list[ImageObservation] = []

        if destination and self.unsplash_tool.access_key:
            # General destination images
            dest_images = await self.unsplash_tool.search_images(
                f"{destination} travel landscape", limit=6
            )
            for url in dest_images:
                observations.append(
                    ImageObservation(
                        image_url=url,
                        labels=["scenery", "travel"],
                        scene_type="attraction",
                        inferred_location=destination,
                        description=f"{destination} landscape",
                        confidence=0.7,
                    )
                )

            # Specific spot images
            for spot in spots[:5]:
                if isinstance(spot, str) and spot:
                    spot_images = await self.unsplash_tool.search_images(
                        f"{spot} {destination}", limit=2
                    )
                    for url in spot_images:
                        observations.append(
                            ImageObservation(
                                image_url=url,
                                labels=["attraction"],
                                scene_type="attraction",
                                inferred_location=spot,
                                description=f"{spot} image",
                                confidence=0.6,
                            )
                        )

        # Include user-provided image URLs
        for url in image_urls:
            observations.append(
                ImageObservation(
                    image_url=url,
                    labels=["user-provided"],
                    scene_type="unknown",
                    description="User-provided image",
                    confidence=0.5,
                )
            )

        return ImageAgentOutput(
            destination=destination,
            crawled_image_urls=[obs.image_url for obs in observations],
            observations=observations,
            trace=[
                AgentTrace(
                    agent=self.name,
                    status="completed",
                    message=f"Found {len(observations)} images for {destination or 'destination'}.",
                    started_at=started,
                    finished_at=utc_now(),
                    metadata={
                        "image_count": len(observations),
                        "unsplash_configured": bool(self.unsplash_tool.access_key),
                    },
                )
            ],
        )
