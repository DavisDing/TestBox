#define AppName "TestBox"
#ifndef AppVersion
#define AppVersion "1.0.1"
#endif
#define AppPublisher "TestBox"
#define AppExeName "TestBox-GUI.exe"

[Setup]
AppId={{B25D0B43-2C68-4ED7-8C2F-0D04BEF5F6B7}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\TestBox
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\TestBox-GUI\{#AppExeName}
OutputDir=..\dist
OutputBaseFilename=TestBox-Setup-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes

[InstallDelete]
Type: filesandordirs; Name: "{app}\TestBox"
Type: filesandordirs; Name: "{app}\TestBox-GUI"
Type: files; Name: "{app}\TestBox-Updater.exe"
Type: files; Name: "{app}\update-manifest.json"

[Files]
Source: "..\dist\windows\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\TestBox"; Filename: "{app}\TestBox-GUI\{#AppExeName}"
Name: "{autoprograms}\TestBox 增量更新"; Filename: "{app}\TestBox-Updater.exe"
Name: "{autodesktop}\TestBox"; Filename: "{app}\TestBox-GUI\{#AppExeName}"

[Run]
Filename: "{app}\TestBox-GUI\{#AppExeName}"; Description: "启动 TestBox"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
