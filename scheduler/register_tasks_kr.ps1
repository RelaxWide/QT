# QT Live Trading (KR) - Windows Task Scheduler 일괄 등록
# 실행: .\scheduler\register_tasks_kr.ps1  (관리자 권한 불필요)
#
# KR 트랙 작업 3개:
#   QT_KR_DailyClose      - 평일 16:00 KST (KOSPI 마감 + 30분, 매도 즉시 / 화요일 매수 후보)
#   QT_KR_WedMorningBuy   - 매주 수 09:00 KST (시초가 매수, 스크립트 내부 09:00 대기)
#   QT_KR_Summary         - 평일 16:30 KST (텔레그램 일일 요약)

$python  = "C:\Users\Quiruri\Anaconda3\envs\qt\python.exe"
$workdir = "C:\Users\Quiruri\Projects\QT"

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

function Register-QTKRTask {
    param($name, $script, $triggers)

    # daily_close_kr 는 매번 가격 캐시 갱신 (--refresh) — 최신 시점 신호 산출 보장
    if ($name -eq "QT_KR_DailyClose") {
        $scriptArg = "$workdir\$script --refresh"
    } else {
        $scriptArg = "$workdir\$script"
    }
    $action   = New-ScheduledTaskAction -Execute $python `
                    -Argument $scriptArg `
                    -WorkingDirectory $workdir

    # wed_buy_kr: 최대 30분 슬립 / daily_close_kr: 분기 리밸런싱일엔 KW SV universe+DART
    # 처리로 무거움 → 둘 다 60분 한도. (평일 daily_close_kr 은 인덱스 1종목만 받고 즉시 종료)
    # PowerShell 5.1: if-else expression 이 New-TimeSpan 출력 캡처 못 함 → 분리
    if ($name -eq "QT_KR_WedMorningBuy" -or $name -eq "QT_KR_DailyClose") {
        $limit = New-TimeSpan -Minutes 60
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

# 1. 16:00 KST 평일 — KR daily close (KOSPI 마감 + 30분 안전 마진)
$t1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "16:00"
Register-QTKRTask "QT_KR_DailyClose" "scheduler\daily_close_kr.py" $t1

# 2. 09:00 KST 수요일 — KR Wed buy 실행 (KOSPI 정규장 개장 직후)
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek "Wednesday" -At "09:00"
Register-QTKRTask "QT_KR_WedMorningBuy" "scheduler\wednesday_morning_buy_kr.py" $t2

# 3. 16:30 KST 평일 — KR summary 텔레그램
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "16:30"
Register-QTKRTask "QT_KR_Summary" "scheduler\summary_kr.py" $t3

Write-Host ""
Write-Host "Registered KR tasks:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "QT_KR_*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
