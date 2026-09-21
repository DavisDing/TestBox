#define AppName "TestBox GUI Incremental Update"
#ifndef AppVersion
#define AppVersion "1.0.14"
#endif
#ifndef UpdatePackage
#define UpdatePackage "TestBox-GUI-update-v1.0.14.zip"
#endif

[Setup]
AppId={{BA7C5B11-10E8-44CC-A21D-C67D4ED801C0}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={tmp}\TestBoxGUIUpdate
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
Uninstallable=no
CreateUninstallRegKey=no
OutputDir=..\dist
OutputBaseFilename=TestBox-GUI-Setup-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\testbox\assets\logo.ico
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\windows\gui\TestBox-GUI-Updater.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion
Source: "..\dist\{#UpdatePackage}"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion

[Run]
Filename: "{tmp}\TestBox-GUI-Updater.exe"; Parameters: "--package ""{tmp}\{#UpdatePackage}"" --install-dir ""{code:GetInstallDir}"""; StatusMsg: "正在应用 TestBox GUI 增量更新..."; Flags: runhidden waituntilterminated

[Code]
var
  TestBoxInstallDir: String;

function GetInstallDir(Param: String): String;
begin
  Result := TestBoxInstallDir;
end;

function InitializeSetup(): Boolean;
begin
  TestBoxInstallDir := ExpandConstant('{localappdata}\Programs\TestBox GUI');
  RegQueryStringValue(HKCU, 'Software\TestBox\GUI', 'InstallDir', TestBoxInstallDir);
  if not DirExists(TestBoxInstallDir) then begin
    MsgBox('未检测到 TestBox GUI 安装目录。请先运行 TestBox-GUI-Install-v{#AppVersion}.exe 进行完整安装。', mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;
