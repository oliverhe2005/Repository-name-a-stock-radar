import logging

from pipeline import run_once
from report import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.FileHandler("watchlist.log", encoding="utf-8"), logging.StreamHandler()],
)

if __name__ == "__main__":
    result = run_once()
    report = generate_report()
    print(result)
    print(f"report={report}")
