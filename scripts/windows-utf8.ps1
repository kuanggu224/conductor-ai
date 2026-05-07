<#
Configure the current PowerShell session for UTF-8.

Usage:
  . .\scripts\windows-utf8.ps1

The leading dot matters. It applies the settings to the current shell instead
of a child PowerShell process.
#>

$ErrorActionPreference = "Stop"

chcp 65001 | Out-Null
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$PSDefaultParameterValues["Get-Content:Encoding"] = "utf8"
$PSDefaultParameterValues["Set-Content:Encoding"] = "utf8"
$PSDefaultParameterValues["Add-Content:Encoding"] = "utf8"
$PSDefaultParameterValues["Out-File:Encoding"] = "utf8"

Write-Host "PowerShell UTF-8 mode enabled for this session."
