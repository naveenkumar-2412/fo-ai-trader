@echo off
REM Daily model retrain script — run after market close (16:00 IST)
REM Reads real trade_log.jsonl and appends to synthetic training data
cd /d %~dp0
python training\train_lgbm.py --retrain
echo Retraining complete. Model saved to models\lgbm_model.pkl
pause
