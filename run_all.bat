@echo off
echo ============================================================
echo   AI F^&O Trading System — Starting All Services
echo ============================================================

cd /d %~dp0

echo [1/11] Starting Market Data MCP (port 8001)...
start "Market Data" cmd /k "cd mcp_market_data && python api.py"
timeout /t 2 /nobreak >nul

echo [2/11] Starting Feature Engine MCP (port 8002)...
start "Feature Engine" cmd /k "cd mcp_features && python api.py"
timeout /t 2 /nobreak >nul

echo [3/11] Starting Prediction MCP (port 8003)...
start "Prediction" cmd /k "cd mcp_prediction && python api.py"
timeout /t 2 /nobreak >nul

echo [4/11] Starting Strategy MCP (port 8004)...
start "Strategy" cmd /k "cd mcp_strategy && python api.py"
timeout /t 2 /nobreak >nul

echo [5/11] Starting Risk Manager MCP (port 8005)...
start "Risk Manager" cmd /k "cd mcp_risk && python api.py"
timeout /t 2 /nobreak >nul

echo [6/11] Starting Execution MCP (port 8006)...
start "Execution" cmd /k "cd mcp_execution && python api.py"
timeout /t 2 /nobreak >nul

echo [7/11] Starting Dashboard API Gateway (port 8007)...
start "Dashboard API" cmd /k "cd mcp_dashboard_api && python api.py"
timeout /t 2 /nobreak >nul

echo [8/11] Starting News MCP (port 8008)...
start "News MCP" cmd /k "cd mcp_news && python api.py"
timeout /t 2 /nobreak >nul

echo [9/11] Starting Event Bus MCP (port 8009)...
start "Event Bus" cmd /k "cd mcp_event_bus && python api.py"
timeout /t 2 /nobreak >nul

echo [10/11] Starting Notifications MCP (port 8010)...
start "Notifications" cmd /k "cd mcp_notifications && python api.py"
timeout /t 2 /nobreak >nul

echo [11/11] Starting React Dashboard (port 5173)...
start "Dashboard UI" cmd /k "cd dashboard-ui && npm run dev"
timeout /t 4 /nobreak >nul

echo.
echo ============================================================
echo   All Services Running
echo ============================================================
echo   Market Data     : http://localhost:8001/docs
echo   Feature Engine  : http://localhost:8002/docs
echo   Prediction      : http://localhost:8003/docs
echo   Strategy        : http://localhost:8004/docs
echo   Risk Manager    : http://localhost:8005/docs
echo   Execution       : http://localhost:8006/docs
echo   Dashboard API   : http://localhost:8007/docs
echo   News MCP        : http://localhost:8008/docs
echo   Event Bus       : http://localhost:8009/docs
echo   Notifications   : http://localhost:8010/docs
echo   React Dashboard : http://localhost:5173
echo ============================================================
echo.
echo   To start trading:
echo     python main_orchestrator.py           (NIFTY)
echo     python main_orchestrator.py --symbol BANKNIFTY
echo     python main_orchestrator.py --dry-run (paper trading)
echo.
echo   To retrain model after market close:
echo     training\retrain_daily.bat
echo ============================================================
pause
