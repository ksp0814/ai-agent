Option Explicit

Dim shell, fso, root, pythonw, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
  MsgBox "가상환경이 없습니다. 먼저 .venv에 desktop 패키지를 설치하세요.", 16, "Personal Agent"
  WScript.Quit 1
End If

command = Chr(34) & pythonw & Chr(34) & _
          " -c " & Chr(34) & "from personal_agent.desktop import main; main()" & Chr(34) & _
          " --workspace " & Chr(34) & root & Chr(34)
shell.Run command, 0, False
