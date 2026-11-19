#Requires -RunAsAdministrator
# Установка бота MAX на Windows: автозапуск при включении ПК, без входа в систему.

$TaskName = "MaxStockBot"
$Dir      = $PSScriptRoot

# --- 1. Ищем Python (пропускаем заглушку из Microsoft Store) ---
$Python = $null
try { $Python = (& py -3 -c "import sys; print(sys.executable)" 2>$null) } catch { }
if (-not $Python -or -not (Test-Path $Python)) {
    $Python = Get-Command python.exe -All -ErrorAction SilentlyContinue |
              Where-Object { $_.Source -notlike "*WindowsApps*" } |
              Select-Object -First 1 -ExpandProperty Source
}
if (-not $Python) {
    Write-Host "Python не найден. Установите с python.org (галочка 'Add python.exe to PATH') и запустите install.ps1 ещё раз." -ForegroundColor Red
    exit 1
}
Write-Host "Python: $Python"

# Python, поставленный "только для меня", лежит в профиле пользователя.
# Задача работает от SYSTEM — проверяем, что ей доступен этот python.
if ($Python -like "*\AppData\*") {
    Write-Host "Внимание: Python установлен только для текущего пользователя. Обычно это работает," -ForegroundColor Yellow
    Write-Host "но надёжнее переустановить Python с галочкой 'Install for all users'." -ForegroundColor Yellow
}

# --- 2. Токен бота ---
$BotDir = $Dir
if (-not (Test-Path (Join-Path $BotDir "bot.py"))) { $BotDir = Split-Path $Dir -Parent }
if (-not (Test-Path (Join-Path $BotDir "bot.py"))) { Write-Host "Не найден bot.py" -ForegroundColor Red; exit 1 }
$EnvFile = Join-Path $BotDir ".env"
$hasToken = (Test-Path $EnvFile) -and (Select-String -Path $EnvFile -Pattern "^MAX_BOT_TOKEN=\S+" -Quiet)
if (-not $hasToken) {
    $token = Read-Host "Вставьте токен бота MAX (business.max.ru -> Чат-боты -> ... -> Настройки)"
    $token = $token.Trim()
    if (-not $token) { Write-Host "Токен пустой, установка прервана." -ForegroundColor Red; exit 1 }
    $content = "MAX_BOT_TOKEN=$token`r`nSYSTEME_TOKEN=`r`n"
    [System.IO.File]::WriteAllText($EnvFile, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Токен сохранён в .env"
}

# --- 3. Задача планировщика: запуск при старте Windows ---
$Supervisor = Join-Path $Dir "supervisor.ps1"
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Supervisor`" -Python `"$Python`""
$action    = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $Dir
$trigger   = New-ScheduledTaskTrigger -AtStartup
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

# --- 4. Не засыпать при питании от сети ---
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 8
Write-Host ""
Write-Host "Готово! Бот запущен и будет стартовать сам при включении компьютера." -ForegroundColor Green
Write-Host "Лог надзирателя: $Dir\server.log"
Write-Host "Лог бота:        $BotDir\bot.log"
if (Test-Path (Join-Path $BotDir "bot.log")) {
    Write-Host "--- последние строки bot.log ---"
    Get-Content (Join-Path $BotDir "bot.log") -Tail 5 -Encoding UTF8
}
