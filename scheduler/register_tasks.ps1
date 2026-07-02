# QT Live Trading — Windows Task Scheduler 일괄 등록
# 실행: .\scheduler\register_tasks.ps1  (관리자 권한 불필요)

$python  = "C:\Users\Quiruri\Anaconda3\envs\qt\python.exe"
$workdir = "C:\Users\Quiruri\Projects\QT"

# 현재 사용자 권한으로 실행 (관리자 불필요)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Register-QTTask {
    param($name, $script, $triggers)

    # daily_close 는 매번 가격 캐시 갱신 (--refresh) — 최신 시점 신호 산출 보장
    if ($name -eq "QT_DailyClose") {
        $scriptArg = "$workdir\$script --refresh"
    } else {
        $scriptArg = "$workdir\$script"
    }
    $action   = New-ScheduledTaskAction -Execute $python `
                    -Argument $scriptArg `
                    -WorkingDirectory $workdir

    # wednesday_morning_buy 는 최대 1시간 슬립할 수 있으므로 90분 한도
    # PowerShell 5.1: if-else expression 이 New-TimeSpan 출력 캡처 못 함 → 분리
    if ($name -eq "QT_WedMorningBuy") {
        $limit = New-TimeSpan -Minutes 90
    } else {
        $limit = New-TimeSpan -Minutes 30
    }
    $settings = New-ScheduledTaskSettingsSet `
        -ExecutionTimeLimit $limit `
        -StartWhenAvailable `
        -RunOnlyIfNetworkAvailable `
        -WakeToRun

    Register-ScheduledTask -TaskName $name `
        -Action $action -Trigger $triggers -Settings $settings `
        -Principal $principal -Force -ErrorAction Stop | Out-Null

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
# 6개 trigger array — PowerShell 의 array unwrap 방지 위해 (,) 로 wrap
Register-QTTask "QT_ExitCheck" "scheduler\exit_check.py" (,$exitTriggers)

# 3. 06:00 — 종가 신호:
#     매도(MA 이탈, rank_exit) 즉시 주문 / KR 수요일엔 매수 후보 pending 저장
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "06:00"
Register-QTTask "QT_DailyClose" "scheduler\daily_close.py" $t3

# 4. 목 00:00 — Wed 11 AM ET 매수 실행 (스크립트가 ET 11:00 까지 대기)
#     DST(서머타임 3~11월): KR 목 00:00 = Wed 11:00 ET 정확
#     표준시(11~3월): KR 목 00:00 = Wed 10:00 ET → 스크립트 1시간 슬립 후 11:00 ET 실행
$t4 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek "Thursday" -At "00:00"
Register-QTTask "QT_WedMorningBuy" "scheduler\wednesday_morning_buy.py" $t4

# 5. 07:00 — 텔레그램 일일 요약
$t5 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "07:00"
Register-QTTask "QT_Summary" "scheduler\summary.py" $t5

# 6. 05:40 — GHA 페이퍼 커밋 로컬 동기화 (git pull --ff-only)
#    로컬이 원격보다 뒤처지면 페이퍼 상태 파일이 낡아 신호가 어긋난다 (2026-05~06 divergence 사고)
$git = (Get-Command git).Source
$t6  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "05:40"
$action6 = New-ScheduledTaskAction -Execute $git -Argument "pull --ff-only origin master" -WorkingDirectory $workdir
$settings6 = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable -RunOnlyIfNetworkAvailable
Register-ScheduledTask -TaskName "QT_GitSync" -Action $action6 -Trigger $t6 -Settings $settings6 -Principal $principal -Force -ErrorAction Stop | Out-Null
Write-Host "[OK] QT_GitSync registered"

Write-Host ""
Write-Host "Registered tasks:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "QT_*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
