<#
.SYNOPSIS
    Retrieve a Starlink/Starshield OAuth access token.
.EXAMPLE
    .\Get-Token.ps1 -ClientId 'xxx' -ClientSecret 'yyy' -AccountType starshield
#>
param(
    [Parameter(Mandatory)][string]$ClientId,
    [Parameter(Mandatory)][string]$ClientSecret,
    [ValidateSet('starlink','starshield')][string]$AccountType = 'starlink'
)
$body = @{
    client_id     = $ClientId
    client_secret = $ClientSecret
    grant_type    = 'client_credentials'
}
$resp = Invoke-RestMethod -Method Post `
    -Uri "https://api.$AccountType.com/auth/connect/token" `
    -ContentType 'application/x-www-form-urlencoded' `
    -Body $body
$resp.access_token
