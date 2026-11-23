param()
$ErrorActionPreference = 'Stop'
$AppRoot = Split-Path -Parent $PSScriptRoot
$GameUrl = 'https://play.games.dmm.com/game/monmusutd'

function Read-Config([string]$File) {
    $values = @{network=@{listen_port='8080';upstream_port='';upstream_insecure='true';debug='false'};browser=@{type='chrome';path='';args=''}}
    $section = ''; $seen = @{}
    foreach ($line in [IO.File]::ReadAllLines($File, [Text.Encoding]::UTF8)) {
        $s = $line.Trim()
        if (!$s -or $s.StartsWith('#') -or $s.StartsWith(';')) { continue }
        if ($s -match '^\[(network|browser)\]$') { $section=$Matches[1]; continue }
        $equal = $s.IndexOf('=')
        if (!$section -or $equal -lt 1) { throw 'config.ini 格式不正确，请检查配置。' }
        $key=$s.Substring(0,$equal).Trim(); $value=$s.Substring($equal+1).Trim()
        if (!$values[$section].ContainsKey($key) -or $seen.ContainsKey("$section.$key")) {
            throw 'config.ini 存在未知或重复字段，请检查配置。'
        }
        $values[$section][$key]=$value; $seen["$section.$key"]=$true
    }
    foreach ($key in @('listen_port','upstream_port')) {
        $value=$values.network[$key]
        if ($key -eq 'upstream_port' -and !$value) { continue }
        if ($value -notmatch '^\d{1,5}$' -or [int]$value -lt 1 -or [int]$value -gt 65535) {
            throw "config.ini 的 $key 必须是 1 至 65535 的端口。"
        }
    }
    if ($values.network.upstream_port -and [int]$values.network.upstream_port -eq [int]$values.network.listen_port) {
        throw '上游代理端口不能与汉化监听端口相同，请修改 config.ini。'
    }
    if ($values.browser.type -notin @('chrome','edge','custom')) {
        throw 'browser.type 只支持 chrome、edge 或 custom，请修改 config.ini。'
    }
    if ($values.network.debug -notin @('true','false')) {
        throw 'network.debug 只能是 true 或 false，请修改 config.ini。'
    }
    if ($values.network.upstream_insecure -notin @('true','false')) {
        throw 'network.upstream_insecure 只能是 true 或 false，请修改 config.ini。'
    }
    return $values
}

function Find-Browser($Config) {
    if ($Config.type -eq 'custom') {
        $path=$Config.path
        if ($path.StartsWith('"') -and $path.EndsWith('"')) { $path=$path.Substring(1,$path.Length-2) }
        if (!$path -or ![IO.Path]::IsPathRooted($path) -or !(Test-Path -LiteralPath $path -PathType Leaf)) {
            throw '自定义浏览器路径无效，请修改 config.ini 的 browser.path。'
        }
        return $path
    }
    $relative=if ($Config.type -eq 'chrome') {'Google\Chrome\Application\chrome.exe'} else {'Microsoft\Edge\Application\msedge.exe'}
    foreach ($folder in @($env:LOCALAPPDATA,$env:ProgramFiles,${env:ProgramFiles(x86)})) {
        if ($folder) {
            $path=Join-Path $folder $relative
            if (Test-Path -LiteralPath $path -PathType Leaf) { return $path }
        }
    }
    throw '未找到所选浏览器，请在 config.ini 中改为 edge 或填写 custom 路径。'
}

function Quote-Argument([string]$Value) {
    $result=New-Object Text.StringBuilder; [void]$result.Append('"'); $slashes=0
    foreach ($c in $Value.ToCharArray()) {
        if ($c -eq '\') { $slashes++; continue }
        if ($c -eq '"') { [void]$result.Append(('\' * ($slashes*2+1))); [void]$result.Append('"') }
        else { [void]$result.Append(('\' * $slashes)); [void]$result.Append($c) }
        $slashes=0
    }
    [void]$result.Append(('\' * ($slashes*2))); [void]$result.Append('"')
    return $result.ToString()
}

function New-BrowserCommand($Config, [string]$Executable) {
    $arguments=$Config.browser.args
    if (!$arguments) { $arguments='--proxy-server="http://127.0.0.1:'+[int]$Config.network.listen_port+'" --disable-quic' }
    # Arguments is a raw Windows command line, not an array containing one large argument.
    # No shell evaluates it; Chromium parses the user's original quotes itself.
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$Executable; $info.UseShellExecute=$false
    $info.Arguments=$arguments+' '+(Quote-Argument $GameUrl)
    return $info
}

function Get-ServiceArguments($Config) {
    # flow_detail=0 removes the per-request line; termlog_verbosity=error drops
    # mitmproxy's own info/debug chatter (WebSocket pings, normal connection
    # closes). The addon prints its own [MonmusuCN] status straight to stdout,
    # which mitmproxy's termlog filter does NOT suppress, and writes full
    # details + stack traces to logs/monmusu_cn.log with bounded rotation.
    # Errors are never hidden with >nul 2>&1.
    $verbosity = 'error'
    $detail = '0'
    if ($Config.network.debug -eq 'true') {
        $verbosity = 'debug'   # developer mode: full network logs again
        $detail = '3'
    }
    # Upstream accelerators such as beptard / ACGP terminate TLS with their own
    # self-signed certificate (and ACGP's key is below OpenSSL's default
    # security level).  With ssl_insecure=false mitmproxy rejects that hop and
    # the browser only sees "502 Bad Gateway" / "Certificate verify failed".
    # The switch below only relaxes verification of the LOCAL upstream proxy;
    # the browser still verifies the mod's CA.  Users on a plain HTTP proxy can
    # set upstream_insecure=false to keep strict verification.
    $insecure='false'
    if ($Config.network.upstream_port -and $Config.network.upstream_insecure -eq 'true') {
        $insecure='true'
    }
    $result=@('--listen-host','127.0.0.1','--listen-port',([int]$Config.network.listen_port).ToString(),
              '--set',"confdir=$(Join-Path $AppRoot 'data')",'--set',"flow_detail=$detail",
              '--set',"termlog_verbosity=$verbosity",
              '--set',"ssl_insecure=$insecure",'-s',(Join-Path $AppRoot 'resources\addon_monmusu_cn.py'))
    if ($Config.network.upstream_port) {
        $result+=@('--mode',('upstream:http://127.0.0.1:'+([int]$Config.network.upstream_port)))
    }
    return $result
}

function Test-CurrentUserRoot($Certificate) {
    $store=New-Object Security.Cryptography.X509Certificates.X509Store('Root','CurrentUser')
    try {
        $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
        return $store.Certificates.Find(
            [Security.Cryptography.X509Certificates.X509FindType]::FindByThumbprint,
            $Certificate.Thumbprint,$false).Count -gt 0
    } finally { $store.Close() }
}

function Install-CurrentUserRoot([string]$File) {
    # Windows controls whether its native import/trust mechanism prompts.
    # Do not invent a confirmation dialog or force consent.
    & "$env:SystemRoot\System32\certutil.exe" -user -addstore Root $File
    if ($LASTEXITCODE -ne 0) {
        throw 'Windows 未完成证书安装（可能已取消或被策略阻止），游戏尚未打开。请重新启动后完成系统授权。'
    }
}

function Ensure-CertificateTrust([string]$File) {
    if (!(Test-Path -LiteralPath $File -PathType Leaf)) { throw '未找到 mitmdump 生成的 CA 证书，请检查上方错误。' }
    $bytes=[IO.File]::ReadAllBytes($File)
    if ([Security.Cryptography.X509Certificates.X509Certificate2]::GetCertContentType($bytes) -ne
        [Security.Cryptography.X509Certificates.X509ContentType]::Cert) {
        throw 'CA 文件必须只包含公钥证书，不能包含私钥。'
    }
    $certificate=New-Object Security.Cryptography.X509Certificates.X509Certificate2(,$bytes)
    try {
        if ($certificate.HasPrivateKey) { throw '不能导入包含私钥的 CA 文件。' }
        if (Test-CurrentUserRoot $certificate) {
            Write-Host '当前用户已信任本地 CA，跳过安装。'
            return
        }
        Write-Host ('正在调用 Windows 原生证书安装机制，CA 指纹：'+$certificate.Thumbprint)
        Install-CurrentUserRoot $File
        if (!(Test-CurrentUserRoot $certificate)) {
            throw '当前用户根证书存储区中未找到相同指纹，游戏尚未打开。请重新启动后完成系统授权。'
        }
        Write-Host 'CA 指纹验证成功，继续打开游戏。'
    } finally { $certificate.Dispose() }
}

function Start-Translation {
    $service=$null
    try {
        $exe=Join-Path $AppRoot 'runtime\mitmdump.exe'
        if (!(Test-Path -LiteralPath $exe)) { throw '缺少内置运行环境，请重新解压完整文件。' }
        $config=Read-Config (Join-Path $AppRoot 'config.ini')
        $browser=Find-Browser $config.browser
        $port=[int]$config.network.listen_port
        $listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$port)
        try { $listener.Start() } catch { throw "端口 $port 被占用或无法使用，请修改 config.ini。" }
        finally { $listener.Stop() }
        $info=New-Object Diagnostics.ProcessStartInfo
        $info.FileName=$exe; $info.UseShellExecute=$false; $info.WorkingDirectory=$AppRoot
        # Inherit this console: closing the BAT console also sends console-close to mitmdump.
        $info.CreateNoWindow=$false
        $info.Arguments=((Get-ServiceArguments $config | ForEach-Object { Quote-Argument $_ }) -join ' ')
        if ($config.network.debug -eq 'true') {
            $info.EnvironmentVariables['MONMUSU_DEBUG']='1'
            Write-Host '开发者调试模式：显示完整网络日志，详见 logs\monmusu_cn.log。'
        } else {
            $info.EnvironmentVariables.Remove('MONMUSU_DEBUG') | Out-Null
        }
        if ($config.network.upstream_port) {
            $verify='严格校验'
            if ($config.network.upstream_insecure -eq 'true') { $verify='放宽（适配 beptard / ACGP 等会替换证书的加速器）' }
            Write-Host ('上游代理：127.0.0.1:'+[int]$config.network.upstream_port+'，上游证书校验：'+$verify)
        }
        Write-Host '魔物娘TD汉化：正在启动，首次运行可能需要片刻解压内置运行时。'
        $service=[Diagnostics.Process]::Start($info)
        $ready=$false; $deadline=[DateTime]::UtcNow.AddSeconds(30)
        while ([DateTime]::UtcNow -lt $deadline -and !$service.HasExited) {
            $client=New-Object Net.Sockets.TcpClient
            try { $client.Connect('127.0.0.1',$port); $ready=$true; break } catch { Start-Sleep -Milliseconds 200 }
            finally { $client.Dispose() }
        }
        if (!$ready) { throw '汉化服务启动失败，请检查上方错误和 config.ini。' }
        Ensure-CertificateTrust (Join-Path $AppRoot 'data\mitmproxy-ca-cert.cer')
        [void][Diagnostics.Process]::Start((New-BrowserCommand $config $browser))
        Write-Host "汉化服务运行中：127.0.0.1:$port"
        Write-Host '请保持此窗口打开。结束时建议先退出浏览器，再关闭此窗口。'
        $service.WaitForExit()
        if ($service.ExitCode -ne 0) { throw '汉化服务已停止，请检查上方错误。' }
    } catch {
        Write-Host ('错误：'+$_.Exception.Message) -ForegroundColor Red
        exit 1
    } finally {
        # Only our own service is cleaned up if startup fails. Never touch browser processes.
        if ($service -and !$service.HasExited) {
            # The official one-file runtime has a worker child. Target only our own PID tree.
            & "$env:SystemRoot\System32\taskkill.exe" /PID $service.Id /T /F >$null 2>&1
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') { Start-Translation }
