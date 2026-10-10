#define AppName "TestBox GUI Incremental Update"
#ifndef AppVersion
#define AppVersion "1.0.22"
#endif
#ifndef UpdatePackage
#define UpdatePackage "TestBox-GUI-update-v1.0.22.zip"
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
Source: "..\dist\windows\gui\TestBox-GUI-Updater.exe"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\dist\{#UpdatePackage}"; DestDir: "{tmp}"; Flags: dontcopy

[Code]
var
  TestBoxInstallDir: String;
  UpdateApplied: Boolean;

{ Resolve existing 8.3 names component-by-component; lexical expansion alone
  cannot detect a short-name alias of the protected user-data directory.
  Refuse junction/symlink ancestors rather than following them into user data. }
function CanonicalPath(Path: String): String;
var
  ParentPath, Name: String;
  FindRec: TFindRec;
begin
  Path := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  ParentPath := RemoveBackslashUnlessRoot(ExtractFileDir(Path));
  if ParentPath = Path then begin
    Result := Path;
    exit;
  end;
  Name := ExtractFileName(Path);
  { Win32 ignores trailing dots/spaces even when a directory does not exist. }
  while Length(Name) > 0 do begin
    if (Name[Length(Name)] <> '.') and (Name[Length(Name)] <> ' ') then break;
    Delete(Name, Length(Name), 1);
  end;
  if Name = '' then RaiseException('安装目录包含无效路径段。');
  Path := AddBackslash(ParentPath) + Name;
  if FindFirst(Path, FindRec) then begin
    try
      if (FindRec.Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
        RaiseException('安装目录或受保护目录不能经过目录链接：' + Path);
      Name := FindRec.Name;
    finally
      FindClose(FindRec);
    end;
  end;
  Result := AddBackslash(CanonicalPath(ParentPath)) + Name;
end;

function PathsOverlap(LeftPath, RightPath: String): Boolean;
begin
  LeftPath := AddBackslash(Lowercase(CanonicalPath(LeftPath)));
  RightPath := AddBackslash(Lowercase(CanonicalPath(RightPath)));
  Result := (Pos(LeftPath, RightPath) = 1) or (Pos(RightPath, LeftPath) = 1);
end;

function InstallationPathError(InstallDir: String): String;
var
  OtherDir, RegisteredOtherDir: String;
  I: Integer;
begin
  Result := '';
  try
    if Trim(InstallDir) = '' then
      RaiseException('TestBox 安装目录为空，请运行对应组件的完整安装器。');
    { Check Unicode characters directly. An ASCII first argument to Pos may
      select Pascal Script's ANSI branch and replace Chinese text with '?'. }
    for I := 1 to Length(InstallDir) do
      if (InstallDir[I] = '*') or (InstallDir[I] = '?') or
         (InstallDir[I] = '"') then
        RaiseException('TestBox 安装目录包含无效字符。');
    InstallDir := CanonicalPath(InstallDir);
    if RemoveBackslashUnlessRoot(ExtractFileDir(InstallDir)) = InstallDir then
      RaiseException('TestBox 不能安装到磁盘或共享根目录。');
    OtherDir := ExpandConstant('{localappdata}\Programs\TestBox CLI');
    if PathsOverlap(InstallDir, ExpandConstant('{localappdata}\TestBox')) or
       PathsOverlap(InstallDir, OtherDir) then
      RaiseException('安装目录不能与 TestBox 用户数据或另一组件的安装目录重叠。请选择独立目录。');
    { Always protect the other component's default root as well as its registry
      location. An empty/stale registry value must not disable the default guard. }
    RegisteredOtherDir := '';
    if RegQueryStringValue(HKCU, 'Software\TestBox\CLI', 'InstallDir', RegisteredOtherDir) then
      if Trim(RegisteredOtherDir) <> '' then
        if PathsOverlap(InstallDir, RegisteredOtherDir) then
          RaiseException('安装目录不能与另一组件的已登记安装目录重叠。请选择独立目录。');
  except
    Result := GetExceptionMessage;
  end;
end;

function InitializeSetup(): Boolean;
var
  RegisteredInstallDir, ErrorMessage: String;
begin
  TestBoxInstallDir := ExpandConstant('{localappdata}\Programs\TestBox GUI');
  RegisteredInstallDir := '';
  if RegQueryStringValue(HKCU, 'Software\TestBox\GUI', 'InstallDir', RegisteredInstallDir) then
    TestBoxInstallDir := RegisteredInstallDir;
  ErrorMessage := InstallationPathError(TestBoxInstallDir);
  if ErrorMessage <> '' then begin
    Log(ErrorMessage);
    SuppressibleMsgBox(ErrorMessage, mbError, MB_OK, IDOK);
    Result := False;
    exit;
  end;
  { A trailing backslash immediately before a quote breaks Windows argv parsing. }
  TestBoxInstallDir := RemoveBackslashUnlessRoot(ExpandFileName(TestBoxInstallDir));
  if not FileExists(AddBackslash(TestBoxInstallDir) + 'update-manifest.json') then begin
    ErrorMessage := '未检测到 TestBox GUI 安装目录。请先运行 TestBox-GUI-Install-v{#AppVersion}.exe 进行完整安装。';
    Log(ErrorMessage);
    SuppressibleMsgBox(ErrorMessage, mbError, MB_OK, IDOK);
    Result := False;
    exit;
  end;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  UpdaterPath, PackagePath, DiagnosticPath, Parameters: String;
  Diagnostic: AnsiString;
begin
  Result := '';
  if UpdateApplied then exit;
  { Recheck before touching the installation, not only when the wizard starts. }
  Result := InstallationPathError(TestBoxInstallDir);
  if Result <> '' then begin
    Log(Result);
    exit;
  end;
  try
    ExtractTemporaryFile('TestBox-GUI-Updater.exe');
    ExtractTemporaryFile('{#UpdatePackage}');
    UpdaterPath := ExpandConstant('{tmp}\TestBox-GUI-Updater.exe');
    PackagePath := ExpandConstant('{tmp}\{#UpdatePackage}');
    DiagnosticPath := ExpandConstant('{tmp}\testbox-update-result.json');
    if FileExists(DiagnosticPath) then
      if not DeleteFile(DiagnosticPath) then
        RaiseException('无法清除旧更新诊断，已停止更新。');
    Parameters := '--package "' + PackagePath + '" --install-dir "' +
      TestBoxInstallDir + '" --result-file "' + DiagnosticPath + '"';
    if not Exec(UpdaterPath, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := '无法启动 TestBox GUI 更新器：' + SysErrorMessage(ResultCode)
    else if ResultCode <> 0 then begin
      Result := 'TestBox GUI 更新失败（退出码 ' + IntToStr(ResultCode) + '）。';
      if LoadStringFromFile(DiagnosticPath, Diagnostic) then begin
        if Diagnostic <> '' then
          Result := Result + #13#10 + UTF8Decode(Diagnostic)
        else
          Result := Result + #13#10 + '更新器生成了空诊断。请使用 /LOG 保存安装日志。';
      end
      else
        Result := Result + #13#10 + '无法读取更新诊断。请使用 /LOG 保存安装日志。';
    end
    else
      UpdateApplied := True;
  except
    Result := '无法完成 TestBox GUI 更新：' + GetExceptionMessage;
  end;
  { A nonempty result blocks installation (including silent setup). Do not mark
    success from a postinstall [Run] entry or swallow an updater failure. }
  if Result <> '' then Log(Result);
end;
