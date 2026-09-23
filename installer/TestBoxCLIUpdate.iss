#define AppName "TestBox CLI Incremental Update"
#ifndef AppVersion
#define AppVersion "1.0.16"
#endif
#ifndef UpdatePackage
#define UpdatePackage "TestBox-CLI-update-v1.0.16.zip"
#endif

[Setup]
AppId={{5121B2FA-08BB-4670-9CF1-9A416DDA850F}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={tmp}\TestBoxCLIUpdate
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
Uninstallable=no
CreateUninstallRegKey=no
OutputDir=..\dist
OutputBaseFilename=TestBox-CLI-Setup-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\testbox\assets\logo.ico
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\windows\cli\TestBox-CLI-Updater.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion
Source: "..\dist\{#UpdatePackage}"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion

[Run]
Filename: "{tmp}\TestBox-CLI-Updater.exe"; Parameters: "--package ""{tmp}\{#UpdatePackage}"" --install-dir ""{code:GetInstallDir}"""; StatusMsg: "正在应用 TestBox CLI 增量更新..."; Flags: runhidden waituntilterminated

[Code]
var
  TestBoxInstallDir: String;

function GetInstallDir(Param: String): String;
begin
  Result := TestBoxInstallDir;
end;

function InitializeSetup(): Boolean;
begin
  TestBoxInstallDir := ExpandConstant('{localappdata}\Programs\TestBox CLI');
  RegQueryStringValue(HKCU, 'Software\TestBox\CLI', 'InstallDir', TestBoxInstallDir);
  if not DirExists(TestBoxInstallDir) then begin
    MsgBox('未检测到 TestBox CLI 安装目录。请先运行 TestBox-CLI-Install-v{#AppVersion}.exe 进行完整安装。', mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;
