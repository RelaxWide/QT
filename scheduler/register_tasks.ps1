# QT Live Trading — Windows Task Scheduler 일괄 등록
# 실행: .\scheduler\register_tasks.ps1  (관리자 권한 불필요)

$python  = "C:\Users\Quiruri\Anaconda3\envs\qt\python.exe"
$workdir = "C:\Users\Quiruri\Projects\QT"

# 현재 사용자 권한으로 실행 (관리자 불필요)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Register-QTTask {
    param($name, $script, $triggers)

    $action   = New-ScheduledTaskAction -Execute $python `
                    -Argument "$workdir\$script" `
                    -WorkingDirectory $workdir

    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable

    Register-ScheduledTask -TaskName $name `
        -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Force | Out-Null

    Write-Host "[OK] $name registered"
}

$weekdays = @("Monday","Tuesday","Wednesday","Thursday","Friday")

# 1. 22:29 — Phase 4 MOO 진입
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "22:29"
Register-QTTask "QT_MorningEntry" "scheduler\morning_entry.py" $t1

# 2. 23:00~04:00 매시 — 손절·트레일 체크 (6개 트리거)
$exitTriggers = @(
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "23:00"),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "00:00"),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "01:00"),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "02:00"),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "03:00"),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "04:00")
)
Register-QTTask "QT_ExitCheck" "scheduler\exit_check.py" $exitTriggers

# 3. 06:00 — 종가 신호 + Clenow/Weinstein 주문
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "06:00"
Register-QTTask "QT_DailyClose" "scheduler\daily_close.py" $t3

# 4. 07:00 — 텔레그램 일일 요약
$t4 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "07:00"
Register-QTTask "QT_Summary" "scheduler\summary.py" $t4

Write-Host ""
Write-Host "Registered tasks:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "QT_*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
