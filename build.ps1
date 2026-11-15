# Baut dist\DeskRemote.exe
Set-Location $PSScriptRoot
.\.venv\Scripts\python scripts\make_icon.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# schlank: was nicht gebraucht wird, filtert DeskRemote.spec aus (Kommentar dort)
.\.venv\Scripts\python -m PyInstaller --noconfirm --clean --log-level WARN DeskRemote.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Get-Item dist\DeskRemote.exe | Select-Object Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}}
