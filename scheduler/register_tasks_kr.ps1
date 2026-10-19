# QT Live Trading (KR) - Windows Task Scheduler 일괄 등록
# 실행: .\scheduler\register_tasks_kr.ps1  (관리자 권한 불필요)
#
# KR 트랙 작업 4개:
#   QT_KR_DailyClose      - 평일 16:00 KST (KOSPI 마감 + 30분, 매수후보/매도예약 산출)
#   QT_KR_KWSVMorningSell - 평일 09:00 KST (KW SV 탈락분 매도 — KIS 가 정규장 밖
#                           매도 주문을 거부(APBK0918)해서 16:00 즉시매도 불가능,
#                           다음 영업일 개장 직후로 미뤄 처리. 2026-10 추가)
#   QT_KR_KWSVMorningBuy  - 평일 09:10 KST (분기 리밸런싱 매수 후보 체결 — 과거엔
#                           "수요일 고정"이었는데 리밸런싱일(5/16,8/16,11/16,4/1)
#                           이 화요일에만 걸리는 게 아니라서 다음 수요일까지
#                           최대 6일 지연되던 버그를 수정, 2026-10. 매도 스크립트와
#                           10분 띄움 — 둘 다 09:00 이면 order_map.json 동시 쓰기로
#                           한쪽 기록이 소실되는 레이스 컨디션 발생(2026-10-02 실제
#                           발생, 매수 11건 소실 — _save_map() 병합 로직으로도 보강)
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
    if ($name -eq "QT_KR_KWSVMorningBuy" -or $name -eq "QT_KR_DailyClose" -or $name -eq "QT_KR_KWSVMorningSell") {
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

# 2. 09:00 KST 평일 — KW SV 탈락분 매도 (pending 없으면 즉시 종료, 평소엔 no-op)
$t4 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "09:00"
Register-QTKRTask "QT_KR_KWSVMorningSell" "scheduler\kw_sv_morning_sell.py" $t4

# 3. 09:10 KST 평일 — KW SV 매수 후보 체결 (매도와 10분 띄움, pending 없으면 no-op)
$t2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "09:10"
Register-QTKRTask "QT_KR_KWSVMorningBuy" "scheduler\wednesday_morning_buy_kr.py" $t2

# 4. 16:30 KST 평일 — KR summary 텔레그램
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "16:30"
Register-QTKRTask "QT_KR_Summary" "scheduler\summary_kr.py" $t3

Write-Host ""
Write-Host "Registered KR tasks:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "QT_KR_*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
