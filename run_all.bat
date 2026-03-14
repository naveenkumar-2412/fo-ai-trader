@echo off
echo Starting AI F^&O Trading System...

echo Waiting 2 seconds for services to start sequentially...

echo [1/9] Starting Market Data MCP (Port 8001)...
start cmd /k "cd /d %~dp0 && python mcp_market_data/api.py"
timeout /t 2 /nobreak >nul

echo [2/9] Starting News MCP (Port 8008)...
start cmd /k "cd /d %~dp0 && python mcp_news/api.py"
timeout /t 2 /nobreak >nul

echo [3/9] Starting Event Bus MCP (Port 8009)...
start cmd /k "cd /d %~dp0 && python mcp_event_bus/api.py"
timeout /t 2 /nobreak >nul

echo [4/9] Starting Feature Engine MCP (Port 8002)...
start cmd /k "cd /d %~dp0 && python mcp_features/api.py"
timeout /t 2 /nobreak >nul

echo [5/9] Starting Prediction MCP (Port 8003)...
start cmd /k "cd /d %~dp0 && python mcp_prediction/api.py"
timeout /t 2 /nobreak >nul

echo [6/9] Starting Strategy MCP (Port 8004)...
start cmd /k "cd /d %~dp0 && python mcp_strategy/api.py"
timeout /t 2 /nobreak >nul

echo [7/9] Starting Risk Manager MCP (Port 8005)...
start cmd /k "cd /d %~dp0 && python mcp_risk/api.py"
timeout /t 2 /nobreak >nul

echo [8/9] Starting Execution MCP (Port 8006)...
start cmd /k "cd /d %~dp0 && python mcp_execution/api.py"
timeout /t 2 /nobreak >nul

echo [9/9] Starting Dashboard API Gateway (Port 8007)...
start cmd /k "cd /d %~dp0 && python mcp_dashboard_api/api.py"
timeout /t 2 /nobreak >nul

echo.
echo [UI] Starting React Dashboard (Port 5173)...
start cmd /k "cd /d %~dp0\dashboard-ui && node node_modules/vite/bin/vite.js"
timeout /t 3 /nobreak >nul

echo.
echo =====================================================
echo  All services started successfully!
echo  React Dashboard  : http://localhost:5173
echo  API Gateway      : http://localhost:8007
echo  Market Data API  : http://localhost:8001/docs
echo  News API         : http://localhost:8008/docs
echo  Event Bus API    : http://localhost:8009/docs
echo  Feature Engine   : http://localhost:8002/docs
echo  Prediction API   : http://localhost:8003/docs
echo  Strategy API     : http://localhost:8004/docs
echo  Risk Manager     : http://localhost:8005/docs
echo  Execution API    : http://localhost:8006/docs
echo =====================================================
echo.
echo [ORCHESTRATOR] To start trading, open a new terminal and run:
echo    python main_orchestrator.py
echo [SIMULATION] Real-time prediction simulation (no real orders):
echo    python main_orchestrator.py --simulation --symbol NIFTY --interval-sec 15
echo.
pause
