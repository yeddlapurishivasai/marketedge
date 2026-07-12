<#
.SYNOPSIS
    Creates (or updates) the Azure Entra ID app registrations MarketEdge needs and,
    optionally, writes the resulting configuration into the API's appsettings.

.DESCRIPTION
    Registers three applications in the target tenant using the Microsoft Graph
    PowerShell SDK:

        * MarketEdge-API     - the protected Web API. Exposes a delegated scope
                               (access_as_user) for user sign-in AND an app role
                               (Api.Access) for client-credentials callers.
        * MarketEdge-SPA     - the React single-page app (public client). Granted
                               the API scope and pre-authorized so users are not
                               prompted for consent.
        * MarketEdge-Client  - a confidential "daemon" client with a secret,
                               granted the Api.Access app role (admin-consented)
                               so it can call the API directly with its own token.

    The script is idempotent: it looks up each app by display name and reuses it
    (including existing scope / role ids) instead of creating duplicates.

    Because the SPA pulls its MSAL settings from the API at runtime
    (GET /api/auth/config), pointing the app at a different tenant only requires
    re-running this script there and updating the API's AzureAd config section
    (use -WriteConfig to do that automatically).

.PARAMETER TenantId
    The Entra tenant (GUID or *.onmicrosoft.com domain) to create the apps in.

.PARAMETER ApiAppName
    Display name for the API app registration. Default: MarketEdge-API.

.PARAMETER SpaAppName
    Display name for the SPA app registration. Default: MarketEdge-SPA.

.PARAMETER DaemonAppName
    Display name for the daemon/client app registration. Default: MarketEdge-Client.

.PARAMETER SpaRedirectUris
    Redirect URIs registered under the SPA platform. Default covers the Vite dev
    server and the API-hosted origin. Add your production URL for real deployments.

.PARAMETER SecretMonths
    Lifetime (in months) of the generated daemon client secret. Default: 12.

.PARAMETER WriteConfig
    When set, writes the resulting AzureAd settings into -AppSettingsPath.

.PARAMETER AppSettingsPath
    appsettings file to update when -WriteConfig is used.
    Default: src\MarketEdge.Api\appsettings.Development.json

.PARAMETER UseDeviceCode
    Authenticate using the device-code flow instead of an interactive browser
    window. Useful on remote/headless machines with no default browser.

.EXAMPLE
    .\scripts\Setup-EntraApps.ps1 -TenantId contoso.onmicrosoft.com

.EXAMPLE
    .\scripts\Setup-EntraApps.ps1 -TenantId 00000000-0000-0000-0000-000000000000 -WriteConfig

.EXAMPLE
    .\scripts\Setup-EntraApps.ps1 -TenantId contoso.onmicrosoft.com `
        -SpaRedirectUris 'http://localhost:5173','https://marketedge.contoso.com'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $TenantId,

    [string] $ApiAppName    = 'MarketEdge-API',
    [string] $SpaAppName    = 'MarketEdge-SPA',
    [string] $DaemonAppName = 'MarketEdge-Client',

    [string[]] $SpaRedirectUris = @('http://localhost:5173', 'http://localhost:5063'),

    [int] $SecretMonths = 12,

    [switch] $WriteConfig,
    [string] $AppSettingsPath,

    [switch] $UseDeviceCode
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# --------------------------------------------------------------------------- #
# Constants & paths
# --------------------------------------------------------------------------- #
$RepoRoot   = Split-Path -Parent $PSScriptRoot
$ScopeValue = 'access_as_user'
$RoleValue  = 'Api.Access'
$Instance   = 'https://login.microsoftonline.com/'

if (-not $AppSettingsPath) {
    $AppSettingsPath = Join-Path $RepoRoot 'src\MarketEdge.Api\appsettings.Development.json'
}

# --------------------------------------------------------------------------- #
# Logging helpers
# --------------------------------------------------------------------------- #
function Write-Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Info($m) { Write-Host "    $m" -ForegroundColor Gray }
function Write-Ok($m)   { Write-Host "    OK  $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "    !!  $m" -ForegroundColor Yellow }

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
function Invoke-WithRetry {
    param([scriptblock] $Script, [int] $Retries = 6, [int] $DelaySec = 5)
    for ($i = 1; $i -le $Retries; $i++) {
        try { return & $Script }
        catch {
            if ($i -eq $Retries) { throw }
            Write-Info "Retry $i/$Retries after transient error: $($_.Exception.Message)"
            Start-Sleep -Seconds $DelaySec
        }
    }
}

function Get-AppByName([string] $Name) {
    Get-MgApplication -Filter "displayName eq '$Name'" -All | Select-Object -First 1
}

function Get-OrCreateApp([string] $Name) {
    $app = Get-AppByName $Name
    if ($app) {
        Write-Ok "Found existing app '$Name' (appId $($app.AppId))"
        return $app
    }
    $app = New-MgApplication -DisplayName $Name -SignInAudience 'AzureADMyOrg'
    Write-Ok "Created app '$Name' (appId $($app.AppId))"
    return $app
}

function Get-OrCreateSp([string] $AppId) {
    $sp = Get-MgServicePrincipal -Filter "appId eq '$AppId'" -All | Select-Object -First 1
    if (-not $sp) {
        $sp = Invoke-WithRetry { New-MgServicePrincipal -AppId $AppId }
        Write-Ok "Created service principal for appId $AppId"
    }
    return $sp
}

# --------------------------------------------------------------------------- #
# Module + connection
# --------------------------------------------------------------------------- #
function Connect-Graph {
    Write-Step 'Ensuring Microsoft Graph PowerShell SDK is available'
    $needed = @('Microsoft.Graph.Applications', 'Microsoft.Graph.Identity.SignIns')
    if ($needed | Where-Object { -not (Get-Module -ListAvailable -Name $_) }) {
        Write-Info 'Installing Microsoft.Graph (CurrentUser scope) - this can take a few minutes...'
        Install-Module Microsoft.Graph -Scope CurrentUser -Force -AllowClobber
    }
    Import-Module Microsoft.Graph.Authentication   -ErrorAction Stop
    Import-Module Microsoft.Graph.Applications      -ErrorAction Stop
    Import-Module Microsoft.Graph.Identity.SignIns  -ErrorAction Stop
    Write-Ok 'Graph modules loaded'

    Write-Step "Connecting to tenant '$TenantId'"
    $scopes = @(
        'Application.ReadWrite.All',
        'AppRoleAssignment.ReadWrite.All',
        'DelegatedPermissionGrant.ReadWrite.All'
    )
    # Clear any cached context so every run is a fresh, deliberate sign-in. This
    # prevents silently reusing a previously chosen (e.g. personal) account.
    Disconnect-MgGraph -ErrorAction SilentlyContinue | Out-Null

    $connectParams = @{ TenantId = $TenantId; Scopes = $scopes; NoWelcome = $true }
    if ($UseDeviceCode) { $connectParams['UseDeviceCode'] = $true }
    Connect-MgGraph @connectParams | Out-Null
    $ctx = Get-MgContext
    if (-not $ctx) { throw 'Sign-in failed: no Microsoft Graph context was established.' }
    if ($TenantId -match '^[0-9a-fA-F-]{36}$' -and $ctx.TenantId -ne $TenantId) {
        throw ("Signed in as '$($ctx.Account)' to tenant '$($ctx.TenantId)', but these apps must be " +
               "created in tenant '$TenantId'. This usually means a personal Microsoft account (consumer " +
               'identity) was chosen. Re-run and sign in with a work/school (organizational) admin account ' +
               'that belongs to the target tenant.')
    }
    $required = @('Application.ReadWrite.All', 'AppRoleAssignment.ReadWrite.All', 'DelegatedPermissionGrant.ReadWrite.All')
    $missing  = $required | Where-Object { $_ -notin @($ctx.Scopes) }
    if ($missing) {
        throw ("Signed in as '$($ctx.Account)', but the token is missing required Graph permission(s): " +
               "$($missing -join ', '). These are admin-restricted and need admin consent. Re-run and, on the " +
               "consent screen, tick 'Consent on behalf of your organization' and Accept. If your tenant uses " +
               'Privileged Identity Management, activate your Global Administrator role first, then retry.')
    }
    Write-Ok "Connected as $($ctx.Account) to tenant $($ctx.TenantId)"
    return [string]$ctx.TenantId
}

# --------------------------------------------------------------------------- #
# 1. API app: expose scope + app role
# --------------------------------------------------------------------------- #
function Set-ApiApp {
    Write-Step "Configuring API app '$ApiAppName'"
    $api = Get-OrCreateApp $ApiAppName

    # Reuse existing ids where present so re-runs stay stable.
    $existingScope = @($api.Api.Oauth2PermissionScopes | Where-Object { $_.Value -eq $ScopeValue })
    $scopeId = if ($existingScope.Count -gt 0) { $existingScope[0].Id } else { [guid]::NewGuid().ToString() }

    $existingRole = @($api.AppRoles | Where-Object { $_.Value -eq $RoleValue })
    $roleId = if ($existingRole.Count -gt 0) { $existingRole[0].Id } else { [guid]::NewGuid().ToString() }

    $scope = @{
        Id                      = $scopeId
        Value                   = $ScopeValue
        Type                    = 'User'
        IsEnabled               = $true
        AdminConsentDisplayName = "Access $ApiAppName"
        AdminConsentDescription = "Allow the application to access $ApiAppName on behalf of the signed-in user."
        UserConsentDisplayName  = "Access $ApiAppName"
        UserConsentDescription  = "Allow the application to access $ApiAppName on your behalf."
    }

    $appRole = @{
        Id                 = $roleId
        Value              = $RoleValue
        IsEnabled          = $true
        AllowedMemberTypes = @('Application')
        DisplayName        = "Access $ApiAppName as an application"
        Description        = "Allows an application to call $ApiAppName using its own identity."
    }

    Invoke-WithRetry {
        Update-MgApplication -ApplicationId $api.Id `
            -IdentifierUris @("api://$($api.AppId)") `
            -Api @{ RequestedAccessTokenVersion = 2; Oauth2PermissionScopes = @($scope) } `
            -AppRoles @($appRole)
    }
    Write-Ok "Exposed scope '$ScopeValue' and app role '$RoleValue'"

    $sp = Get-OrCreateSp $api.AppId

    return [pscustomobject]@{
        App = Get-MgApplication -ApplicationId $api.Id
        Sp  = $sp
        ScopeId = $scopeId
        RoleId  = $roleId
    }
}

# --------------------------------------------------------------------------- #
# 2. SPA app: public client, delegated permission, pre-authorization
# --------------------------------------------------------------------------- #
function Set-SpaApp($Api) {
    Write-Step "Configuring SPA app '$SpaAppName'"
    $spa = Get-OrCreateApp $SpaAppName

    $rra = @{
        ResourceAppId  = $Api.App.AppId
        ResourceAccess = @(@{ Id = $Api.ScopeId; Type = 'Scope' })
    }
    Invoke-WithRetry {
        Update-MgApplication -ApplicationId $spa.Id `
            -Spa @{ RedirectUris = $SpaRedirectUris } `
            -RequiredResourceAccess @($rra)
    }
    Write-Ok ("Registered SPA redirect URIs: {0}" -f ($SpaRedirectUris -join ', '))

    $spaSp = Get-OrCreateSp $spa.AppId

    # Pre-authorize the SPA on the API so no consent prompt is shown.
    $scope = @{
        Id                      = $Api.ScopeId
        Value                   = $ScopeValue
        Type                    = 'User'
        IsEnabled               = $true
        AdminConsentDisplayName = "Access $ApiAppName"
        AdminConsentDescription = "Allow the application to access $ApiAppName on behalf of the signed-in user."
        UserConsentDisplayName  = "Access $ApiAppName"
        UserConsentDescription  = "Allow the application to access $ApiAppName on your behalf."
    }
    Invoke-WithRetry {
        Update-MgApplication -ApplicationId $Api.App.Id -Api @{
            RequestedAccessTokenVersion = 2
            Oauth2PermissionScopes      = @($scope)
            PreAuthorizedApplications   = @(@{ AppId = $spa.AppId; DelegatedPermissionIds = @($Api.ScopeId) })
        }
    }
    Write-Ok 'Pre-authorized SPA on the API'

    # Tenant-wide admin consent for the delegated permission.
    Invoke-WithRetry {
        $existing = Get-MgOauth2PermissionGrant -All |
            Where-Object { $_.ClientId -eq $spaSp.Id -and $_.ResourceId -eq $Api.Sp.Id } |
            Select-Object -First 1
        if ($existing) {
            Update-MgOauth2PermissionGrant -OAuth2PermissionGrantId $existing.Id -Scope $ScopeValue
        } else {
            New-MgOauth2PermissionGrant -BodyParameter @{
                ClientId    = $spaSp.Id
                ConsentType = 'AllPrincipals'
                ResourceId  = $Api.Sp.Id
                Scope       = $ScopeValue
            } | Out-Null
        }
    }
    Write-Ok 'Granted admin consent for delegated access'

    return [pscustomobject]@{ App = $spa; Sp = $spaSp }
}

# --------------------------------------------------------------------------- #
# 3. Daemon app: confidential client + secret + app-role (admin consent)
# --------------------------------------------------------------------------- #
function Set-DaemonApp($Api) {
    Write-Step "Configuring daemon app '$DaemonAppName'"
    $daemon = Get-OrCreateApp $DaemonAppName

    $rra = @{
        ResourceAppId  = $Api.App.AppId
        ResourceAccess = @(@{ Id = $Api.RoleId; Type = 'Role' })
    }
    Invoke-WithRetry {
        Update-MgApplication -ApplicationId $daemon.Id -RequiredResourceAccess @($rra)
    }

    $daemonSp = Get-OrCreateSp $daemon.AppId

    Write-Info 'Generating a new client secret'
    $secret = Add-MgApplicationPassword -ApplicationId $daemon.Id -PasswordCredential @{
        DisplayName = 'MarketEdge client secret'
        EndDateTime = (Get-Date).AddMonths($SecretMonths)
    }
    Write-Ok "Secret created (expires $($secret.EndDateTime.ToString('yyyy-MM-dd')))"

    # Admin consent for the application permission = assign the app role to the daemon SP.
    Invoke-WithRetry {
        $existing = Get-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $daemonSp.Id -All |
            Where-Object { $_.AppRoleId -eq $Api.RoleId -and $_.ResourceId -eq $Api.Sp.Id } |
            Select-Object -First 1
        if (-not $existing) {
            New-MgServicePrincipalAppRoleAssignment -ServicePrincipalId $daemonSp.Id -BodyParameter @{
                PrincipalId = $daemonSp.Id
                ResourceId  = $Api.Sp.Id
                AppRoleId   = $Api.RoleId
            } | Out-Null
        }
    }
    Write-Ok "Granted app role '$RoleValue' to the daemon (admin consent)"

    return [pscustomobject]@{ App = $daemon; Sp = $daemonSp; Secret = $secret.SecretText }
}

# --------------------------------------------------------------------------- #
# Optionally patch appsettings
# --------------------------------------------------------------------------- #
function Write-AppSettings($ResolvedTenantId, $Api, $Spa) {
    if (-not (Test-Path $AppSettingsPath)) {
        Write-Warn2 "appsettings file not found at $AppSettingsPath - skipping -WriteConfig."
        return
    }
    Write-Step "Writing AzureAd config into $AppSettingsPath"
    $json = Get-Content $AppSettingsPath -Raw | ConvertFrom-Json -Depth 20
    $azureAd = [ordered]@{
        Enabled     = $true
        Instance    = $Instance
        TenantId    = $ResolvedTenantId
        ClientId    = $Api.App.AppId
        SpaClientId = $Spa.App.AppId
        Scopes      = $ScopeValue
        AppRoles    = $RoleValue
    }
    $json | Add-Member -NotePropertyName AzureAd -NotePropertyValue ([pscustomobject]$azureAd) -Force
    $json | ConvertTo-Json -Depth 20 | Set-Content -Path $AppSettingsPath -Encoding UTF8
    Write-Ok 'appsettings updated (auth is now ENABLED for this environment)'
}

# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
$resolvedTenantId = Connect-Graph

$api    = Set-ApiApp
$spa    = Set-SpaApp    -Api $api
$daemon = Set-DaemonApp -Api $api

if ($WriteConfig) { Write-AppSettings -ResolvedTenantId $resolvedTenantId -Api $api -Spa $spa }

$authority   = "$($Instance.TrimEnd('/'))/$resolvedTenantId"
$apiScope    = "api://$($api.App.AppId)/$ScopeValue"
$tokenUrl    = "$authority/oauth2/v2.0/token"

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host ' MarketEdge Entra app registrations are ready' -ForegroundColor Green
Write-Host '============================================================' -ForegroundColor Green
Write-Host ''
Write-Host ' API config (put this in appsettings AzureAd, or use -WriteConfig):' -ForegroundColor Cyan
$configBlock = [ordered]@{
    Enabled     = $true
    Instance    = $Instance
    TenantId    = $resolvedTenantId
    ClientId    = $api.App.AppId
    SpaClientId = $spa.App.AppId
    Scopes      = $ScopeValue
    AppRoles    = $RoleValue
}
Write-Host (([pscustomobject]$configBlock | ConvertTo-Json))
Write-Host ''
Write-Host ' Identifiers:' -ForegroundColor Cyan
Write-Host ("   Tenant Id        : {0}" -f $resolvedTenantId)
Write-Host ("   API client id    : {0}" -f $api.App.AppId)
Write-Host ("   API scope        : {0}" -f $apiScope)
Write-Host ("   SPA client id    : {0}" -f $spa.App.AppId)
Write-Host ("   Daemon client id : {0}" -f $daemon.App.AppId)
Write-Host ''
Write-Host ' Daemon client secret (shown once - store it securely):' -ForegroundColor Yellow
Write-Host ("   {0}" -f $daemon.Secret)
Write-Host ''
Write-Host ' Direct API access (client-credentials) example:' -ForegroundColor Cyan
Write-Host ("   curl -X POST `"{0}`" \" -f $tokenUrl)
Write-Host ("        -d `"grant_type=client_credentials`" \")
Write-Host ("        -d `"client_id={0}`" \" -f $daemon.App.AppId)
Write-Host  "        -d `"client_secret=<the-secret-above>`" \"
Write-Host ("        -d `"scope=api://{0}/.default`"" -f $api.App.AppId)
Write-Host ''
Write-Host '   Then call the API with:  Authorization: Bearer <access_token>' -ForegroundColor Gray
Write-Host ''
Write-Host ' To DISABLE auth entirely, set AzureAd:Enabled = false in appsettings.' -ForegroundColor Gray
Write-Host ''
