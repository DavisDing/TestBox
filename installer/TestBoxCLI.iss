#define AppName "TestBox CLI"
#ifndef AppVersion
#define AppVersion "1.0.15"
#endif
#define AppPublisher "TestBox"
#define AppExeName "TestBox.exe"

[Setup]
AppId={{A4D1265C-4D25-4935-ACFA-4053B154C2BB}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\TestBox CLI
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\TestBox\{#AppExeName}
OutputDir=..\dist
OutputBaseFilename=TestBox-CLI-Install-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\testbox\assets\logo.ico
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[InstallDelete]
Type: filesandordirs; Name: "{app}\TestBox"
Type: files; Name: "{app}\TestBox-CLI-Updater.exe"
Type: files; Name: "{app}\update-manifest.json"

[Registry]
Root: HKCU; Subkey: "Software\TestBox\CLI"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletevalue

[Files]
Source: "..\dist\windows\cli\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\TestBox CLI 增量更新"; Filename: "{app}\TestBox-CLI-Updater.exe"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
