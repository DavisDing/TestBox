#define AppName "TestBox CLI"
#ifndef AppVersion
#define AppVersion "1.0.21"
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
; Discard legacy recursive {app} deletion records from the same AppId.
; Untracked old-version files are deliberately retained, never cleaned up.
UninstallLogMode=overwrite

[Registry]
Root: HKCU; Subkey: "Software\TestBox\CLI"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletevalue

[Files]
Source: "..\dist\windows\cli\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\TestBox CLI 增量更新"; Filename: "{app}\TestBox-CLI-Updater.exe"

; Do not recursively remove {app}: it may contain user-created files.
; Inno removes only files recorded in its uninstall log.

[Code]
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
    OtherDir := ExpandConstant('{localappdata}\Programs\TestBox GUI');
    if PathsOverlap(InstallDir, ExpandConstant('{localappdata}\TestBox')) or
       PathsOverlap(InstallDir, OtherDir) then
      RaiseException('安装目录不能与 TestBox 用户数据或另一组件的安装目录重叠。请选择独立目录。');
    { Always protect the other component's default root as well as its registry
      location. An empty/stale registry value must not disable the default guard. }
    RegisteredOtherDir := '';
    if RegQueryStringValue(HKCU, 'Software\TestBox\GUI', 'InstallDir', RegisteredOtherDir) then
      if Trim(RegisteredOtherDir) <> '' then
        if PathsOverlap(InstallDir, RegisteredOtherDir) then
          RaiseException('安装目录不能与另一组件的已登记安装目录重叠。请选择独立目录。');
  except
    Result := GetExceptionMessage;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := InstallationPathError(WizardDirValue);
end;

function InitializeUninstall(): Boolean;
var
  ErrorMessage: String;
begin
  ErrorMessage := InstallationPathError(ExpandConstant('{app}'));
  Result := ErrorMessage = '';
  if not Result then begin
    Log(ErrorMessage);
    SuppressibleMsgBox(ErrorMessage, mbError, MB_OK, IDOK);
  end;
end;
