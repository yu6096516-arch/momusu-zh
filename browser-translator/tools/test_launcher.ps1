param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference='Stop'
. (Join-Path $Root 'runtime\launch.ps1')
$checks=0
function Check($Condition, $Message) {
    if (!$Condition) { throw $Message }
    $script:checks++
}
$c=Read-Config (Join-Path $Root 'config.ini')
Check ($c.network.listen_port -eq '8080' -and !$c.network.upstream_port) 'default config'
$c.network.listen_port='19090'
foreach ($type in @('chrome','edge','custom')) {
    $c.browser.type=$type
    $b=New-BrowserCommand $c 'C:\中文 Browser\browser.exe'
    Check ($b.Arguments -eq ('--proxy-server="http://127.0.0.1:19090" --disable-quic "'+$GameUrl+'"')) "$type default arguments"
    Check (!$b.UseShellExecute -and $b.FileName -eq 'C:\中文 Browser\browser.exe') "$type direct execution"
}
$c.browser.args='--proxy-server="http://127.0.0.1:9999" --title="中文 two words" --literal="& echo nope"'
$b=New-BrowserCommand $c 'C:\中文 Browser\browser.exe'
Check ($b.Arguments -eq ($c.browser.args+' "'+$GameUrl+'"')) 'custom args exact replacement'
Check (!$b.Arguments.Contains('--disable-quic')) 'no defaults appended'
$direct=Get-ServiceArguments $c
Check (!($direct -contains '--mode') -and ($direct -contains '19090')) 'direct connection and port'
$c.network.upstream_port='17890';$upstream=Get-ServiceArguments $c
Check (($upstream -contains '--mode') -and ($upstream -contains 'upstream:http://127.0.0.1:17890')) 'upstream'
Check (($upstream -contains 'ssl_insecure=false') -and ($upstream -contains '127.0.0.1')) 'local TLS verification'
# Phase 33: quiet by default, full network logs only in developer debug mode.
Check ($c.network.debug -eq 'false') 'debug defaults to false'
Check (($direct -contains 'flow_detail=0') -and ($direct -contains 'termlog_verbosity=error')) 'quiet mode suppresses traffic chatter'
Check (!(($direct | Where-Object { $_ -like '*>nul*' }))) 'errors are not hidden with >nul'
$c.network.debug='true'
$dbg=Get-ServiceArguments $c
Check (($dbg -contains 'flow_detail=3') -and ($dbg -contains 'termlog_verbosity=debug')) 'debug mode restores full logs'
$c.network.debug='false'
$c2=Read-Config (Join-Path $Root 'config.ini')
$c2.network.debug='oops'
$t=(Join-Path $Root 'work')+'\launcher_debug_bad.ini'
[IO.File]::WriteAllText($t,"[network]`nlisten_port = 8080`nupstream_port =`ndebug = oops`n",[Text.Encoding]::UTF8)
try { Read-Config $t | Out-Null; throw 'invalid debug value was accepted' }
catch { if ($_.Exception.Message -eq 'invalid debug value was accepted') { throw } }
finally { [IO.File]::Delete($t) }
$c.browser.path='"'+(Join-Path $Root 'runtime\mitmdump.exe')+'"'
Check ((Find-Browser $c.browser) -eq (Join-Path $Root 'runtime\mitmdump.exe')) 'quoted custom path'
# Resolve Chrome/Edge against isolated common-path fixtures; never launch them.
$save=@($env:LOCALAPPDATA,$env:ProgramFiles,${env:ProgramFiles(x86)})
$fixture=Join-Path $Root 'launcher-test'
try {
    $env:LOCALAPPDATA=$fixture;$env:ProgramFiles=$fixture;${env:ProgramFiles(x86)}=$fixture
    foreach ($pair in @(@('chrome','Google\Chrome\Application\chrome.exe'),@('edge','Microsoft\Edge\Application\msedge.exe'))) {
        $p=Join-Path $fixture $pair[1]
        [void][IO.Directory]::CreateDirectory((Split-Path -Parent $p));[IO.File]::WriteAllBytes($p,[byte[]]@())
        Check ((Find-Browser @{type=$pair[0]}) -eq $p) ($pair[0]+' common path')
    }
} finally {
    $env:LOCALAPPDATA=$save[0];$env:ProgramFiles=$save[1];${env:ProgramFiles(x86)}=$save[2]
    # Remove only explicitly named fixture files/directories inside this test root.
    [IO.File]::Delete((Join-Path $fixture 'Google\Chrome\Application\chrome.exe'))
    [IO.File]::Delete((Join-Path $fixture 'Microsoft\Edge\Application\msedge.exe'))
}
@{checks=$checks;customCommandLine=$b.Arguments;browser='NOT RUN'} | ConvertTo-Json -Compress
