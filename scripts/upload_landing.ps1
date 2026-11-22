# Upload landing files up to a date (by arrival folder), simulating daily arrivals.
# Usage:  $env:LANDING_SAS = "<SAS token from Cloud Shell>"
#         .\scripts\upload_landing.ps1 -Until 2014-06-21
param(
    [Parameter(Mandatory)][string]$Until,
    [string]$Account = "stsupplychainiqjd",
    [string]$Sas = $env:LANDING_SAS
)
$ErrorActionPreference = "Stop"
if (-not $Sas -or $Sas -notmatch "sig=") { throw "Set `$env:LANDING_SAS to the real SAS token first" }
$root  = Split-Path $PSScriptRoot -Parent
$src   = Join-Path $root "data\generated\landing"
$stage = Join-Path $root "data\upload_stage"
$cut   = [datetime]$Until
$added = 0
foreach ($ds in "pos", "shipments", "inventory") {
    Get-ChildItem (Join-Path $src $ds) -Directory |
        Where-Object { [datetime]$_.Name -le $cut } |
        ForEach-Object {
            $dest = Join-Path $stage "$ds\$($_.Name)"
            if (-not (Test-Path $dest)) {
                New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
                Copy-Item $_.FullName $dest -Recurse
                $added++
            }
        }
}
Write-Host "Staged $added new date folders (up to $Until). Uploading new files only..."
azcopy sync $stage "https://$Account.blob.core.windows.net/landing?$Sas" --recursive