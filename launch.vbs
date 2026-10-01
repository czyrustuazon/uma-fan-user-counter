' Starts the app from source with no console window. Works from any folder or machine.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)

' Try the Python launcher first, then pythonw on PATH.
On Error Resume Next
sh.Run "pyw app.py", 0, False
If Err.Number <> 0 Then
    Err.Clear
    sh.Run "pythonw app.py", 0, False
    If Err.Number <> 0 Then
        MsgBox "Python was not found. Install Python 3 and tick ""Add to PATH"".", 16, "Uma Fan Counter"
    End If
End If
