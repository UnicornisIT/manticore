#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Manticore"
#define MyAppExeName "Manticore.exe"
#define MyAppIconName "Manticore-" + MyAppVersion + ".ico"

[Setup]
AppId={{815471B3-D4A7-49C8-9F25-BEACF00E37B8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputDir=..\dist\installer
OutputBaseFilename=Manticore-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=yes
AppMutex=ManticoreDesktopClient
UninstallDisplayIcon={app}\{#MyAppIconName}
SetupIconFile=manticore.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\Manticore.exe"; DestDir: "{app}"; Flags: ignoreversion restartreplace uninsrestartdelete
Source: "manticore.ico"; DestDir: "{app}"; DestName: "{#MyAppIconName}"; Flags: ignoreversion

[InstallDelete]
; Удаляем только известные устаревшие ресурсы программы. Данные пользователя
; находятся в отдельной папке %LOCALAPPDATA%\Manticore и сюда не попадают.
Type: files; Name: "{app}\Manticore-*.ico"
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIconName}"; IconIndex: 0
Name: "{autoprograms}\{#MyAppName} — настройка"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--configure"; IconFilename: "{app}\{#MyAppIconName}"; IconIndex: 0
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppIconName}"; IconIndex: 0; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные ярлыки:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall
