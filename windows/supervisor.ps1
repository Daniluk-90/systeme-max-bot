# Надзиратель бота MAX: держит bot.py запущенным и перезапускает его,
# если он упал или если вернулся интернет.
param(
    [string]$Python     = "python.exe",   # install.ps1 подставит полный путь
    [string]$Script     = "bot.py",
    [int]   $CheckEvery = 15,              # проверка интернета, сек
    [int]   $FailsToOffline = 2            # столько неудач подряд = "интернета нет"
)

# bot.py ищем рядом со скриптом, а если нет — в папке выше (структура репозитория)
$WorkDir = $PSScriptRoot
if (-not (Test-Path (Join-Path $WorkDir $Script))) { $WorkDir = Split-Path $PSScriptRoot -Parent }
$LogFile = Join-Path $WorkDir "server.log"
$OutLog  = Join-Path $WorkDir "bot.out.log"
$ErrLog  = Join-Path $WorkDir "bot.log"      # сюда пишет logging самого бота

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    if ((Get-Item $LogFile).Length -gt 5MB) { Move-Item $LogFile "$LogFile.old" -Force }
}

function Test-Internet {
    foreach ($t in @(@("77.88.8.8", 53), @("1.1.1.1", 53), @("8.8.8.8", 53))) {
        $c = New-Object System.Net.Sockets.TcpClient
        try {
            $task = $c.ConnectAsync($t[0], $t[1])
            if ($task.Wait(3000) -and $c.Connected) { return $true }
        } catch { } finally { $c.Close() }
    }
    return $false
}

function Start-Bot {
    try {
        # перед запуском сохраняем прошлый лог бота
        if ((Test-Path $ErrLog) -and (Get-Item $ErrLog).Length -gt 0) {
            Move-Item $ErrLog "$ErrLog.prev" -Force
        }
        $env:PYTHONIOENCODING = "utf-8"
        $p = Start-Process -FilePath $Python -ArgumentList "-u `"$Script`"" -WorkingDirectory $WorkDir `
             -PassThru -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
        Log "Бот запущен (PID $($p.Id))"
        return $p
    } catch {
        Log "ОШИБКА запуска: $($_.Exception.Message)"
        return $null
    }
}

function Stop-Bot($p) {
    if ($p -and -not $p.HasExited) {
        & taskkill.exe /PID $p.Id /T /F | Out-Null
        Log "Бот остановлен (PID $($p.Id))"
        Start-Sleep -Seconds 2
    }
}

Log "=== Надзиратель запущен, Python: $Python ==="
$online = Test-Internet
if (-not $online) { Log "Интернета пока нет" }
$fails = 0
$proc  = Start-Bot

while ($true) {
    Start-Sleep -Seconds $CheckEvery

    if ($null -eq $proc -or $proc.HasExited) {
        if ($proc) { Log "Бот завершился (код $($proc.ExitCode)), перезапуск. Причина в bot.log.prev" }
        $proc = Start-Bot
        continue
    }

    if (Test-Internet) {
        $fails = 0
        if (-not $online) {
            Log "Интернет вернулся, перезапускаю бота"
            Stop-Bot $proc
            $proc = Start-Bot
            $online = $true
        }
    } else {
        $fails++
        if ($online -and $fails -ge $FailsToOffline) {
            Log "Интернет пропал, жду восстановления"
            $online = $false
        }
    }
}
