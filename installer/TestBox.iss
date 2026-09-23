#define AppName "TestBox GUI"
#ifndef AppVersion
#define AppVersion "1.0.17"
#endif
#define AppPublisher "TestBox"
#define AppExeName "TestBox-GUI.exe"

[Setup]
AppId={{3B8BE87A-BBE1-4DE9-9FB7-3E5EC6D9A3C4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\TestBox GUI
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\TestBox-GUI\{#AppExeName}
OutputDir=..\dist
OutputBaseFilename=TestBox-GUI-Install-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\testbox\assets\logo.ico
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[InstallDelete]
Type: filesandordirs; Name: "{app}\TestBox-GUI"
Type: files; Name: "{app}\TestBox-GUI-Updater.exe"
; Compatibility cleanup: versions built during the GUI Host transition
; may have installed this console companion next to the GUI bundle.
Type: files; Name: "{app}\TestBox-GUI-Host.exe"
Type: files; Name: "{app}\update-manifest.json"

[Registry]
Root: HKCU; Subkey: "Software\TestBox\GUI"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletevalue

[Files]
Source: "..\dist\windows\gui\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\TestBox GUI"; Filename: "{app}\TestBox-GUI\{#AppExeName}"; IconFilename: "{app}\TestBox-GUI\{#AppExeName}"
Name: "{autoprograms}\TestBox GUI 增量更新"; Filename: "{app}\TestBox-GUI-Updater.exe"
Name: "{autodesktop}\TestBox GUI"; Filename: "{app}\TestBox-GUI\{#AppExeName}"; IconFilename: "{app}\TestBox-GUI\{#AppExeName}"

[Run]
Filename: "{app}\TestBox-GUI\{#AppExeName}"; Description: "启动 TestBox GUI"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
