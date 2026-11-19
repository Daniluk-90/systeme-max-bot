#Requires -RunAsAdministrator
$TaskName = "MaxStockBot"
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*supervisor.ps1*" -or $_.CommandLine -like "*bot.py*" } |
    ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F | Out-Null }
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Бот остановлен, автозапуск удалён." -ForegroundColor Yellow
