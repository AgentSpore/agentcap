.PHONY: install lint check smoke test run

install:
	pip install -r requirements.txt
	pip install ruff pytest pytest-asyncio httpx

lint:
	ruff check . --select E,W,F,I --ignore E501

check:
	python -c "from models import *; print('Models OK')"
	python -c "from main import app; print(f'App: {app.title} v{app.version}')"

smoke:
	python scripts/smoke_test.py

test: lint check smoke

run:
	uvicorn main:app --reload --port 8000
