# Creates a desktop shortcut (with icon) that runs launch.vbs from wherever this folder lives.
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$desktop\Uma Fan Counter.lnk")
$lnk.TargetPath = "$env:WINDIR\System32\wscript.exe"
$lnk.Arguments = "`"$dir\launch.vbs`""
$lnk.WorkingDirectory = $dir
$lnk.IconLocation = "$dir\icon.ico"
$lnk.Save()
Write-Host "Shortcut created on your Desktop."
