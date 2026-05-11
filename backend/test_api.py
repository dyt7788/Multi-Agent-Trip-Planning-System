"""FastAPI endpoint integration tests using httpx TestClient."""

import sys
from pathlib import Path

backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient

from app.api.main import app
from app.config import get_settings
from app.models.schemas import TripPlanRequest

client = TestClient(app)


def test_health():
    """Test the health endpoint."""
    print("[GET /health]")
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "TotalAgent" in data["agents"]
    print(f"  OK: version={data['version']}, agents={data['agents']}")


def test_preferences():
    """Test preference CRUD endpoints."""
    import uuid
    user_id = f"test_api_user_{uuid.uuid4().hex[:6]}"

    # Get preferences (new user)
    print(f"[GET /api/v1/users/{{user_id}}/preferences]")
    response = client.get(f"/api/v1/users/{user_id}/preferences")
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == user_id
    print(f"  OK: new user preferences={data['preferences']}")

    # Update preferences
    print(f"[PUT /api/v1/users/{{user_id}}/preferences]")
    response = client.put(
        f"/api/v1/users/{user_id}/preferences",
        json={"preferences": ["历史", "自然"], "budget_level": "中等", "pace": "轻松"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "历史" in data["preferences"]
    print(f"  OK: updated preferences={data['preferences']}")

    # Read back
    response = client.get(f"/api/v1/users/{user_id}/preferences")
    assert response.status_code == 200
    data = response.json()
    assert data["preferences"] == ["历史", "自然"]
    print(f"  OK: read back preferences={data['preferences']}")


def test_plan_trip():
    """Test the main trip planning endpoint."""
    import uuid
    print("[POST /api/v1/trips/plan]")

    request_data = {
        "destination": "杭州",
        "days": 2,
        "preferences": {"景点类型": ["历史", "自然"], "旅行风格": "轻松"},
        "mode": "初次规划",
        "travel_style": "轻松",
        "user_id": f"test_api_user_{uuid.uuid4().hex[:6]}",
    }

    response = client.post("/api/v1/trips/plan", json=request_data, timeout=300)
    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["plan"]["destination"] == "杭州"
    assert data["plan"]["days"] == 2
    assert len(data["plan"]["itinerary"]) == 2
    assert len(data["trace"]) >= 5  # At least 5 agent traces

    agents_traced = [t["agent"] for t in data["trace"]]
    assert "TotalAgent" in agents_traced
    assert "StrategyAgent" in agents_traced
    assert "QueryAgent" in agents_traced
    assert "AnalysisAgent" in agents_traced
    assert "ReportAgent" in agents_traced

    print(f"  OK: success={data['success']}")
    print(f"  OK: trip_id={data['plan']['trip_id']}")
    print(f"  OK: itinerary days={len(data['plan']['itinerary'])}")
    print(f"  OK: agents traced={agents_traced}")

    return data["plan"]["trip_id"]


def test_get_html_report(trip_id: str):
    """Test retrieving the generated HTML report."""
    print(f"[GET /api/v1/reports/{{trip_id}}.html]")
    response = client.get(f"/api/v1/reports/{trip_id}.html")
    assert response.status_code == 200
    assert "<!doctype html>" in response.text or "<!DOCTYPE html>" in response.text
    print(f"  OK: HTML report length={len(response.text)} chars")


def test_report_not_found():
    """Test 404 for non-existent report."""
    print("[GET /api/v1/reports/nonexistent.html]")
    response = client.get("/api/v1/reports/nonexistent.html")
    assert response.status_code == 404
    print(f"  OK: 404 for non-existent report")


def test_research_endpoint():
    """Test the standalone research endpoint."""
    print("[POST /api/v1/query/research]")

    response = client.post(
        "/api/v1/query/research",
        json={"destination": "北京", "keywords": ["故宫", "长城"], "limit": 3},
        timeout=120,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["destination"] == "北京"
    print(f"  OK: research spots={len(data.get('spots_summary', []))}")


def main():
    print("=" * 60)
    print("FastAPI Endpoint Integration Tests")
    print("=" * 60)

    results = {}

    try:
        test_health()
        results["health"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["health"] = False

    try:
        test_preferences()
        results["preferences"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["preferences"] = False

    try:
        trip_id = test_plan_trip()
        results["plan_trip"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["plan_trip"] = False
        trip_id = None

    if trip_id:
        try:
            test_get_html_report(trip_id)
            results["get_html_report"] = True
        except Exception as e:
            print(f"  FAILED: {e}")
            results["get_html_report"] = False

    try:
        test_report_not_found()
        results["report_404"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["report_404"] = False

    try:
        test_research_endpoint()
        results["research"] = True
    except Exception as e:
        print(f"  FAILED: {e}")
        results["research"] = False

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    for name, passed in results.items():
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")

    all_passed = all(results.values())
    print("\n" + ("All tests PASSED!" if all_passed else "Some tests FAILED!"))
    print("=" * 60)
    return all_passed


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
