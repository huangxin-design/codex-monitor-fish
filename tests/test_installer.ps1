# Offline adversarial tests. No network, account data, browser or real EXE is used.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:CodexMonitorInstallerTest = @{}
$installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'tooling/install_windows.ps1'
$suiteRoot = Join-Path $env:TEMP ('codex-monitor-installer-tests-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $suiteRoot
$suiteRoot = [IO.Path]::GetFullPath($suiteRoot)
$global:CodexMonitorInstallerTest.passed = 0
$global:CodexMonitorInstallerTest.fakeExe = [Text.Encoding]::ASCII.GetBytes('Synthetic executable; never execute this test fixture.')
$hasher = [Security.Cryptography.SHA256]::Create()
$global:CodexMonitorInstallerTest.digest = ([BitConverter]::ToString($hasher.ComputeHash($global:CodexMonitorInstallerTest.fakeExe))).Replace('-', '').ToLowerInvariant()
$hasher.Dispose()

function Assert-True($Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Assert-Throws([scriptblock]$Body, [string]$Pattern) {
    try { & $Body | Out-Null } catch {
        if ($_.Exception.Message -notmatch $Pattern) { throw "Unexpected error: $($_.Exception.Message)" }
        return
    }
    throw "Expected an error matching: $Pattern"
}
function Reset-Mocks {
    $global:CodexMonitorInstallerTest.httpCalls = [Collections.Generic.List[object]]::new()
    $global:CodexMonitorInstallerTest.processCalls = [Collections.Generic.List[object]]::new()
    $global:CodexMonitorInstallerTest.caseRoot = Join-Path $suiteRoot ('case-' + [Guid]::NewGuid().ToString('N'))
    $global:CodexMonitorInstallerTest.checksum = $global:CodexMonitorInstallerTest.digest + "  CodexMonitorFish-Windows.exe`n"
    $global:CodexMonitorInstallerTest.downloadBytes = $global:CodexMonitorInstallerTest.fakeExe
    $global:CodexMonitorInstallerTest.healthStatus = 200
    $global:CodexMonitorInstallerTest.health = @{ app = 'codex-monitor-fish-desktop'; data_dir = $global:CodexMonitorInstallerTest.caseRoot; pid = 4242; instance = 'SyntheticInstance123456789'; version = '0.2.0' }
    $global:CodexMonitorInstallerTest.runningPath = [IO.Path]::GetFullPath((Join-Path $global:CodexMonitorInstallerTest.caseRoot 'app/v0.2.0/CodexMonitorFish-Windows.exe'))
    $global:CodexMonitorInstallerTest.release = [pscustomobject]@{
        tag_name = 'v0.2.0'; draft = $false; prerelease = $false
        assets = @('CodexMonitorFish-Windows.exe', 'CodexMonitorFish-Windows.exe.sha256') | ForEach-Object {
            [pscustomobject]@{ name = $_; browser_download_url = ('https://github.com/huangxin-design/codex-monitor-fish/releases/download/v0.2.0/' + $_) }
        }
    }
}
function Invoke-RestMethod {
    param($Uri, $Headers, $TimeoutSec)
    $global:CodexMonitorInstallerTest.httpCalls.Add([pscustomobject]@{ uri = $Uri; kind = 'metadata' })
    if ($Uri -notmatch '\Ahttps://api\.github\.com/repos/huangxin-design/codex-monitor-fish/releases/(latest|tags/v0\.2\.0)\z') { throw 'Unexpected metadata request.' }
    return $global:CodexMonitorInstallerTest.release
}
function Invoke-WebRequest {
    param($Uri, $Headers, [switch]$UseBasicParsing, [int]$MaximumRedirection = -1, $TimeoutSec, [string]$OutFile)
    $global:CodexMonitorInstallerTest.httpCalls.Add([pscustomobject]@{ uri = $Uri; kind = 'download'; redirects = $MaximumRedirection })
    if ($OutFile) {
        if ($Uri -ceq 'https://github.com/huangxin-design/codex-monitor-fish/releases/download/v0.2.0/CodexMonitorFish-Windows.exe.sha256') {
            [IO.File]::WriteAllText($OutFile, $global:CodexMonitorInstallerTest.checksum, [Text.Encoding]::ASCII)
        } elseif ($Uri -ceq 'https://github.com/huangxin-design/codex-monitor-fish/releases/download/v0.2.0/CodexMonitorFish-Windows.exe') {
            [IO.File]::WriteAllBytes($OutFile, $global:CodexMonitorInstallerTest.downloadBytes)
        } else { throw 'Unexpected download request.' }
        return
    }
    if ($Uri -cne 'http://127.0.0.1:18776/api/health') { throw 'Unexpected health request.' }
    return [pscustomobject]@{ StatusCode = $global:CodexMonitorInstallerTest.healthStatus; Content = ($global:CodexMonitorInstallerTest.health | ConvertTo-Json -Compress) }
}
function Get-Process {
    [CmdletBinding()]
    param($Id)
    return [pscustomobject]@{ Id = 4242; Path = $global:CodexMonitorInstallerTest.runningPath }
}
function Start-Process {
    param($FilePath, $ArgumentList, $WindowStyle, [switch]$PassThru)
    $global:CodexMonitorInstallerTest.processCalls.Add([pscustomobject]@{ file = $FilePath; arguments = $ArgumentList; style = $WindowStyle })
    if ($PassThru) {
        Assert-True ($WindowStyle -eq 'Hidden') 'The background application must start hidden.'
        Assert-True ($FilePath -eq $global:CodexMonitorInstallerTest.runningPath) 'The executable path was not resolved physically.'
        Assert-True ($ArgumentList[0] -eq '--data-dir' -and $ArgumentList[1] -eq ('"' + $global:CodexMonitorInstallerTest.caseRoot + '"') -and $ArgumentList[2] -eq '--no-browser') 'The launcher arguments changed.'
        Write-State
        return [pscustomobject]@{ HasExited = $false; ExitCode = 0 }
    }
    Assert-True ($FilePath -eq 'http://127.0.0.1:18776/') 'A browser was opened to an unexpected URL.'
}
function Write-State([string]$Url = 'http://127.0.0.1:18776/') {
    $null = New-Item -ItemType Directory -Path $global:CodexMonitorInstallerTest.caseRoot -Force
    @{ url = $Url; pid = 4242; instance = 'SyntheticInstance123456789'; version = '0.2.0' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $global:CodexMonitorInstallerTest.caseRoot 'server.json') -Encoding UTF8
}
function Install-Fixture([switch]$Launch, [switch]$NoBrowser) {
    return (& $installer -InstallDir $global:CodexMonitorInstallerTest.caseRoot -NoLaunch:(-not $Launch) -NoBrowser:$NoBrowser | ConvertFrom-Json)
}
function Run-Test([string]$Name, [scriptblock]$Body) {
    Reset-Mocks
    & $Body
    $global:CodexMonitorInstallerTest.passed++
    Write-Host "PASS $Name"
}

# Load the health validator without executing the installer's top-level actions.
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
Assert-True ($parseErrors.Count -eq 0) 'Installer has PowerShell syntax errors.'
foreach ($definition in $ast.FindAll({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    . ([scriptblock]::Create($definition.Extent.Text))
}

try {
    Run-Test 'clean install pins publisher and verifies checksum before launch' {
        $result = Install-Fixture
        Assert-True ($result.installed -and $result.version -eq 'v0.2.0' -and $null -eq $result.url) 'Unexpected install result.'
        Assert-True ((Get-FileHash -LiteralPath $result.executable).Hash -ieq $global:CodexMonitorInstallerTest.digest) 'Installed bytes are incorrect.'
        Assert-True ($global:CodexMonitorInstallerTest.processCalls.Count -eq 0) 'NoLaunch started a process.'
        Assert-True (@(Get-ChildItem -LiteralPath (Split-Path $result.executable) -Force).Count -eq 1) 'Staging files were left behind.'
    }
    Run-Test 'repeat install preserves preferences and unrelated files' {
        $first = Install-Fixture
        $sentinel = Join-Path $global:CodexMonitorInstallerTest.caseRoot 'preferences.json'
        [IO.File]::WriteAllText($sentinel, 'DO NOT REPLACE')
        $second = Install-Fixture
        Assert-True (-not $second.installed) 'Repeat install should reuse verified bytes.'
        Assert-True ([IO.File]::ReadAllText($sentinel) -eq 'DO NOT REPLACE') 'Preferences were changed.'
        Assert-True (@($global:CodexMonitorInstallerTest.httpCalls | Where-Object { $_.uri -match '\.exe$' }).Count -eq 1) 'Repeated install downloaded EXE again.'
    }
    Run-Test 'pinned tag uses exact release metadata endpoint' {
        $null = & $installer -InstallDir $global:CodexMonitorInstallerTest.caseRoot -ReleaseTag v0.2.0 -NoLaunch
        Assert-True ($global:CodexMonitorInstallerTest.httpCalls[0].uri -match '/tags/v0\.2\.0$') 'Pinned tag was ignored.'
    }
    Run-Test 'installation through a real directory junction uses physical runtime identity' {
        $null = New-Item -ItemType Directory -Path $global:CodexMonitorInstallerTest.caseRoot
        $alias = Join-Path $suiteRoot ('junction-' + [Guid]::NewGuid().ToString('N'))
        $null = New-Item -ItemType Junction -Path $alias -Target $global:CodexMonitorInstallerTest.caseRoot
        try {
            $result = & $installer -InstallDir $alias -NoBrowser | ConvertFrom-Json
            Assert-True ($result.url -eq 'http://127.0.0.1:18776/') 'A relocated directory failed its health identity check.'
            Assert-True ($result.executable -eq $global:CodexMonitorInstallerTest.runningPath) 'A junction remained in the executable path.'
            $repeat = & $installer -InstallDir $alias -NoBrowser | ConvertFrom-Json
            Assert-True (-not $repeat.installed -and $global:CodexMonitorInstallerTest.processCalls.Count -eq 1) 'Reinstall through the junction did not reuse its process.'
        } finally {
            # Directory.Delete without recursion removes this link, never its target.
            [IO.Directory]::Delete($alias)
        }
    }
    Run-Test 'an app child junction also resolves the executable identity' {
        $null = New-Item -ItemType Directory -Path $global:CodexMonitorInstallerTest.caseRoot
        $physicalApp = Join-Path $global:CodexMonitorInstallerTest.caseRoot 'relocated-code'
        $null = New-Item -ItemType Directory -Path $physicalApp
        $alias = Join-Path $global:CodexMonitorInstallerTest.caseRoot 'app'
        $null = New-Item -ItemType Junction -Path $alias -Target $physicalApp
        $global:CodexMonitorInstallerTest.runningPath = [IO.Path]::GetFullPath((Join-Path $physicalApp 'v0.2.0/CodexMonitorFish-Windows.exe'))
        try {
            $result = Install-Fixture -Launch -NoBrowser
            Assert-True ($result.url -eq 'http://127.0.0.1:18776/' -and $result.executable -eq $global:CodexMonitorInstallerTest.runningPath) 'A relocated app folder failed its process identity check.'
        } finally { [IO.Directory]::Delete($alias) }
    }
    Run-Test '8.3 aliases use physical runtime identity when available' {
        $null = New-Item -ItemType Directory -Path $global:CodexMonitorInstallerTest.caseRoot
        if (-not ('CodexMonitorInstallerTests.ShortPath' -as [type])) {
            Add-Type -Namespace CodexMonitorInstallerTests -Name ShortPath -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
public static extern uint GetShortPathNameW(string path, System.Text.StringBuilder result, uint length);
'@
        }
        $shortBuffer = [Text.StringBuilder]::new(32768)
        $shortLength = [CodexMonitorInstallerTests.ShortPath]::GetShortPathNameW($global:CodexMonitorInstallerTest.caseRoot, $shortBuffer, $shortBuffer.Capacity)
        Assert-True ($shortLength -gt 0 -and $shortLength -lt $shortBuffer.Capacity) 'Windows could not inspect the fixture short path.'
        $shortPath = $shortBuffer.ToString()
        if ($shortPath -eq $global:CodexMonitorInstallerTest.caseRoot) {
            Write-Host '8.3 aliases are disabled for this fixture; alias branch skipped.'
            return
        }
        $result = & $installer -InstallDir $shortPath -NoBrowser | ConvertFrom-Json
        Assert-True ($result.url -eq 'http://127.0.0.1:18776/' -and $result.executable -eq $global:CodexMonitorInstallerTest.runningPath) 'A short path failed its runtime identity check.'
    }
    Run-Test 'tampered download never becomes installed or executable' {
        $global:CodexMonitorInstallerTest.downloadBytes = [Text.Encoding]::ASCII.GetBytes('TAMPERED')
        Assert-Throws { Install-Fixture } 'failed SHA256'
        Assert-True (-not (Test-Path -LiteralPath $global:CodexMonitorInstallerTest.runningPath)) 'Tampered EXE was installed.'
        Assert-True ($global:CodexMonitorInstallerTest.processCalls.Count -eq 0) 'Tampered EXE was launched.'
        Assert-True (@(Get-ChildItem -LiteralPath (Split-Path $global:CodexMonitorInstallerTest.runningPath) -Force).Count -eq 0) 'Failed download left staging files.'
    }
    Run-Test 'checksum must name the exact executable' {
        $global:CodexMonitorInstallerTest.checksum = $global:CodexMonitorInstallerTest.digest + '  evil.exe'
        Assert-Throws { Install-Fixture } 'Invalid SHA256'
    }
    Run-Test 'existing unverified executable is preserved without launching' {
        $null = Install-Fixture
        [IO.File]::WriteAllText($global:CodexMonitorInstallerTest.runningPath, 'UNEXPECTED EXISTING CONTENT')
        Assert-Throws { Install-Fixture } 'existing executable does not match'
        Assert-True ([IO.File]::ReadAllText($global:CodexMonitorInstallerTest.runningPath) -eq 'UNEXPECTED EXISTING CONTENT') 'Existing file was overwritten.'
        Assert-True ($global:CodexMonitorInstallerTest.processCalls.Count -eq 0) 'Unverified existing file was launched.'
    }
    Run-Test 'asset URLs cannot change publisher or download path' {
        foreach ($badUrl in @('https://evil.example/monitor.exe', 'https://github.com/another/repository/releases/download/v0.2.0/CodexMonitorFish-Windows.exe', 'https://github.com/huangxin-design/codex-monitor-fish/releases/download/v0.2.0/../evil.exe')) {
            $global:CodexMonitorInstallerTest.release.assets[0].browser_download_url = $badUrl
            Assert-Throws { Install-Fixture } 'unexpected release asset URL'
        }
        Assert-True (-not (Test-Path -LiteralPath $global:CodexMonitorInstallerTest.caseRoot)) 'Invalid metadata created installation files.'
    }
    Run-Test 'duplicate assets are rejected' {
        $global:CodexMonitorInstallerTest.release.assets += $global:CodexMonitorInstallerTest.release.assets[0]
        Assert-Throws { Install-Fixture } 'unexpected release asset URL'
    }
    Run-Test 'release traversal and drafts cannot select installation folders' {
        Assert-Throws { & $installer -InstallDir $global:CodexMonitorInstallerTest.caseRoot -ReleaseTag '../bad' -NoLaunch } 'stable version'
        Assert-True ($global:CodexMonitorInstallerTest.httpCalls.Count -eq 0) 'Invalid tag reached the network.'
        $global:CodexMonitorInstallerTest.release.tag_name = 'v0.2.0/../../bad'
        Assert-Throws { Install-Fixture } 'stable published release'
        $global:CodexMonitorInstallerTest.release.tag_name = 'v0.2.0'
        $global:CodexMonitorInstallerTest.release.draft = $true
        Assert-Throws { Install-Fixture } 'stable published release'
    }
    Run-Test 'malformed local state never sends a health request' {
        foreach ($badUrl in @('https://127.0.0.1:18776/', 'http://localhost:18776/', 'http://127.0.0.1:18776.evil.example/', 'http://127.0.0.1:18776@evil.example/', 'http://127.0.0.1:18776/?redirect=https://evil.example', 'http://127.0.0.1:18776/#anything', 'http://127.0.0.1:18776/other', 'http://127.0.0.1:0/', 'http://127.0.0.1:65536/', 'file:///C:/private.txt')) {
            Write-State $badUrl
            Assert-True ($null -eq (Get-MonitorState $global:CodexMonitorInstallerTest.caseRoot)) 'Malformed URL was accepted.'
        }
        Assert-True ($global:CodexMonitorInstallerTest.httpCalls.Count -eq 0) 'Malformed state caused a network request.'
    }
    Run-Test 'health must match app, directory, numeric pid, instance and version' {
        Write-State
        foreach ($key in @('app', 'data_dir', 'pid', 'instance', 'version')) {
            $original = $global:CodexMonitorInstallerTest.health[$key]
            $global:CodexMonitorInstallerTest.health[$key] = 'wrong'
            Assert-True ($null -eq (Get-MonitorState $global:CodexMonitorInstallerTest.caseRoot)) "Mismatched health $key was accepted."
            $global:CodexMonitorInstallerTest.health[$key] = $original
        }
        $global:CodexMonitorInstallerTest.health.pid = '4242'
        Assert-True ($null -eq (Get-MonitorState $global:CodexMonitorInstallerTest.caseRoot)) 'A string PID was accepted.'
    }
    Run-Test 'health refuses redirects and only accepts status 200' {
        Write-State
        $global:CodexMonitorInstallerTest.healthStatus = 302
        Assert-True ($null -eq (Get-MonitorState $global:CodexMonitorInstallerTest.caseRoot)) 'Health redirect was accepted.'
        Assert-True ($global:CodexMonitorInstallerTest.httpCalls[0].redirects -eq 0) 'Health redirects were enabled.'
        $global:CodexMonitorInstallerTest.healthStatus = 200
        Assert-True ($null -ne (Get-MonitorState $global:CodexMonitorInstallerTest.caseRoot)) 'Valid health was rejected.'
    }
    Run-Test 'install starts hidden and opens only the validated browser URL' {
        $result = Install-Fixture -Launch
        Assert-True ($result.url -eq 'http://127.0.0.1:18776/') 'Ready URL was not returned.'
        Assert-True ($global:CodexMonitorInstallerTest.processCalls.Count -eq 2) 'Expected one EXE and one browser launch.'
    }
    Run-Test 'NoBrowser still starts and returns the ready URL' {
        $result = Install-Fixture -Launch -NoBrowser
        Assert-True ($result.url -eq 'http://127.0.0.1:18776/' -and $global:CodexMonitorInstallerTest.processCalls.Count -eq 1) 'NoBrowser did not suppress browser launch.'
    }
    Run-Test 'active matching version is reused without another EXE process' {
        $null = Install-Fixture
        Write-State
        $result = Install-Fixture -Launch -NoBrowser
        Assert-True ($result.url -eq 'http://127.0.0.1:18776/' -and $global:CodexMonitorInstallerTest.processCalls.Count -eq 0) 'Matching active instance was not reused.'
    }
    Run-Test 'another running version is not terminated or reused silently' {
        $null = Install-Fixture
        Write-State
        $global:CodexMonitorInstallerTest.runningPath = Join-Path $global:CodexMonitorInstallerTest.caseRoot 'app/v0.1.0/CodexMonitorFish-Windows.exe'
        Assert-Throws { Install-Fixture -Launch } 'Another version is running'
        Assert-True ($global:CodexMonitorInstallerTest.processCalls.Count -eq 0) 'Cross-version state launched another process.'
    }
    Write-Host "$($global:CodexMonitorInstallerTest.passed) installer tests passed. All external operations were mocked."
} finally {
    $resolvedRoot = [IO.Path]::GetFullPath($suiteRoot)
    $tempPrefix = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if ($resolvedRoot.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path $resolvedRoot -Leaf) -match '\Acodex-monitor-installer-tests-[a-f0-9]{32}\z') {
        Remove-Item -LiteralPath $resolvedRoot -Recurse -Force
    } else { throw 'Refusing to remove a test directory outside the expected temporary root.' }
    Remove-Variable -Scope Global -Name CodexMonitorInstallerTest
}
