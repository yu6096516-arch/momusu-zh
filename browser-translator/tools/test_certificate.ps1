param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
. (Join-Path $Root 'runtime\launch.ps1')
$checks=0
function Check($Condition,[string]$Message) {
    if (!$Condition) { throw $Message }
    $script:checks++
}
function Must-Fail([scriptblock]$Action,[string]$Message) {
    $failed=$false
    try { & $Action } catch { $failed=$true }
    Check $failed $Message
}
$file=Join-Path $Root 'data\mitmproxy-ca-cert.cer'
$certificate=New-Object Security.Cryptography.X509Certificates.X509Certificate2($file)
Check (!$certificate.HasPrivateKey) 'mitmdump exports a certificate without a private key'
# Exercise the real read-only Windows CurrentUser Root query before replacing the I/O.
Check (!(Test-CurrentUserRoot $certificate)) 'fresh generated CA is not trusted by Windows'
$store=New-Object Security.Cryptography.X509Certificates.X509Store('Root','CurrentUser')
try {
    $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
    if ($store.Certificates.Count) { Check (Test-CurrentUserRoot $store.Certificates[0]) 'real existing root thumbprint is found' }
} finally { $store.Close() }

$script:trusted=@{};$script:imports=0;$script:probes=@();$script:mode='success'
function Test-CurrentUserRoot($Certificate) {
    $script:probes+=,$Certificate.Thumbprint
    return $script:trusted.ContainsKey($Certificate.Thumbprint)
}
function Install-CurrentUserRoot([string]$File) {
    $script:imports++
    Check ($File -eq $file -and $File.EndsWith('mitmproxy-ca-cert.cer')) 'only public CA file is imported'
    if ($script:mode -eq 'cancel') { throw 'simulated Windows import denied/cancelled' }
    if ($script:mode -eq 'success') { $script:trusted[$certificate.Thumbprint]=$true }
}
Ensure-CertificateTrust $file
Check ($script:imports -eq 1 -and $script:probes.Count -eq 2) 'first import followed by fresh thumbprint check'
Check (($script:probes | Where-Object { $_ -ne $certificate.Thumbprint }).Count -eq 0) 'fingerprint is always the generated CA fingerprint'
Ensure-CertificateTrust $file
Check ($script:imports -eq 1) 'already trusted CA skips import'
$script:trusted=@{'0000000000000000000000000000000000000000'=$true};$script:mode='no-write'
Must-Fail { Ensure-CertificateTrust $file } 'another fingerprint cannot satisfy post-import verification'
$script:mode='cancel'
Must-Fail { Ensure-CertificateTrust $file } 'Windows cancellation/failure stops certificate gate'
Must-Fail { Ensure-CertificateTrust ($file+'.missing') } 'missing certificate stops certificate gate'
$pfx=Join-Path $Root 'data\certificate-test.pfx'
try {
    [IO.File]::WriteAllBytes($pfx,$certificate.Export([Security.Cryptography.X509Certificates.X509ContentType]::Pfx))
    $before=$script:imports
    Must-Fail { Ensure-CertificateTrust $pfx } 'PFX containers cannot be imported'
    Check ($script:imports -eq $before) 'PFX rejected before native importer'
} finally { [IO.File]::Delete($pfx);$certificate.Dispose() }
$source=[IO.File]::ReadAllText((Join-Path $Root 'runtime\launch.ps1'))
Check ($source.IndexOf('Ensure-CertificateTrust (Join-Path') -lt $source.IndexOf('[void][Diagnostics.Process]::Start((New-BrowserCommand')) 'certificate gate precedes browser launch'
Check ($source.Contains('certutil.exe" -user -addstore Root $File') -and !$source.Contains('-importPFX')) 'native importer targets current user Root only'
@{checks=$checks;status='PASS';nativeImport='MOCKED';windowsRootQuery='READ ONLY';systemStoreModified=$false} | ConvertTo-Json -Compress
