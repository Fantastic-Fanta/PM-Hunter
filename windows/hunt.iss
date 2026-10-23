; PoopihuntSetup.exe: installs dist\Hunt (Poopihunt: Hunt.exe, the samples for --diagnose) for this Windows user (no admin), with a
; Start menu entry and a desktop icon. Hunt's window runs it with /VERYSILENT
; to update itself; it then starts the new Hunt. What Hunt keeps (config,
; finds, the website key) is in %LOCALAPPDATA%\Hunt and stays.
;   iscc "/DName=23 Oct 18:02" windows\hunt.iss

#ifndef Name
  #define Name "test"
#endif

[Setup]
AppId={{B67D989D-E853-40C9-AAC3-95E19B1D7B33}
AppName=Poopihunt
AppVersion={#Name}
AppVerName=Poopihunt ({#Name})
AppPublisher=POOPIHUNT
AppPublisherURL=https://poopihunt.xyz
DefaultDirName={localappdata}\Programs\Hunt
PrivilegesRequired=lowest
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir=..\dist
OutputBaseFilename=PoopihuntSetup
SetupIconFile=hunt.ico
UninstallDisplayIcon={app}\Hunt.exe
UninstallDisplayName=Poopihunt
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: desktopicon; Description: "Put Poopihunt on the desktop"

[InstallDelete]
; the last build's files, so nothing old is left next to the new ones (and the
; Python hunter's, from before Hunt was all Rust)
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\hunt-engine.exe"
; its shortcuts from when it was called Hunt
Type: files; Name: "{autoprograms}\Hunt.lnk"
Type: files; Name: "{autodesktop}\Hunt.lnk"

[Files]
Source: "..\dist\Hunt\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Poopihunt"; Filename: "{app}\Hunt.exe"
Name: "{autodesktop}\Poopihunt"; Filename: "{app}\Hunt.exe"; Tasks: desktopicon

[Run]
; ticked after a normal install, and always after an update (/VERYSILENT)
Filename: "{app}\Hunt.exe"; Description: "Start Poopihunt"; Flags: nowait postinstall
