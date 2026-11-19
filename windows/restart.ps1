#Requires -RunAsAdministrator
# Перезапуск бота (например, после замены bot.py или токена в .env)
$TaskName = "MaxStockBot"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*supervisor.ps1*" -or $_.CommandLine -like "*bot.py*" } |
    ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F | Out-Null }
Start-ScheduledTask -TaskName $TaskName
Write-Host "Бот перезапущен." -ForegroundColor Green
