# SPY 1주 시험 매수 — 화 12/1 01:00 KST (= 월 11/30 11:00 ET) 한 번만 실행
# 실행: .\scripts\register_core_test.ps1      삭제: Unregister-ScheduledTask QT_CoreTestBuy -Confirm:$false
$python  = "C:\Users\Quiruri\Anaconda3\envs\qt\python.exe"
$workdir = "C:\Users\Quiruri\Projects\QT"
$action   = New-ScheduledTaskAction -Execute $python -Argument "$workdir\scripts\core_test_buy.py --live" -WorkingDirectory $workdir
$trigger  = New-ScheduledTaskTrigger -Once -At ([datetime]"2026-12-01 01:00")
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -WakeToRun -RunOnlyIfNetworkAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "QT_CoreTestBuy" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "[OK] QT_CoreTestBuy registered: 2026-12-01 01:00 KST"
