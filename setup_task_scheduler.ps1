# setup_task_scheduler.ps1
# Run in an elevated PowerShell on the Windows Server 2022 VM.
# Registers a nightly Task Scheduler job to run patron_sync.py at 2:00 AM.

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe  = (Get-Command python).Source
$ScriptPath = Join-Path $ScriptDir 'patron_sync.py'
$TaskName   = 'Polaris-Patron-Sync'

$action  = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$ScriptPath`"" `
    -WorkingDirectory $ScriptDir

$trigger = New-ScheduledTaskTrigger -Daily -At '2:00AM'

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 1 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable

# Run as current logged-on user (Windows auth for SQL access)
Register-ScheduledTask `
    -TaskName   $TaskName `
    -Action     $action `
    -Trigger    $trigger `
    -Settings   $settings `
    -RunLevel   Highest `
    -Force

Write-Host "Task '$TaskName' registered. Verify with: Get-ScheduledTask -TaskName '$TaskName'"
