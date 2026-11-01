# 一条命令跑通常用入口（参数化，不依赖 shell 脚本）
.PHONY: help install test test-engine test-backend test-frontend lint data audit golden build up down

help:
	@echo "make install       安装前后端依赖"
	@echo "make test          引擎 + 后端 pytest + 前端单测"
	@echo "make lint          ruff + tsc"
	@echo "make data          生成看盘静态数据（apps/platform-web/public/data）"
	@echo "make audit         因果审计（BTC 1D 采样）"
	@echo "make golden        重新生成缠论/指标层 golden 快照"
	@echo "make build         前端生产构建"
	@echo "make up / down     docker compose 启停"

install:
	python3 -m pip install -r apps/platform-api/requirements-dev.txt
	cd apps/platform-web && npm ci

test: test-engine test-backend test-frontend test-chanlun-web

test-engine:
	python3 -m pytest packages/chanlun-core/tests -q

test-backend:
	cd apps/platform-api && python3 -m pytest -q

test-frontend:
	cd apps/platform-web && npm test

test-chanlun-web:
	node --test apps/chanlun-web/tests/*.test.mjs

lint:
	ruff check apps packages scripts experiments labs
	cd apps/platform-web && npx tsc --noEmit

data:
	python3 scripts/build_web_data.py

audit:
	python3 scripts/audit_causality.py --symbol BTC-USD --tf 1day --samples 5

golden:
	python3 scripts/export_chanlun_golden.py --bars 400
	python3 scripts/export_indicator_golden.py --bars 600

build:
	cd apps/platform-web && npm run build

up:
	docker compose up -d --build

down:
	docker compose down

# 浏览器端到端验证（需先起后端 8000 / 前端 3100 / headless Chrome 9222，见 docs/runbooks/BROWSER_VERIFICATION.md）
verify-browser:
	node scripts/verify_browser.mjs --base $${BASE:-http://127.0.0.1:3100} --shots reports/browser-verify
