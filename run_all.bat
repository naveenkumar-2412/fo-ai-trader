@echo off
echo Starting AI F^&O Trading System...

echo Waiting 2 seconds for services to start sequentially...

echo [1/7] Starting Market Data MCP (Port 8001)...
start cmd /k "cd /d %~dp0 && python mcp_market_data/api.py"
timeout /t 2 /nobreak >nul

echo [2/7] Starting Feature Engine MCP (Port 8002)...
start cmd /k "cd /d %~dp0 && python mcp_features/api.py"
timeout /t 2 /nobreak >nul

echo [3/7] Starting Prediction MCP (Port 8003)...
start cmd /k "cd /d %~dp0 && python mcp_prediction/api.py"
timeout /t 2 /nobreak >nul

echo [4/7] Starting Strategy MCP (Port 8004)...
start cmd /k "cd /d %~dp0 && python mcp_strategy/api.py"
timeout /t 2 /nobreak >nul

echo [5/7] Starting Risk Manager MCP (Port 8005)...
start cmd /k "cd /d %~dp0 && python mcp_risk/api.py"
timeout /t 2 /nobreak >nul

echo [6/7] Starting Execution MCP (Port 8006)...
start cmd /k "cd /d %~dp0 && python mcp_execution/api.py"
timeout /t 2 /nobreak >nul

echo [7/7] Starting Dashboard API Gateway (Port 8007)...
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
echo  Feature Engine   : http://localhost:8002/docs
echo  Prediction API   : http://localhost:8003/docs
echo  Strategy API     : http://localhost:8004/docs
echo  Risk Manager     : http://localhost:8005/docs
echo  Execution API    : http://localhost:8006/docs
echo =====================================================
echo.
echo [ORCHESTRATOR] To start trading, open a new terminal and run:
echo    python main_orchestrator.py
echo.
pause
