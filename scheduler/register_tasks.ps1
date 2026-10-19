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

    # 인자 변환 오류는 -ErrorAction Stop 으로 안 멈춰서 실패해도 [OK] 가 찍혔다 → try/catch
    try {
        Register-ScheduledTask -TaskName $name `
            -Action $action -Trigger $triggers -Settings $settings `
            -Principal $principal -Force -ErrorAction Stop | Out-Null
        Write-Host "[OK] $name registered"
    } catch {
        Write-Host "[FAIL] $name : $($_.Exception.Message)" -ForegroundColor Red
        if ($_.Exception.HResult -eq -2147024891) {   # 0x80070005 액세스 거부
            # 관리자 권한으로 만든 작업 → 관리자 PowerShell 에서 삭제 후 재실행
            Write-Host "       Created by admin. In admin PowerShell: Unregister-ScheduledTask $name -Confirm:`$false ; then rerun this script" -ForegroundColor Yellow
        }
    }
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
# 함수 인자로는 배열이 그대로 전달된다. (,) 로 감싸면 배열 안의 배열이 돼 등록 실패
Register-QTTask "QT_ExitCheck" "scheduler\exit_check.py" $exitTriggers

# 3. 06:30 — 종가 신호:
#     매도(MA 이탈, rank_exit) 즉시 주문 / KR 수요일엔 매수 후보 pending 저장
#     06:00 이면 표준시(11~3월)에 16:00 ET 마감 정각이라 당일 일봉 미확정 → 06:30 (2026-10)
$t3 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "06:30"
Register-QTTask "QT_DailyClose" "scheduler\daily_close.py" $t3

# 4. 목 00:00 — Wed 11 AM ET 매수 실행 (스크립트가 ET 11:00 까지 대기)
#     DST(서머타임 3~11월): KR 목 00:00 = Wed 11:00 ET 정확
#     표준시(11~3월): KR 목 00:00 = Wed 10:00 ET → 스크립트 1시간 슬립 후 11:00 ET 실행
$t4 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek "Thursday" -At "00:00"
Register-QTTask "QT_WedMorningBuy" "scheduler\wednesday_morning_buy.py" $t4

# 5. 07:00 — 텔레그램 일일 요약
$t5 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "07:00"
Register-QTTask "QT_Summary" "scheduler\summary.py" $t5

# 6. 05:40 — GHA 페이퍼 커밋 로컬 동기화 (git pull --ff-only, 실패 시 텔레그램)
#    로컬이 원격보다 뒤처지면 페이퍼 상태 파일이 낡아 신호가 어긋난다 (2026-05~06 divergence 사고)
#    git 을 직접 실행하면 실패가 아무 데도 안 보여서 73커밋 정체를 놓쳤다 (2026-10) → 스크립트 경유
$t6  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "05:40"
Register-QTTask "QT_GitSync" "scheduler\git_sync.py" $t6

Write-Host ""
Write-Host "Registered tasks:"
Get-ScheduledTask | Where-Object { $_.TaskName -like "QT_*" } |
    Select-Object TaskName, State | Format-Table -AutoSize
