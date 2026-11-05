<#
.SYNOPSIS
    Packs the PyInstaller build into an MSIX for the Microsoft Store.

.DESCRIPTION
    Run from anywhere, after `pyinstaller packaging/penultimate_cut.spec`
    has produced dist/PenultimateCut/:

        powershell -ExecutionPolicy Bypass -File scripts/build_msix.ps1

    Produces packaging/dist/PenultimateCut-<version>.msix, unsigned: the
    Store signs what's uploaded to it. Also leaves the unpacked package in
    packaging/build/msix/, which Windows can install as it is for testing
    (Developer Mode on, then
    `Add-AppxPackage -Register packaging/build/msix/AppxManifest.xml`).

    makeappx and makepri come from Microsoft's Windows SDK build tools
    NuGet package (about 20 MB, cached in packaging/build/), not from an
    installed Windows SDK, so this works the same here and in CI.
#>

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$buildDir = Join-Path $repoRoot "packaging\build"
$stage = Join-Path $buildDir "msix"
$outDir = Join-Path $repoRoot "packaging\dist"
$appDir = Join-Path $repoRoot "dist\PenultimateCut"

if (-not (Test-Path (Join-Path $appDir "PenultimateCut.exe"))) {
    throw "No PyInstaller build in $appDir -- run 'pyinstaller packaging/penultimate_cut.spec --noconfirm' first."
}

# --- the tools -----------------------------------------------------------------
$toolsVersion = "10.0.28000.2705"
$toolsDir = Join-Path $buildDir "sdk-buildtools-$toolsVersion"
if (-not (Test-Path $toolsDir)) {
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
    $zip = "$toolsDir.zip"
    Invoke-WebRequest "https://www.nuget.org/api/v2/package/Microsoft.Windows.SDK.BuildTools/$toolsVersion" -OutFile $zip
    Expand-Archive $zip $toolsDir
    Remove-Item $zip
}
$bin = Get-ChildItem (Join-Path $toolsDir "bin") -Directory | Select-Object -First 1
$makeappx = Join-Path $bin.FullName "x64\makeappx.exe"
$makepri = Join-Path $bin.FullName "x64\makepri.exe"

# --- the version: "0.96" -> "0.96.0.0" -------------------------------------------
$pyproject = Get-Content (Join-Path $repoRoot "pyproject.toml") -Raw
if ($pyproject -notmatch '(?m)^version\s*=\s*"([0-9.]+)"') {
    throw "Couldn't find a plain numeric version in pyproject.toml"
}
$version = $Matches[1]
$parts = @($version.Split(".")) + @("0", "0", "0")
$msixVersion = ($parts[0..2] -join ".") + ".0"

# --- the package's contents ------------------------------------------------------
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item -Recurse (Join-Path $appDir "*") $stage
$manifest = (Get-Content (Join-Path $repoRoot "packaging\msix\AppxManifest.xml") -Raw).Replace("{Version}", $msixVersion)
[IO.File]::WriteAllText((Join-Path $stage "AppxManifest.xml"), $manifest)
python (Join-Path $repoRoot "scripts\make_store_assets.py") (Join-Path $stage "Assets")
if ($LASTEXITCODE -ne 0) { throw "make_store_assets.py failed" }

# resources.pri: lists the icons' sizes, so Windows can pick between them.
$priConfig = Join-Path $buildDir "priconfig.xml"
& $makepri createconfig /cf $priConfig /dq en-US /pv 10.0.0 /o | Out-Null
if ($LASTEXITCODE -ne 0) { throw "makepri createconfig failed" }
# One package, not a bundle: keep every size in the one resources.pri
# rather than splitting them out by scale and language.
$config = (Get-Content $priConfig -Raw) -replace '(?s)<packaging>.*?</packaging>', ''
[IO.File]::WriteAllText($priConfig, $config)
& $makepri new /pr $stage /cf $priConfig /mn (Join-Path $stage "AppxManifest.xml") /of (Join-Path $stage "resources.pri") /o | Out-Null
if ($LASTEXITCODE -ne 0) { throw "makepri new failed" }

# --- pack ---------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$msix = Join-Path $outDir "PenultimateCut-$version.msix"
& $makeappx pack /d $stage /p $msix /o | Out-Null
if ($LASTEXITCODE -ne 0) { throw "makeappx pack failed (run its command by hand to see why)" }
Write-Host "Wrote $msix (package version $msixVersion)"
