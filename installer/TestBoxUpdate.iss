#define AppName "TestBox Incremental Update"
#ifndef AppVersion
#define AppVersion "1.0.1"
#endif
#define AppId "{0CB20564-C9B7-466E-881C-CA6BF1FA56B9}"
#define UpdatePackage "TestBox-update-v1.0.1.zip"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={tmp}\TestBoxUpdate
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
Uninstallable=no
CreateUninstallRegKey=no
OutputDir=..\dist
OutputBaseFilename=TestBox-Setup-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64

[Files]
Source: "..\dist\windows\TestBox-Updater.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion
Source: "..\dist\{#UpdatePackage}"; DestDir: "{tmp}"; Flags: deleteafterinstall ignoreversion

[Run]
Filename: "{tmp}\TestBox-Updater.exe"; Parameters: "--package ""{tmp}\{#UpdatePackage}"" --install-dir ""{code:GetInstallDir}"""; StatusMsg: "正在应用 TestBox 增量更新..."; Flags: runhidden waituntilterminated

[Code]
var
  TestBoxInstallDir: String;

function GetInstallDir(Param: String): String;
begin
  Result := TestBoxInstallDir;
end;

function InitializeSetup(): Boolean;
begin
  TestBoxInstallDir := ExpandConstant('{localappdata}\Programs\TestBox');
  RegQueryStringValue(HKCU, 'Software\TestBox', 'InstallDir', TestBoxInstallDir);
  if not DirExists(TestBoxInstallDir) then begin
    MsgBox('未检测到 TestBox 安装目录。请先运行 TestBox-Install-v{#AppVersion}.exe 进行完整安装。', mbError, MB_OK);
    Result := False;
    exit;
  end;
  Result := True;
end;
