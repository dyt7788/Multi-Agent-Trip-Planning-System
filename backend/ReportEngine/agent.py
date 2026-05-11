"""ReportAgent: creates HTML and optional PDF artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from app.config import Settings, get_settings
from app.models.schemas import AgentTrace, AnalysisAgentOutput, ItineraryPlan, ReportAgentOutput, ReportArtifact
from ReportEngine.tools.html_renderer import HtmlReportRenderer
from ReportEngine.tools.pdf_exporter import PdfExporter
from TravelCore.text import utc_now


class ReportAgent:
    name = "ReportAgent"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.renderer = HtmlReportRenderer()
        self.pdf = PdfExporter()

    async def run(
        self,
        plan: ItineraryPlan,
        export_pdf: bool = False,
        analysis: Optional[AnalysisAgentOutput] = None,
    ) -> tuple[ReportAgentOutput, list[AgentTrace]]:
        started = utc_now()
        html = self.renderer.render(plan)
        html_path = Path(self.settings.report_dir) / f"{plan.trip_id}.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, encoding="utf-8")

        structured = self._build_structured_report(plan)
        json_path = Path(self.settings.report_dir) / f"{plan.trip_id}.json"
        json_path.write_text(
            json.dumps(structured, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        generated_at = utc_now()
        artifacts = [
            ReportArtifact(
                type="html",
                path=str(html_path),
                url=f"/api/v1/reports/{plan.trip_id}.html",
                generated_at=generated_at,
            ),
            ReportArtifact(
                type="json",
                path=str(json_path),
                url=f"/api/v1/reports/{plan.trip_id}.json",
                generated_at=generated_at,
            )
        ]

        pdf_status = "disabled"
        if export_pdf or self.settings.enable_pdf_export:
            pdf_path = Path(self.settings.report_dir) / f"{plan.trip_id}.pdf"
            exported = self.pdf.export(html, pdf_path)
            if exported:
                artifacts.append(
                    ReportArtifact(
                        type="pdf",
                        path=str(exported),
                        url=f"/api/v1/reports/{plan.trip_id}.pdf",
                        generated_at=utc_now(),
                    )
                )
                pdf_status = "generated"
            else:
                pdf_status = "unavailable"

        modifiable_spots = [
            spot.name
            for spot in (analysis.spots if analysis else [])
            if spot.status in ("推荐", "用户确认")
        ]
        output = ReportAgentOutput(
            html_report=html,
            structured_report=structured,
            modifiable_spots=modifiable_spots,
            artifacts=artifacts,
        )
        return (
            output,
            [
                AgentTrace(
                    agent=self.name,
                    status="completed",
                    message="Generated report artifacts.",
                    started_at=started,
                    finished_at=utc_now(),
                    metadata={
                        "pdf": pdf_status,
                        "modifiable_spots": modifiable_spots,
                        "json_report": "generated",
                    },
                )
            ],
        )

    def _build_structured_report(self, plan: ItineraryPlan) -> dict:
        """Build report JSON that matches the planner-style schema."""
        detail = plan.detailed_plan
        if detail:
            return detail.model_dump(mode="json")

        return {
            "city": plan.destination,
            "start_date": None,
            "end_date": None,
            "days": [],
            "weather_info": [],
            "overall_suggestions": "详细天气和酒店信息暂不可用。",
            "budget": {
                "total_attractions": 0,
                "total_hotels": 0,
                "total_meals": 0,
                "total_transportation": 0,
                "total": 0,
            },
        }
