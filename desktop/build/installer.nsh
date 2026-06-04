!include LogicLib.nsh

!ifdef BUILD_UNINSTALLER
Var /GLOBAL UninstallShouldDeleteAppData
!endif

!macro customUnInit
  StrCpy $UninstallShouldDeleteAppData "0"
  ${GetParameters} $R0
  ${GetOptions} $R0 "--delete-app-data" $R1
  ${Unless} ${Errors}
    StrCpy $UninstallShouldDeleteAppData "1"
  ${EndUnless}
!macroend

!macro customUnInstall
  ${If} $UninstallShouldDeleteAppData == "1"
    SectionSetFlags ${SEC_DELETE_APP_DATA} 1
  ${EndIf}
!macroend

!macro customUnInstallSection
Section /o "删除本地数据（数据库、材料、缓存和本地配置）" SEC_DELETE_APP_DATA
  Call un.DeleteAutoEmailSenderAppData
SectionEnd
!macroend

!ifdef BUILD_UNINSTALLER
Function un.DeleteAutoEmailSenderAppData
  ${IfNot} ${Silent}
    MessageBox MB_ICONEXCLAMATION|MB_YESNO|MB_DEFBUTTON2 "这将永久删除 Auto Email Sender 的本地数据，包括数据库、上传材料、缓存和本地配置。删除后无法通过重新安装恢复。是否继续？" IDYES delete_data IDNO skip_delete
  ${EndIf}

  delete_data:
    SetShellVarContext current
    StrCpy $R0 "$APPDATA\Auto Email Sender"

    ${If} $R0 == ""
      Return
    ${EndIf}

    ${If} $R0 == "$APPDATA"
      Return
    ${EndIf}

    ${If} ${FileExists} "$R0\*.*"
      RMDir /r "$R0"
    ${EndIf}
    Return

  skip_delete:
    Return
FunctionEnd
!endif
