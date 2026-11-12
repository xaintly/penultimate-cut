; Inno Setup script for Penultimate Cut.
;
; Build with (on Windows, after the PyInstaller build has already produced
; dist/PenultimateCut/):
;
;     ISCC.exe packaging/installer.iss
;
; Produces packaging/dist/PenultimateCutSetup-<version>.exe.
;
; #AppVersion is passed in from CI via `/DAppVersion=x.y.z` so the installer
; filename and "Programs and Features" entry track pyproject.toml's version
; without duplicating it here; a local manual build falls back to 0.0.0-dev.
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
; Fixed, generated once -- never change this, or Windows treats upgrades as
; a different, unrelated program instead of an in-place update.
AppId={{D79C6304-65EF-4389-AB74-4C8D9A8E997C}}
AppName=Penultimate Cut
AppVersion={#AppVersion}
AppPublisher=Penultimate Cut
; Installed for the current user (%LOCALAPPDATA%\Programs), so it never asks
; for admin rights -- and never shows Windows' "Unknown publisher" admin
; prompt. The app writes nothing outside the user's own folders anyway.
PrivilegesRequired=lowest
DefaultDirName={autopf}\PenultimateCut
DefaultGroupName=Penultimate Cut
UninstallDisplayIcon={app}\PenultimateCut.exe
SetupIconFile=..\penultimate_cut\resources\icon.ico
OutputDir=dist
OutputBaseFilename=PenultimateCutSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[InstallDelete]
; Installing over an older version only overwrites files, never removes
; them, so files a newer build no longer has (Qt pieces the spec now leaves
; out, ffmpeg DLLs renamed by a newer build) would stay behind. These two
; folders hold only bundled files, so clear them first.
Type: filesandordirs; Name: "{app}\PySide6"
Type: filesandordirs; Name: "{app}\vendor"

[Files]
; Everything PyInstaller produced -- the exe, the Python runtime, bundled
; packages, and vendor/ (ffmpeg.exe, ffprobe.exe and their DLLs,
; libmpv-2.dll) -- all as
; one flat tree, since packaging/penultimate_cut.spec's
; contents_directory="." keeps PyInstaller from nesting it under its own
; _internal/ subfolder.
Source: "..\dist\PenultimateCut\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Penultimate Cut"; Filename: "{app}\PenultimateCut.exe"
Name: "{group}\Uninstall Penultimate Cut"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Penultimate Cut"; Filename: "{app}\PenultimateCut.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Run]
Filename: "{app}\PenultimateCut.exe"; Description: "Launch Penultimate Cut"; Flags: nowait postinstall skipifsilent
