; SMBT per-user installer (no admin rights). Installs to %LOCALAPPDATA%\Programs\SMBT.
; Operator settings (provider keys) live in %LOCALAPPDATA%\SMBT\settings.env and survive reinstall/uninstall. No market data or log file is stored.
Unicode true
SetCompressor /SOLID lzma
Name "SMBT Market Research"
OutFile "${OUT}"
Icon "${ICON}"
UninstallIcon "${ICON}"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\SMBT"
VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "SMBT Market Research"
VIAddVersionKey "FileDescription" "SMBT installer"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "ProductVersion" "${VERSION}"

!include "MUI2.nsh"
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_FINISHPAGE_RUN "$INSTDIR\python\python.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS "-m app.desktop"
!define MUI_FINISHPAGE_RUN_TEXT "Start SMBT now"
!define MUI_FINISHPAGE_TEXT "SMBT opens in your browser at http://127.0.0.1:8765. A console window runs the local server; close it to stop SMBT.$\r$\n$\r$\nTo install it as an app window: Chrome/Edge address bar install icon."
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  ; clean previous program files on upgrade (user data is elsewhere)
  RMDir /r "$INSTDIR\python"
  RMDir /r "$INSTDIR\backend"
  RMDir /r "$INSTDIR\frontend"
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*"
  File "/oname=smbt.ico" "${ICON}"
  ; shortcuts start in $INSTDIR\backend so "python -m app.desktop" finds the app package
  SetOutPath "$INSTDIR\backend"
  CreateDirectory "$SMPROGRAMS\SMBT"
  CreateShortcut "$SMPROGRAMS\SMBT\SMBT.lnk" "$INSTDIR\python\python.exe" "-m app.desktop" "$INSTDIR\smbt.ico"
  CreateShortcut "$SMPROGRAMS\SMBT\SMBT settings (API keys).lnk" "notepad.exe" "$LOCALAPPDATA\SMBT\settings.env"
  CreateShortcut "$SMPROGRAMS\SMBT\Uninstall SMBT.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\SMBT.lnk" "$INSTDIR\python\python.exe" "-m app.desktop" "$INSTDIR\smbt.ico"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SMBT" "DisplayName" "SMBT Market Research"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SMBT" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SMBT" "DisplayIcon" "$INSTDIR\smbt.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SMBT" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SMBT" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SMBT" "NoRepair" 1
SectionEnd

Section "Uninstall"
  Delete "$DESKTOP\SMBT.lnk"
  RMDir /r "$SMPROGRAMS\SMBT"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\SMBT"
SectionEnd
