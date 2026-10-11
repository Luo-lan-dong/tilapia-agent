; TilapiaHub Windows 安装包脚本（ADR-021）
; 使用 Inno Setup 6 编译：
;   1. 先执行 pyinstaller launcher/tilapia-hub.spec --noconfirm
;   2. 再用 ISCC 编译本脚本：iscc installer/tilapia-hub.iss
; 产物：installer/Output/TilapiaHub-Setup-x64.exe

#define MyAppName "TilapiaHub 中枢调度台"
#define MyAppVersion "0.5.0"
#define MyAppExeName "tilapia-hub.exe"
#define DistRoot "dist\tilapia-hub"

[Setup]
AppId={{8E4F6B2A-9C1D-4E5A-B7F8-2A3D5C6E8F90}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=OPC Team
DefaultDirName={localappdata}\Programs\TilapiaHub
DefaultGroupName=TilapiaHub
PrivilegesRequired=lowest
OutputBaseFilename=TilapiaHub-Setup-x64
OutputDir=Output
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\assets\tilapia-icon.ico
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
; 主程序（PyInstaller onedir 全部内容）
Source: "..\{#DistRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; PostgreSQL 便携版（由 scripts/prepare_desktop_bundle.py 准备）
Source: "..\pgsql\*"; DestDir: "{app}\pgsql"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\TilapiaHub"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 TilapiaHub"; Filename: "{uninstallexe}"
Name: "{autodesktop}\TilapiaHub"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载时一并清除运行期数据（PG 数据目录、日志、连接信息）
Type: filesandordirs; Name: "{app}\data"
