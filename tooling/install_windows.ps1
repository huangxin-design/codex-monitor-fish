# Requires Windows PowerShell 5.1 or PowerShell 7 on Windows.
[CmdletBinding()]
param(
    [string]$ReleaseTag,
    [string]$InstallDir,
    [switch]$NoBrowser,
    [switch]$NoLaunch
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repository = 'huangxin-design/codex-monitor-fish'
$assetName = 'CodexMonitorFish-Windows.exe'
$headers = @{ 'User-Agent' = 'CodexMonitorFish-Installer'; 'Accept' = 'application/vnd.github+json' }

function Resolve-PhysicalDirectory([string]$Directory) {
    # Resolve junctions and 8.3 aliases the same way as Python's Path.resolve().
    if (-not ('CodexMonitorFishInstaller.NativePath' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
namespace CodexMonitorFishInstaller {
    public static class NativePath {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string path, uint access, uint share,
            IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle,
            StringBuilder path, uint length, uint flags);
        public static string ResolveDirectory(string path) {
            using (SafeFileHandle handle = CreateFileW(path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
                if (handle.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
                StringBuilder result = new StringBuilder(512);
                uint length = GetFinalPathNameByHandleW(handle, result, (uint)result.Capacity, 0);
                if (length == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
                if (length >= result.Capacity) {
                    result = new StringBuilder(checked((int)length + 1));
                    length = GetFinalPathNameByHandleW(handle, result, (uint)result.Capacity, 0);
                    if (length == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
                    if (length >= result.Capacity) throw new InvalidOperationException("Installation path changed while resolving it.");
                }
                string resolved = result.ToString();
                if (resolved.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase)) return @"\\" + resolved.Substring(8);
                if (resolved.StartsWith(@"\\?\", StringComparison.Ordinal)) return resolved.Substring(4);
                return resolved;
            }
        }
    }
}
'@
    }
    return [CodexMonitorFishInstaller.NativePath]::ResolveDirectory($Directory)
}

function Get-MonitorState([string]$Directory) {
    # server.json is local input, not permission to visit an arbitrary address.
    try {
        $statePath = Join-Path $Directory 'server.json'
        if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { return $null }
        if ((Get-Item -LiteralPath $statePath).Length -gt 16384) { return $null }
        $saved = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($saved.url -isnot [string] -or $saved.url -cnotmatch '\Ahttp://127\.0\.0\.1:([1-9][0-9]{0,4})/?\z') { return $null }
        if ([int]$Matches[1] -gt 65535) { return $null }
        if (($saved.pid -isnot [long] -and $saved.pid -isnot [int]) -or $saved.pid -le 0) { return $null }
        if ($saved.instance -isnot [string] -or $saved.instance -cnotmatch '\A[A-Za-z0-9_-]{16,128}\z') { return $null }
        if ($saved.version -isnot [string] -or $saved.version -cnotmatch '\A[0-9]+\.[0-9]+\.[0-9]+\z') { return $null }
        $response = Invoke-WebRequest -Uri ($saved.url.TrimEnd('/') + '/api/health') -UseBasicParsing -MaximumRedirection 0 -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $null }
        $health = $response.Content | ConvertFrom-Json
        if ($health.pid -isnot [long] -and $health.pid -isnot [int]) { return $null }
        if ($health.app -cne 'codex-monitor-fish-desktop' -or $health.pid -ne $saved.pid -or $health.instance -cne $saved.instance -or $health.version -cne $saved.version) { return $null }
        if ($health.data_dir -isnot [string] -or -not [StringComparer]::OrdinalIgnoreCase.Equals($health.data_dir, $Directory)) { return $null }
        return $saved
    } catch { return $null }
}

function Assert-RunningExecutable($State, [string]$Executable, [string]$Version) {
    $running = Get-Process -Id $State.pid -ErrorAction Stop
    if ($State.version -cne $Version.TrimStart('v') -or -not [StringComparer]::OrdinalIgnoreCase.Equals($running.Path, $Executable)) {
        throw 'Another version is running. Exit Codex Monitor Fish from its web page, then run this installer again.'
    }
}

if ($env:OS -ne 'Windows_NT') { throw 'This installer supports Windows only.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Codex Monitor Fish requires 64-bit Windows; this computer is running 32-bit Windows.' }
if ($ReleaseTag -and $ReleaseTag -cnotmatch '\Av[0-9]+\.[0-9]+\.[0-9]+\z') { throw 'ReleaseTag must be a stable version such as v0.2.0.' }
if (-not $InstallDir) {
    if (-not $env:LOCALAPPDATA) { throw 'LOCALAPPDATA is unavailable; specify InstallDir.' }
    $InstallDir = Join-Path $env:LOCALAPPDATA 'CodexMonitorFish'
}
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
if ($InstallDir -eq [IO.Path]::GetPathRoot($InstallDir)) { throw 'InstallDir must be an application folder, not a drive root.' }
$InstallDir = $InstallDir.TrimEnd('\', '/')

# Use a fixed publisher; release metadata cannot redirect us to another repository.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$releaseEndpoint = if ($ReleaseTag) { 'tags/' + $ReleaseTag } else { 'latest' }
$release = Invoke-RestMethod -Uri ("https://api.github.com/repos/$repository/releases/$releaseEndpoint") -Headers $headers -TimeoutSec 30
if ($release.tag_name -isnot [string] -or $release.tag_name -cnotmatch '\Av[0-9]+\.[0-9]+\.[0-9]+\z' -or $release.draft -ne $false -or $release.prerelease -ne $false) {
    throw 'GitHub did not return a stable published release.'
}
if ($ReleaseTag -and $release.tag_name -cne $ReleaseTag) { throw 'GitHub returned a different release tag.' }
$version = $release.tag_name
$downloadBase = "https://github.com/$repository/releases/download/$version/"
foreach ($name in @($assetName, "$assetName.sha256")) {
    $assets = @($release.assets | Where-Object { $_.name -ceq $name })
    if ($assets.Count -ne 1 -or $assets[0].browser_download_url -cne ($downloadBase + $name)) {
        throw "Missing or unexpected release asset URL: $name"
    }
}

# Keep runtime identity physical: a relocated LocalAppData or app junction is valid.
$null = [IO.Directory]::CreateDirectory($InstallDir)
$InstallDir = Resolve-PhysicalDirectory $InstallDir
if ($InstallDir -eq [IO.Path]::GetPathRoot($InstallDir)) { throw 'InstallDir must resolve to an application folder, not a drive root.' }
$appDirectory = Join-Path $InstallDir 'app'
$null = [IO.Directory]::CreateDirectory($appDirectory)
$appDirectory = Resolve-PhysicalDirectory $appDirectory
$versionDir = Join-Path $appDirectory $version
$null = [IO.Directory]::CreateDirectory($versionDir)
$versionDir = Resolve-PhysicalDirectory $versionDir
$executable = Join-Path $versionDir $assetName
$stageId = [Guid]::NewGuid().ToString('N')
$checksumFile = Join-Path $versionDir ('.download-' + $stageId + '.sha256')
$stagedExe = Join-Path $versionDir ('.download-' + $stageId + '.exe')
$installed = $false
try {
    Invoke-WebRequest -Uri ($downloadBase + "$assetName.sha256") -Headers $headers -UseBasicParsing -OutFile $checksumFile -TimeoutSec 30 | Out-Null
    if ((Get-Item -LiteralPath $checksumFile).Length -gt 256) { throw 'Invalid SHA256 checksum file.' }
    $checksumText = [IO.File]::ReadAllText($checksumFile)
    if ($checksumText -cnotmatch '\A([A-Fa-f0-9]{64})  CodexMonitorFish-Windows\.exe(?:\r?\n)?\z') { throw 'Invalid SHA256 checksum file.' }
    $expectedHash = $Matches[1]
    if (Test-Path -LiteralPath $executable) {
        if (-not (Test-Path -LiteralPath $executable -PathType Leaf) -or (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash -ine $expectedHash) {
            throw 'The existing executable does not match the published SHA256. It was not overwritten; review it before retrying.'
        }
    } else {
        Invoke-WebRequest -Uri ($downloadBase + $assetName) -Headers $headers -UseBasicParsing -OutFile $stagedExe -TimeoutSec 180 | Out-Null
        if ((Get-FileHash -LiteralPath $stagedExe -Algorithm SHA256).Hash -ine $expectedHash) { throw 'Downloaded executable failed SHA256 verification; nothing was launched.' }
        # No Force: never replace a concurrently installed/running executable.
        Move-Item -LiteralPath $stagedExe -Destination $executable
        $installed = $true
    }
} finally {
    foreach ($stage in @($checksumFile, $stagedExe)) {
        if (Test-Path -LiteralPath $stage -PathType Leaf) { Remove-Item -LiteralPath $stage -Force }
    }
}

$url = $null
if (-not $NoLaunch) {
    $active = Get-MonitorState $InstallDir
    if ($null -ne $active) {
        Assert-RunningExecutable $active $executable $version
    } else {
        $arguments = @('--data-dir', ('"' + $InstallDir + '"'), '--no-browser')
        $process = Start-Process -FilePath $executable -ArgumentList $arguments -WindowStyle Hidden -PassThru
        $timer = [Diagnostics.Stopwatch]::StartNew()
        while ($timer.Elapsed.TotalSeconds -lt 30) {
            $active = Get-MonitorState $InstallDir
            if ($null -ne $active) { break }
            if ($process.HasExited -and $process.ExitCode -ne 0) { throw 'The monitor could not start. Reopen the installed application to view its error.' }
            Start-Sleep -Milliseconds 300
        }
        if ($null -eq $active) { throw 'The monitor did not become ready within 30 seconds. Reopen the installed application to retry.' }
        Assert-RunningExecutable $active $executable $version
    }
    $url = $active.url
    if (-not $NoBrowser) { Start-Process -FilePath $url | Out-Null }
}
[ordered]@{ installed = $installed; version = $version; executable = $executable; url = $url } | ConvertTo-Json -Compress
