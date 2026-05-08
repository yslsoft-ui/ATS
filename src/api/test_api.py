import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app)

def run_api_test():
    print("=== API 상태 조회 (GET /api/status) ===")
    response = client.get("/api/status")
    print(f"응답 코드: {response.status_code}")
    print(f"응답 데이터: {response.json()}\n")

    print("=== 백테스트 실행 (POST /api/backtest/run) ===")
    payload = {
        "symbol": "KRW-BTC",
        "start_date": "2023-01-01",
        "end_date": "2023-12-31",
        "initial_cash": 10000000.0
    }
    response = client.post("/api/backtest/run", json=payload)
    print(f"응답 코드: {response.status_code}")
    print(f"응답 데이터: {response.json()}")

if __name__ == "__main__":
    run_api_test()
