@echo off
setlocal
echo =======================================================
echo   🚦 TrafficFlow AI — Full Training Pipeline
echo =======================================================
echo.
echo This script will train PPO, DQN, and A2C sequentially.
echo It will then run the benchmark to compare all models.
echo.

set TIMESTEPS=100000
if not "%~1"=="" set TIMESTEPS=%~1

echo [1/6] Training PPO model (%TIMESTEPS% timesteps)...
python -m ai.trainer --algorithm PPO --timesteps %TIMESTEPS%
if errorlevel 1 (
    echo ❌ PPO Training failed!
    exit /b 1
)

echo.
echo [2/6] Training DQN model (%TIMESTEPS% timesteps)...
python -m ai.trainer --algorithm DQN --timesteps %TIMESTEPS%
if errorlevel 1 (
    echo ❌ DQN Training failed!
    exit /b 1
)

echo.
echo [3/6] Training A2C model (%TIMESTEPS% timesteps)...
python -m ai.trainer --algorithm A2C --timesteps %TIMESTEPS%
if errorlevel 1 (
    echo ❌ A2C Training failed!
    exit /b 1
)

echo.
echo [4/6] Evaluating RULE_BASED model...
python -m ai.trainer --algorithm RULE_BASED --timesteps 5000
if errorlevel 1 (
    echo ❌ RULE_BASED Evaluation failed!
    exit /b 1
)

echo.
echo [5/6] Evaluating FIXED_TIME model...
python -m ai.trainer --algorithm FIXED_TIME --timesteps 5000
if errorlevel 1 (
    echo ❌ FIXED_TIME Evaluation failed!
    exit /b 1
)

echo.
echo [6/6] Running Benchmark for all models...
python -m ai.benchmark

echo.
echo =======================================================
echo   ✅ Full Training Pipeline Completed Successfully!
echo =======================================================
pause
